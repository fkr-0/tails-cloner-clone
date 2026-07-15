#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
LANE_DIR = Path(__file__).resolve().parent
BOOT_SCRIPT = LANE_DIR / 'boot_tails_qemu.sh'
CACHE_DIRS = [
    REPO_ROOT / '.cache/vagrant-lab/tails-images',
    Path('/workspace/tails-cloner/.cache/vagrant-lab/tails-images'),
    Path('/opt/tails-cloner-fixtures/tails-images'),
]
VERSION_RE = re.compile(r'tails-amd64-(?P<version>[^/]+)\.img$')


def cached_images() -> list[Path]:
    images: list[Path] = []
    for cache_dir in CACHE_DIRS:
        if cache_dir.exists():
            images.extend(sorted(cache_dir.glob('tails-amd64-*.img')))
    return sorted(set(images))


def image_version(path: Path) -> str:
    match = VERSION_RE.search(path.name)
    return match.group('version') if match else path.name


def choose_image(version: str | None) -> Path:
    images = cached_images()
    if not images:
        raise SystemExit('no cached Tails images found for QMP probe')
    if version:
        matches = [image for image in images if image_version(image) == version]
        if not matches:
            available = ', '.join(image_version(image) for image in images)
            raise SystemExit(f'requested version {version!r} not cached; available: {available}')
        return matches[-1]
    return images[-1]


class QmpClient:
    def __init__(self, path: Path, timeout: float) -> None:
        self.path = path
        self.timeout = timeout
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(timeout)
        self.file = None

    def connect(self) -> None:
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self.sock.connect(str(self.path))
                self.file = self.sock.makefile('rwb', buffering=0)
                self._read_message()
                self.command('qmp_capabilities')
                return
            except (TimeoutError, FileNotFoundError, ConnectionRefusedError):
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.1)

    def _read_message(self) -> dict[str, Any]:
        assert self.file is not None
        line = self.file.readline()
        if not line:
            raise RuntimeError('QMP socket closed while waiting for message')
        return json.loads(line.decode('utf-8'))

    def command(self, execute: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        assert self.file is not None
        payload: dict[str, Any] = {'execute': execute}
        if arguments is not None:
            payload['arguments'] = arguments
        self.file.write(json.dumps(payload).encode('utf-8') + b'\n')
        while True:
            message = self._read_message()
            if 'return' in message or 'error' in message:
                return message

    def close(self) -> None:
        try:
            if self.file is not None:
                self.file.close()
        finally:
            self.sock.close()


def probe_qmp(image: Path, timeout: int, memory_mb: int, cpus: int) -> dict[str, Any]:
    env = os.environ.copy()
    env.setdefault('TAILS_QEMU_MEMORY_MB', str(memory_mb))
    env.setdefault('TAILS_QEMU_CPUS', str(cpus))
    with tempfile.TemporaryDirectory(prefix='tails-qmp-') as tmpdir:
        tmp = Path(tmpdir)
        qmp_socket = tmp / 'qmp.sock'
        pidfile = tmp / 'qemu.pid'
        cmd = [
            'bash',
            str(BOOT_SCRIPT),
            '--headless',
            '--timeout',
            str(timeout),
            '--no-network',
            '--qmp',
            str(qmp_socket),
            '--pidfile',
            str(pidfile),
            str(image),
        ]
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        client = QmpClient(qmp_socket, timeout=min(timeout, 20))
        qmp_status: dict[str, Any] | None = None
        qmp_block: dict[str, Any] | None = None
        qmp_kvm: dict[str, Any] | None = None
        qmp_quit: dict[str, Any] | None = None
        qmp_connected = False
        try:
            client.connect()
            qmp_connected = True
            qmp_status = client.command('query-status')
            qmp_block = client.command('query-block')
            qmp_kvm = client.command('query-kvm')
            qmp_quit = client.command('quit')
        finally:
            client.close()
            try:
                stdout, stderr = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate(timeout=10)
        pid_text = pidfile.read_text(encoding='utf-8').strip() if pidfile.exists() else ''
        success = qmp_connected and qmp_status is not None and qmp_block is not None
        return {
            'image': str(image),
            'image_name': image.name,
            'version': image_version(image),
            'timeout_seconds': timeout,
            'memory_mb': memory_mb,
            'cpus': cpus,
            'command': cmd,
            'pidfile': str(pidfile),
            'pid': pid_text,
            'returncode': process.returncode,
            'qmp_connected': qmp_connected,
            'qmp_status': qmp_status,
            'qmp_block': qmp_block,
            'qmp_kvm': qmp_kvm,
            'qmp_quit': qmp_quit,
            'stdout_tail': stdout[-2000:],
            'stderr_tail': stderr[-4000:],
            'success': success,
            'success_meaning': 'QEMU accepted launch arguments, exposed QMP, reported status/block devices, and accepted QMP quit',
        }


def main() -> int:
    parser = argparse.ArgumentParser(description='QMP probe for the Tails real-boot QEMU lane.')
    parser.add_argument('--version', help='Cached tails-amd64 version to probe, e.g. 7.7.2')
    parser.add_argument('--timeout', type=int, default=45, help='Probe timeout in seconds. Default: 45')
    parser.add_argument('--memory-mb', type=int, default=2048, help='Memory for probe. Default: 2048')
    parser.add_argument('--cpus', type=int, default=1, help='vCPUs for probe. Default: 1')
    parser.add_argument('--json', action='store_true', help='Print JSON result')
    args = parser.parse_args()
    image = choose_image(args.version)
    result = probe_qmp(image=image, timeout=args.timeout, memory_mb=args.memory_mb, cpus=args.cpus)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        outcome = 'passed' if result['success'] else 'failed'
        print(f"real-boot QMP probe {outcome}: {result['image_name']} rc={result['returncode']}")
    return 0 if result['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
