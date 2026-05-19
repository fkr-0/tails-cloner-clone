#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from tails_cloner.creator import build_clone_command
from tails_cloner.upgrader import upgrade_tails_system_partition

STATE_PATH = Path('/opt/tails-cloner-fixtures/fixture-state.json')
ARTIFACT_DIR = Path('/opt/tails-cloner-fixtures/tails-images')
DESTRUCTIVE_FLAG = 'TAILS_CLONER_LAB_ALLOW_DESTRUCTIVE'
PERSISTENCE_MARKER = 'PERSISTENCE_MARKER.txt'


def load_fixture_state(path: Path = STATE_PATH) -> dict[str, str]:
    if not path.exists():
        raise SystemExit(f'fixture state missing: {path}')
    data: dict[str, Any] = json.loads(path.read_text())
    required = {'source_disk', 'target_fresh_disk', 'target_upgrade_disk', 'target_extra_disk'}
    missing = sorted(required - set(data))
    if missing:
        raise SystemExit(f'fixture state missing keys: {missing}')
    return {key: str(data[key]) for key in required}


def run_json(cmd: list[str]) -> dict[str, Any]:
    return json.loads(subprocess.check_output(cmd, text=True))


def run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=check, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def assert_block_device(device: str) -> None:
    path = Path(device)
    if not path.exists():
        raise SystemExit(f'device path does not exist: {device}')
    if not path.is_block_device():
        raise SystemExit(f'not a block device: {device}')


def require_destructive_lab_flag() -> None:
    if os.environ.get(DESTRUCTIVE_FLAG) != '1':
        raise SystemExit(
            f'destructive lab scenario refused; set {DESTRUCTIVE_FLAG}=1 inside the Vagrant lab VM'
        )


def choose_image(version: str) -> Path:
    image = ARTIFACT_DIR / f'tails-amd64-{version}.img'
    if not image.exists():
        raise SystemExit(f'missing scenario image: {image}')
    return image


def dry_run_install(target: str, image: Path) -> None:
    command = build_clone_command(image, target, use_pkexec=False)
    print('dry-run install command:')
    print(' '.join(command))


def destructive_install(target: str, image: Path) -> None:
    require_destructive_lab_flag()
    assert_block_device(target)
    command = build_clone_command(image, target, use_pkexec=False)
    print('running destructive lab install command:')
    print(' '.join(command))
    subprocess.run(command, check=True)
    subprocess.run(['sync'], check=True)
    subprocess.run(['blockdev', '--flushbufs', target], check=False)


def partitions(device: str) -> list[dict[str, Any]]:
    lsblk = run_json(['lsblk', '-J', '-o', 'NAME,PATH,FSTYPE,LABEL,PARTLABEL,SIZE,TYPE', device])
    parts: list[dict[str, Any]] = []
    for block_device in lsblk.get('blockdevices', []):
        parts.extend(block_device.get('children') or [])
    return parts


def find_partition(device: str, *, label: str | None = None, fstype: str | None = None) -> str:
    for part in partitions(device):
        if label is not None and part.get('label') != label:
            continue
        if fstype is not None and part.get('fstype') != fstype:
            continue
        path = str(part.get('path') or '')
        if path:
            return path
    raise SystemExit(f'could not find partition on {device}: label={label!r}, fstype={fstype!r}')


def mount_partition(partition: str, mountpoint: Path) -> None:
    mountpoint.mkdir(parents=True, exist_ok=True)
    run(['mount', partition, str(mountpoint)])


def unmount_quietly(mountpoint: Path) -> None:
    run(['umount', str(mountpoint)], check=False)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def persistence_marker_digest(device: str) -> str:
    persistence_part = find_partition(device, label='persistence', fstype='ext4')
    with tempfile.TemporaryDirectory(prefix='tails-cloner-persist-') as tmp:
        mountpoint = Path(tmp)
        mount_partition(persistence_part, mountpoint)
        try:
            marker = mountpoint / PERSISTENCE_MARKER
            if not marker.exists():
                raise SystemExit(f'persistence marker missing after upgrade: {marker}')
            return sha256_file(marker)
        finally:
            unmount_quietly(mountpoint)


def validate_upgrade_persistence_marker(state: dict[str, str]) -> None:
    target = state['target_upgrade_disk']
    assert_block_device(target)
    labels = {str(part.get('label') or '') for part in partitions(target)}
    fstypes = {str(part.get('fstype') or '') for part in partitions(target)}
    if 'persistence' not in labels:
        raise SystemExit(f'upgrade disk is missing persistence label: {labels}')
    if 'ext4' not in fstypes:
        raise SystemExit(f'upgrade disk is missing ext4 persistence partition: {fstypes}')
    digest = persistence_marker_digest(target)
    print(f'upgrade persistence layout validator passed; marker_sha256={digest}')


def validate_fixture_layout(state: dict[str, str]) -> None:
    for name, device in sorted(state.items()):
        assert_block_device(device)
        print(f'{name}: {device}')
    validate_upgrade_persistence_marker(state)


def simulate_internal_upgrade_preserve_persistence(state: dict[str, str], image: Path) -> None:
    """Run the real partition-scoped upgrader and prove persistence survives."""
    require_destructive_lab_flag()
    target = state['target_upgrade_disk']
    assert_block_device(target)

    before_parts = partitions(target)
    before_digest = persistence_marker_digest(target)
    print(f'before marker_sha256={before_digest}')
    print(f'before partitions={json.dumps(before_parts, sort_keys=True)}')

    upgrade_tails_system_partition(image, target, progress_callback=print)

    after_parts = partitions(target)
    after_digest = persistence_marker_digest(target)
    print(f'after marker_sha256={after_digest}')
    print(f'after partitions={json.dumps(after_parts, sort_keys=True)}')

    if before_digest != after_digest:
        raise SystemExit('persistence marker digest changed during internal upgrade simulation')
    if not any(part.get('label') == 'persistence' and part.get('fstype') == 'ext4' for part in after_parts):
        raise SystemExit('persistence partition missing after internal upgrade simulation')
    print('internal upgrade simulation passed: real upgrader preserved persistence partition and marker')


def main() -> int:
    parser = argparse.ArgumentParser(description='Run guarded tails-cloner lab scenarios')
    parser.add_argument(
        'scenario',
        choices=[
            'dry-run-install',
            'destructive-install',
            'validate-layout',
            'simulate-internal-upgrade-preserve-persistence',
        ],
        help='Scenario to run against the provisioned Vagrant fixture disks.',
    )
    parser.add_argument('--target', choices=['fresh', 'upgrade'], default='fresh')
    parser.add_argument('--version', default='7.7.2')
    args = parser.parse_args()

    state = load_fixture_state()
    if args.scenario == 'validate-layout':
        validate_fixture_layout(state)
        return 0

    image = choose_image(args.version)
    target = state['target_fresh_disk'] if args.target == 'fresh' else state['target_upgrade_disk']
    if args.scenario == 'dry-run-install':
        dry_run_install(target, image)
        return 0
    if args.scenario == 'simulate-internal-upgrade-preserve-persistence':
        simulate_internal_upgrade_preserve_persistence(state, image)
        return 0
    destructive_install(target, image)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
