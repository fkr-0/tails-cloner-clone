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
VALIDATOR = LANE_DIR / 'validate_guest_probe_output.py'
PREPARE_SHARE = LANE_DIR / 'prepare_guest_probe_share.py'
PROBE_PREFIX = 'TAILS_CLONER_GUEST_PROBE='
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
        raise SystemExit('no cached Tails images found for guest-probe capture')
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


def read_probe_marker(serial_log: Path) -> str | None:
    if not serial_log.exists():
        return None
    try:
        lines = serial_log.read_text(encoding='utf-8', errors='replace').splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        if line.startswith(PROBE_PREFIX):
            return line
    return None


def wait_for_probe_marker(serial_log: Path, timeout: int) -> str | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        marker = read_probe_marker(serial_log)
        if marker is not None:
            return marker
        time.sleep(0.5)
    return read_probe_marker(serial_log)


def validate_serial_log(serial_log: Path) -> dict[str, Any]:
    result = subprocess.run(
        ['python3', str(VALIDATOR), '--log-file', str(serial_log)],
        check=False,
        text=True,
        capture_output=True,
    )
    try:
        payload = json.loads(result.stdout or '{}')
    except json.JSONDecodeError:
        payload = {'success': False, 'scenario_variant': None, 'failures': ['validator produced invalid JSON']}
    payload['validator_returncode'] = result.returncode
    payload['validator_stderr'] = result.stderr
    return payload


def prepare_guest_probe_share(output_dir: Path, scenario_variant: str, share_tag: str) -> Path:
    subprocess.run(
        [
            'python3',
            str(PREPARE_SHARE),
            '--output-dir',
            str(output_dir),
            '--scenario-variant',
            scenario_variant,
            '--tag',
            share_tag,
        ],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    return output_dir


def guest_command_hint(share_tag: str = 'tailscloner', mount_point: str = '/mnt/tailscloner') -> str:
    return (
        f'sudo mkdir -p {mount_point} && '
        f'sudo mount -t 9p -o trans=virtio,version=9p2000.L,ro {share_tag} {mount_point} && '
        f'{mount_point}/run_guest_probe.sh'
    )


def build_boot_command(
    *,
    image: Path,
    qmp_socket: Path,
    pidfile: Path,
    serial_log: Path,
    share_dir: Path,
    share_tag: str,
    timeout: int,
    memory_mb: int,
    cpus: int,
    headless: bool,
    extra_drives: list[Path],
) -> tuple[list[str], dict[str, str]]:
    env = os.environ.copy()
    env.setdefault('TAILS_QEMU_MEMORY_MB', str(memory_mb))
    env.setdefault('TAILS_QEMU_CPUS', str(cpus))
    command = [
        'bash',
        str(BOOT_SCRIPT),
    ]
    if headless:
        command.append('--headless')
    command.extend([
        '--timeout',
        str(timeout + 30),
        '--no-network',
        '--qmp',
        str(qmp_socket),
        '--pidfile',
        str(pidfile),
        '--serial-log',
        str(serial_log),
        '--share-dir',
        f'{share_dir},{share_tag}',
    ])
    for drive in extra_drives:
        command.extend(['--extra-drive', str(drive)])
    command.append(str(image))
    return command, env


def quit_qemu(qmp_socket: Path, timeout: int = 10) -> dict[str, Any] | None:
    client = QmpClient(qmp_socket, timeout=timeout)
    try:
        client.connect()
        return client.command('quit')
    except Exception as error:  # noqa: BLE001 - best-effort cleanup path
        return {'error': str(error)}
    finally:
        client.close()


def capture_probe(
    *,
    image: Path,
    scenario_variant: str,
    wait_timeout: int,
    memory_mb: int,
    cpus: int,
    share_dir: Path,
    share_tag: str,
    serial_log: Path | None,
    dry_run: bool,
    headless: bool,
    prepare_share: bool,
    extra_drives: list[Path],
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix='tails-guest-probe-capture-') as tmpdir:
        tmp = Path(tmpdir)
        if prepare_share:
            share_dir = prepare_guest_probe_share(tmp / 'guest-probe-share', scenario_variant, share_tag)
        qmp_socket = tmp / 'qmp.sock'
        pidfile = tmp / 'qemu.pid'
        actual_serial_log = serial_log or (tmp / 'serial.log')
        actual_serial_log.parent.mkdir(parents=True, exist_ok=True)
        command, env = build_boot_command(
            image=image,
            qmp_socket=qmp_socket,
            pidfile=pidfile,
            serial_log=actual_serial_log,
            share_dir=share_dir,
            share_tag=share_tag,
            timeout=wait_timeout,
            memory_mb=memory_mb,
            cpus=cpus,
            headless=headless,
            extra_drives=extra_drives,
        )
        base_result: dict[str, Any] = {
            'image': str(image),
            'image_name': image.name,
            'version': image_version(image),
            'scenario_variant': scenario_variant,
            'wait_timeout_seconds': wait_timeout,
            'serial_log': str(actual_serial_log),
            'share_dir': str(share_dir),
            'share_tag': share_tag,
            'headless': headless,
            'prepare_share': prepare_share,
            'guest_command': guest_command_hint(share_tag),
            'command': command,
        }
        if dry_run:
            return {**base_result, 'success': True, 'dry_run': True, 'result': 'command constructed but not executed'}

        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        qmp_status: dict[str, Any] | None = None
        qmp_quit: dict[str, Any] | None = None
        marker: str | None = None
        validator_result: dict[str, Any] | None = None
        try:
            client = QmpClient(qmp_socket, timeout=min(wait_timeout, 20))
            try:
                client.connect()
                qmp_status = client.command('query-status')
            finally:
                client.close()
            marker = wait_for_probe_marker(actual_serial_log, wait_timeout)
            if marker is not None:
                validator_result = validate_serial_log(actual_serial_log)
            qmp_quit = quit_qemu(qmp_socket)
        finally:
            try:
                stdout, stderr = process.communicate(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate(timeout=15)
        success = bool(marker and validator_result and validator_result.get('success'))
        return {
            **base_result,
            'success': success,
            'dry_run': False,
            'qmp_status': qmp_status,
            'qmp_quit': qmp_quit,
            'marker_found': marker is not None,
            'marker_tail': marker,
            'validator_result': validator_result,
            'returncode': process.returncode,
            'stdout_tail': stdout[-2000:],
            'stderr_tail': stderr[-4000:],
        }


def main() -> int:
    parser = argparse.ArgumentParser(description='Boot QEMU and capture/validate a Tails guest probe serial marker.')
    parser.add_argument('--version', help='Cached tails-amd64 version to boot, e.g. 7.7.2')
    parser.add_argument('--scenario-variant', default='running-live-install')
    parser.add_argument('--wait-timeout', type=int, default=300)
    parser.add_argument('--memory-mb', type=int, default=2048)
    parser.add_argument('--cpus', type=int, default=1)
    parser.add_argument('--share-dir', type=Path, default=REPO_ROOT)
    parser.add_argument('--share-tag', default='tailscloner')
    parser.add_argument('--serial-log', type=Path)
    parser.add_argument('--extra-drive', dest='extra_drives', action='append', type=Path, default=[])
    parser.add_argument('--interactive-display', action='store_true', help='Use the default graphical display instead of headless serial-only QEMU.')
    parser.add_argument('--prepare-share', action='store_true', help='Build a guest-probe share automatically and expose that instead of --share-dir.')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    image = choose_image(args.version)
    result = capture_probe(
        image=image,
        scenario_variant=args.scenario_variant,
        wait_timeout=args.wait_timeout,
        memory_mb=args.memory_mb,
        cpus=args.cpus,
        share_dir=args.share_dir,
        share_tag=args.share_tag,
        serial_log=args.serial_log,
        dry_run=args.dry_run,
        headless=not args.interactive_display,
        prepare_share=args.prepare_share,
        extra_drives=args.extra_drives,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
