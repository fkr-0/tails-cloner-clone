#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_DIR = Path(os.environ.get('TAILS_CLONER_CAPTURE_MEDIA_DIR', REPO_ROOT / '.cache/vagrant-lab/capture-media'))
TAILS_IMAGE_CACHE = REPO_ROOT / '.cache/vagrant-lab/tails-images'


def run(command: list[str], *, require: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if require and result.returncode != 0:
        raise SystemExit(f"command failed: {' '.join(command)}\nstdout={result.stdout}\nstderr={result.stderr}")
    return result


def require_tool(name: str) -> None:
    if shutil.which(name) is None:
        raise SystemExit(f'missing required tool: {name}')


def ensure_output_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def cached_tails_image(version: str) -> Path:
    path = TAILS_IMAGE_CACHE / f'tails-amd64-{version}.img'
    if not path.exists():
        raise SystemExit(f'cached Tails image missing: {path}')
    return path


def create_sparse_file(path: Path, size_mib: int, *, force: bool) -> None:
    if path.exists() and not force:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    run(['truncate', '-s', f'{size_mib}M', str(path)])


def make_vfat_partitioned_image(path: Path, *, size_mib: int, label: str, version: str | None, force: bool) -> None:
    require_tool('parted')
    require_tool('mkfs.vfat')
    require_tool('mcopy')
    if path.exists() and not force:
        return
    create_sparse_file(path, size_mib, force=True)
    run(['parted', '-s', str(path), 'mklabel', 'gpt'])
    run(['parted', '-s', str(path), 'mkpart', 'primary', 'fat32', '1MiB', f'{size_mib - 1}MiB'])
    run(['mkfs.vfat', '-n', label, '--offset=2048', str(path)])
    if version is not None:
        tempdir = path.parent / f'.{path.name}.mtools'
        if tempdir.exists():
            shutil.rmtree(tempdir)
        tempdir.mkdir(parents=True)
        version_file = tempdir / 'Tails.version'
        version_file.write_text(version + '\n', encoding='utf-8')
        run(['mmd', '-i', f'{path}@@1048576', '::/live'])
        run(['mcopy', '-i', f'{path}@@1048576', str(version_file), '::/live/Tails.version'])
        shutil.rmtree(tempdir)


def prepare_media(
    *,
    output_dir: Path,
    newer_version: str,
    persistent_size_mib: int,
    source_size_mib: int,
    force: bool,
) -> dict[str, Any]:
    output_dir = ensure_output_dir(output_dir)
    newer_img = cached_tails_image(newer_version)
    persistent_target = output_dir / 'persistent-target-media.img'
    attached_source = output_dir / 'newer-attached-source-media.img'
    make_vfat_partitioned_image(
        persistent_target,
        size_mib=persistent_size_mib,
        label='TAILS',
        version='persistent-target-fixture',
        force=force,
    )
    make_vfat_partitioned_image(
        attached_source,
        size_mib=source_size_mib,
        label='TAILS_SRC',
        version=f'{newer_version}-attached-source-fixture',
        force=force,
    )
    return {
        'output_dir': str(output_dir),
        'newer_img_on_disc': str(newer_img),
        'persistent_target_media': str(persistent_target),
        'newer_attached_source_media': str(attached_source),
        'roles': {
            'newer_img_on_disc': str(newer_img),
            'persistent_target_media': str(persistent_target),
            'newer_attached_source_media': str(attached_source),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description='Prepare host-side media files for real-boot capture-session role mappings.')
    parser.add_argument('--output-dir', type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument('--newer-version', default='7.7.2')
    parser.add_argument('--persistent-size-mib', type=int, default=1536)
    parser.add_argument('--source-size-mib', type=int, default=1024)
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args()
    result = prepare_media(
        output_dir=args.output_dir,
        newer_version=args.newer_version,
        persistent_size_mib=args.persistent_size_mib,
        source_size_mib=args.source_size_mib,
        force=args.force,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
