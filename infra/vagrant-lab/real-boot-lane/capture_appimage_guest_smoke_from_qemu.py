#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

LANE_DIR = Path(__file__).resolve().parent
REPO_ROOT = LANE_DIR.parents[2]
sys.path.insert(0, str(LANE_DIR))

from capture_guest_probe_from_qemu import (  # noqa: E402
    QmpClient,
    build_boot_command,
    choose_image,
    image_version,
    quit_qemu,
)

PREPARE_SHARE = LANE_DIR / 'prepare_appimage_guest_smoke_share.py'
VALIDATOR = LANE_DIR / 'validate_appimage_guest_smoke_output.py'
DEFAULT_SHARE_DIR = LANE_DIR / 'out' / 'appimage-guest-smoke-share'
DEFAULT_SERIAL_LOG = LANE_DIR / 'out' / 'serial-logs' / 'appimage-guest-smoke.log'
MARKER = 'TAILS_CLONER_APPIMAGE_SMOKE='
DEFAULT_SHARE_TAG = 'tailsclonerappimage'
DEFAULT_MOUNT_POINT = '/mnt/tailscloner-appimage'
DEFAULT_LOCK_FILE = LANE_DIR / 'out' / 'appimage-guest-smoke.lock'



class CaptureLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.acquired = False

    def __enter__(self) -> 'CaptureLock':
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            existing = self.path.read_text(errors='replace') if self.path.exists() else ''
            raise RuntimeError(f'AppImage Tails capture already running or stale lock exists: {self.path} {existing!r}') from exc
        with os.fdopen(fd, 'w') as handle:
            handle.write(f'pid={os.getpid()}\n')
        self.acquired = True
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        if self.acquired:
            with contextlib.suppress(FileNotFoundError):
                self.path.unlink()


def read_marker(serial_log: Path) -> str | None:
    if not serial_log.exists():
        return None
    for line in reversed(serial_log.read_text(errors='replace').splitlines()):
        if MARKER in line:
            return line
    return None


def wait_for_marker(serial_log: Path, timeout: int) -> str | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        marker = read_marker(serial_log)
        if marker:
            return marker
        time.sleep(0.5)
    return read_marker(serial_log)


def validate_serial_log(serial_log: Path) -> dict[str, Any]:
    result = subprocess.run(
        ['python3', str(VALIDATOR), '--log-file', str(serial_log), '--json'],
        check=False,
        text=True,
        capture_output=True,
    )
    try:
        payload = json.loads(result.stdout or '{}')
    except json.JSONDecodeError:
        payload = {'valid': False, 'errors': ['validator produced invalid JSON'], 'payload': {}}
    payload['validator_returncode'] = result.returncode
    payload['validator_stderr'] = result.stderr
    return payload


def prepare_share(output_dir: Path, *, appimage: Path | None, share_tag: str, mount_point: str) -> Path:
    command = ['python3', str(PREPARE_SHARE), '--output-dir', str(output_dir), '--tag', share_tag, '--mount-point', mount_point]
    if appimage is not None:
        command.extend(['--appimage', str(appimage)])
    subprocess.run(command, check=True, text=True, stdout=subprocess.PIPE)
    return output_dir


def guest_command_hint(share_tag: str, mount_point: str) -> str:
    return (
        f'sudo mkdir -p {mount_point} && '
        f'sudo mount -t 9p -o trans=virtio,version=9p2000.L,ro {share_tag} {mount_point} && '
        f'{mount_point}/run_appimage_guest_smoke.sh'
    )


def capture_appimage_smoke(
    *,
    image: Path,
    wait_timeout: int,
    memory_mb: int,
    cpus: int,
    share_dir: Path,
    share_tag: str,
    mount_point: str,
    serial_log: Path,
    dry_run: bool,
    headless: bool,
    appimage: Path | None,
    prepare: bool,
    extra_drives: list[Path],
    lock_file: Path = DEFAULT_LOCK_FILE,
) -> dict[str, Any]:
    if prepare:
        prepare_share(share_dir, appimage=appimage, share_tag=share_tag, mount_point=mount_point)
    lock_context = CaptureLock(lock_file) if not dry_run else None
    if lock_context is not None:
        lock_context.__enter__()
    try:
        return _capture_appimage_smoke_unlocked(
            image=image,
            wait_timeout=wait_timeout,
            memory_mb=memory_mb,
            cpus=cpus,
            share_dir=share_dir,
            share_tag=share_tag,
            mount_point=mount_point,
            serial_log=serial_log,
            dry_run=dry_run,
            headless=headless,
            extra_drives=extra_drives,
            prepared_share=prepare,
        )
    finally:
        if lock_context is not None:
            lock_context.__exit__(None, None, None)


def _capture_appimage_smoke_unlocked(
    *,
    image: Path,
    wait_timeout: int,
    memory_mb: int,
    cpus: int,
    share_dir: Path,
    share_tag: str,
    mount_point: str,
    serial_log: Path,
    dry_run: bool,
    headless: bool,
    extra_drives: list[Path],
    prepared_share: bool,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix='tails-appimage-smoke-capture-') as tmpdir:
        tmp = Path(tmpdir)
        qmp_socket = tmp / 'qmp.sock'
        pidfile = tmp / 'qemu.pid'
        serial_log.parent.mkdir(parents=True, exist_ok=True)
        command, env = build_boot_command(
            image=image,
            qmp_socket=qmp_socket,
            pidfile=pidfile,
            serial_log=serial_log,
            share_dir=share_dir,
            share_tag=share_tag,
            timeout=wait_timeout,
            memory_mb=memory_mb,
            cpus=cpus,
            headless=headless,
            extra_drives=extra_drives,
        )
        command.insert(command.index('--timeout'), '--boot-usb')
        base: dict[str, Any] = {
            'image': str(image),
            'image_name': image.name,
            'version': image_version(image),
            'wait_timeout_seconds': wait_timeout,
            'serial_log': str(serial_log),
            'share_dir': str(share_dir),
            'share_tag': share_tag,
            'mount_point': mount_point,
            'headless': headless,
            'prepared_share': prepared_share,
            'guest_command': guest_command_hint(share_tag, mount_point),
            'command': command,
        }
        if dry_run:
            return {**base, 'success': True, 'dry_run': True, 'result': 'command constructed but not executed'}

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
            marker = wait_for_marker(serial_log, wait_timeout)
            if marker:
                validator_result = validate_serial_log(serial_log)
            qmp_quit = quit_qemu(qmp_socket)
        finally:
            try:
                stdout, stderr = process.communicate(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate(timeout=15)
        success = bool(marker and validator_result and validator_result.get('valid'))
        result_status = 'passed' if success else ('marker-invalid' if marker else 'pending-no-marker')
        return {
            **base,
            'success': success,
            'result_status': result_status,
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
    parser = argparse.ArgumentParser(description='Boot QEMU and capture/validate Tails AppImage guest smoke serial marker.')
    parser.add_argument('--version', help='Cached tails-amd64 version to boot, e.g. 7.7.2')
    parser.add_argument('--image', type=Path, help='Explicit Tails image path to boot; overrides --version.')
    parser.add_argument('--wait-timeout', type=int, default=300)
    parser.add_argument('--memory-mb', type=int, default=2048)
    parser.add_argument('--cpus', type=int, default=1)
    parser.add_argument('--share-dir', type=Path, default=DEFAULT_SHARE_DIR)
    parser.add_argument('--share-tag', default=DEFAULT_SHARE_TAG)
    parser.add_argument('--mount-point', default=DEFAULT_MOUNT_POINT)
    parser.add_argument('--serial-log', type=Path, default=DEFAULT_SERIAL_LOG)
    parser.add_argument('--lock-file', type=Path, default=DEFAULT_LOCK_FILE)
    parser.add_argument('--appimage', type=Path)
    parser.add_argument('--extra-drive', dest='extra_drives', action='append', type=Path, default=[])
    parser.add_argument('--interactive-display', action='store_true', help='Use GTK display instead of headless serial-only QEMU.')
    parser.add_argument('--no-prepare-share', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    image = args.image if args.image is not None else choose_image(args.version)
    if not image.exists():
        raise SystemExit(f'image not found: {image}')
    result = capture_appimage_smoke(
        image=image,
        wait_timeout=args.wait_timeout,
        memory_mb=args.memory_mb,
        cpus=args.cpus,
        share_dir=args.share_dir,
        share_tag=args.share_tag,
        mount_point=args.mount_point,
        serial_log=args.serial_log,
        dry_run=args.dry_run,
        headless=not args.interactive_display,
        appimage=args.appimage,
        prepare=not args.no_prepare_share,
        extra_drives=args.extra_drives,
        lock_file=args.lock_file,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
