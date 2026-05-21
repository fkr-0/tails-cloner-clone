#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

LANE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LANE_DIR))

from capture_guest_probe_from_qemu import QmpClient, build_boot_command, quit_qemu  # noqa: E402

DEFAULT_IMAGE = LANE_DIR / 'out' / 'fsuuid' / 'tails-amd64-7.7.2-boot-8g.img'
DEFAULT_SHARE_DIR = LANE_DIR / 'out' / 'appimage-guest-smoke-share'
DEFAULT_SHARE_TAG = 'tailsclonerappimage'
DEFAULT_OUT_DIR = LANE_DIR / 'out' / 'boot-screen-probe'


def insert_boot_usb(command: list[str]) -> list[str]:
    if '--boot-usb' not in command:
        command.insert(command.index('--timeout'), '--boot-usb')
    return command


def qmp_screendump(qmp_socket: Path, destination: Path, timeout: int = 20) -> dict[str, Any]:
    client = QmpClient(qmp_socket, timeout=timeout)
    try:
        client.connect()
        return client.command('screendump', {'filename': str(destination)})
    finally:
        client.close()


def qmp_status(qmp_socket: Path, timeout: int = 20) -> dict[str, Any]:
    client = QmpClient(qmp_socket, timeout=timeout)
    try:
        client.connect()
        return client.command('query-status')
    finally:
        client.close()


def run_probe(
    *,
    image: Path,
    out_dir: Path,
    waits: list[int],
    memory_mb: int,
    cpus: int,
    headless: bool,
    share_dir: Path,
    share_tag: str,
    timeout: int,
    dry_run: bool,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    serial_log = out_dir / 'serial.log'
    with tempfile.TemporaryDirectory(prefix='tails-boot-screen-probe-') as tmpdir:
        tmp = Path(tmpdir)
        qmp_socket = tmp / 'qmp.sock'
        pidfile = tmp / 'qemu.pid'
        command, env = build_boot_command(
            image=image,
            qmp_socket=qmp_socket,
            pidfile=pidfile,
            serial_log=serial_log,
            share_dir=share_dir,
            share_tag=share_tag,
            timeout=timeout,
            memory_mb=memory_mb,
            cpus=cpus,
            headless=headless,
            extra_drives=[],
        )
        command = insert_boot_usb(command)
        base: dict[str, Any] = {
            'image': str(image),
            'out_dir': str(out_dir),
            'serial_log': str(serial_log),
            'share_dir': str(share_dir),
            'share_tag': share_tag,
            'waits': waits,
            'headless': headless,
            'command': command,
        }
        if dry_run:
            return {**base, 'success': True, 'dry_run': True}

        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        captures: list[dict[str, Any]] = []
        started = time.monotonic()
        try:
            # Wait for QMP to come up and capture initial status.
            status = qmp_status(qmp_socket, timeout=30)
            for wait in waits:
                target = started + wait
                remaining = target - time.monotonic()
                if remaining > 0:
                    time.sleep(remaining)
                screenshot = out_dir / f'screen-{wait:04d}s.ppm'
                result = qmp_screendump(qmp_socket, screenshot, timeout=20)
                captures.append({
                    'wait_seconds': wait,
                    'screenshot': str(screenshot),
                    'exists': screenshot.exists(),
                    'size': screenshot.stat().st_size if screenshot.exists() else 0,
                    'qmp_result': result,
                })
            quit_result = quit_qemu(qmp_socket)
        finally:
            try:
                stdout, stderr = process.communicate(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate(timeout=20)
        serial_tail = serial_log.read_text(errors='replace')[-8000:] if serial_log.exists() else ''
        success = any(item['exists'] and item['size'] > 0 for item in captures)
        evidence = {
            **base,
            'success': success,
            'dry_run': False,
            'qmp_status': status,
            'qmp_quit': quit_result,
            'captures': captures,
            'returncode': process.returncode,
            'stdout_tail': stdout[-4000:],
            'stderr_tail': stderr[-4000:],
            'serial_tail': serial_tail,
        }
        (out_dir / 'boot-screen-probe-evidence.json').write_text(json.dumps(evidence, indent=2, sort_keys=True), encoding='utf-8')
        return evidence


def parse_waits(value: str) -> list[int]:
    waits = [int(part) for part in value.split(',') if part.strip()]
    if not waits or any(wait <= 0 for wait in waits):
        raise argparse.ArgumentTypeError('waits must be comma-separated positive seconds')
    return sorted(waits)


def main() -> int:
    parser = argparse.ArgumentParser(description='Boot Tails briefly and take QMP screendumps to diagnose normal boot progress.')
    parser.add_argument('--image', type=Path, default=DEFAULT_IMAGE)
    parser.add_argument('--out-dir', type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument('--waits', type=parse_waits, default=parse_waits('30,90,180'))
    parser.add_argument('--memory-mb', type=int, default=2048)
    parser.add_argument('--cpus', type=int, default=1)
    parser.add_argument('--interactive-display', action='store_true')
    parser.add_argument('--share-dir', type=Path, default=DEFAULT_SHARE_DIR)
    parser.add_argument('--share-tag', default=DEFAULT_SHARE_TAG)
    parser.add_argument('--timeout', type=int, default=240)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    if not args.image.exists():
        raise SystemExit(f'image not found: {args.image}')
    if not args.share_dir.exists():
        raise SystemExit(f'share dir not found: {args.share_dir}')
    result = run_probe(
        image=args.image,
        out_dir=args.out_dir,
        waits=args.waits,
        memory_mb=args.memory_mb,
        cpus=args.cpus,
        headless=not args.interactive_display,
        share_dir=args.share_dir,
        share_tag=args.share_tag,
        timeout=args.timeout,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
