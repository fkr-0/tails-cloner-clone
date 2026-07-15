#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

LANE_DIR = Path(__file__).resolve().parent
DEFAULT_SOURCE = LANE_DIR / 'out' / 'fsuuid' / 'tails-amd64-7.7.2-boot-8g.img'
DEFAULT_OUTPUT = LANE_DIR / 'out' / 'debug-boot' / 'tails-amd64-7.7.2-boot-8g-debug-serial.img'
PARTITION_OFFSET = 2048 * 512
DEBUG_ARGS = [
    'console=ttyS0,115200n8',
    'systemd.log_target=console',
    'loglevel=7',
]
REMOVE_ARGS = {'quiet', 'splash', 'noautologin'}


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, text=True, capture_output=True)


def patch_kernel_args(line: str, extra_kernel_args: list[str] | None = None) -> str:
    prefix = ''
    if line.startswith('\t'):
        prefix = '\t'
        stripped = line[1:]
    else:
        stripped = line
    if not (stripped.startswith('append ') or stripped.lstrip().startswith('linux /live/vmlinuz ')):
        return line
    parts = stripped.split()
    existing = []
    for part in parts:
        if part in REMOVE_ARGS:
            continue
        existing.append(part)
    for arg in [*DEBUG_ARGS, *(extra_kernel_args or [])]:
        if not any(existing_arg == arg or existing_arg.startswith(arg.split('=')[0] + '=') for existing_arg in existing):
            existing.append(arg)
    return prefix + ' '.join(existing)


def patch_text(text: str, extra_kernel_args: list[str] | None = None) -> str:
    return '\n'.join(
        patch_kernel_args(line, extra_kernel_args=extra_kernel_args) for line in text.splitlines()
    ) + ('\n' if text.endswith('\n') else '')


def mtype(image: Path, path: str) -> str:
    result = run(['mtype', '-i', f'{image}@@{PARTITION_OFFSET}', path])
    return result.stdout


def mcopy_to_image(image: Path, source: Path, destination: str) -> None:
    run(['mcopy', '-o', '-i', f'{image}@@{PARTITION_OFFSET}', str(source), destination])


def patch_image(source: Path, output: Path, *, extra_kernel_args: list[str] | None = None) -> Path:
    if not source.exists():
        raise FileNotFoundError(f'source image not found: {source}')
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, output)
    with tempfile.TemporaryDirectory(prefix='tails-debug-boot-config-') as tmpdir:
        tmp = Path(tmpdir)
        files = [
            ('::/syslinux/live.cfg', tmp / 'live.cfg'),
            ('::/syslinux/live64.cfg', tmp / 'live64.cfg'),
            ('::/EFI/debian/grub.cfg', tmp / 'grub.cfg'),
        ]
        for image_path, local_path in files:
            original = mtype(output, image_path)
            local_path.write_text(patch_text(original, extra_kernel_args=extra_kernel_args), encoding='utf-8')
            mcopy_to_image(output, local_path, image_path)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description='Create a Tails boot image copy with serial/debug kernel arguments.')
    parser.add_argument('--source', type=Path, default=DEFAULT_SOURCE)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        '--kernel-arg',
        action='append',
        default=[],
        help='Additional disposable lab-only kernel argument to append, for example rootpw=<test-password>.',
    )
    args = parser.parse_args()
    output = patch_image(args.source, args.output, extra_kernel_args=args.kernel_arg)
    print(output)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
