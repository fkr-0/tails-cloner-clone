#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
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
        raise SystemExit('no cached Tails images found for real-boot smoke launch')
    if version:
        matches = [image for image in images if image_version(image) == version]
        if not matches:
            available = ', '.join(image_version(image) for image in images)
            raise SystemExit(f'requested version {version!r} not cached; available: {available}')
        return matches[-1]
    return images[-1]


def run_smoke(image: Path, timeout: int, memory_mb: int, cpus: int) -> dict[str, Any]:
    env = os.environ.copy()
    env.setdefault('TAILS_QEMU_MEMORY_MB', str(memory_mb))
    env.setdefault('TAILS_QEMU_CPUS', str(cpus))
    cmd = [
        'bash',
        str(BOOT_SCRIPT),
        '--headless',
        '--timeout',
        str(timeout),
        '--no-network',
        str(image),
    ]
    completed = subprocess.run(
        cmd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        check=False,
    )
    # GNU timeout returns 124 when the VM stayed alive until the timeout. For
    # this launcher smoke, that is success: qemu accepted the image/arguments.
    success = completed.returncode in {0, 124}
    return {
        'image': str(image),
        'image_name': image.name,
        'version': image_version(image),
        'timeout_seconds': timeout,
        'memory_mb': memory_mb,
        'cpus': cpus,
        'returncode': completed.returncode,
        'success': success,
        'success_meaning': 'qemu launched and either exited cleanly or stayed alive until timeout',
        'stdout_tail': completed.stdout[-2000:],
        'stderr_tail': completed.stderr[-4000:],
        'command': cmd,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description='Bounded smoke launch for the Tails real-boot QEMU lane.')
    parser.add_argument('--version', help='Cached tails-amd64 version to launch, e.g. 7.7.2')
    parser.add_argument('--timeout', type=int, default=20, help='Launch timeout in seconds. Default: 20')
    parser.add_argument('--memory-mb', type=int, default=2048, help='Memory for smoke launch. Default: 2048')
    parser.add_argument('--cpus', type=int, default=1, help='vCPUs for smoke launch. Default: 1')
    parser.add_argument('--json', action='store_true', help='Print JSON result')
    args = parser.parse_args()

    image = choose_image(args.version)
    result = run_smoke(image=image, timeout=args.timeout, memory_mb=args.memory_mb, cpus=args.cpus)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        outcome = 'passed' if result['success'] else 'failed'
        print(f"real-boot qemu launch smoke {outcome}: {result['image_name']} rc={result['returncode']}")
        if result['stderr_tail']:
            print(result['stderr_tail'])
    return 0 if result['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
