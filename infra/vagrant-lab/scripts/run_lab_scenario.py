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
from tails_cloner.drive_inspector import inspect_drive_tails_facts
from tails_cloner.upgrader import (
    build_partition_upgrade_command,
    upgrade_tails_system_partition,
    upgrade_tails_system_partition_from_device,
)

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
    return subprocess.run(cmd, check=check, text=True, capture_output=True)


def assert_block_device(device: str) -> None:
    path = Path(device)
    if not path.exists():
        raise SystemExit(f'device path does not exist: {device}')
    if not path.is_block_device():
        raise SystemExit(f'not a block device: {device}')


def block_device_size_bytes(device: str) -> int:
    assert_block_device(device)
    result = run(['blockdev', '--getsize64', device])
    return int(result.stdout.strip())


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


def source_live_usb_version(device: str) -> str:
    source_system_partition = find_partition(device, label='TAILS_SRC', fstype='vfat')
    with tempfile.TemporaryDirectory(prefix='tails-cloner-source-') as tmp:
        mountpoint = Path(tmp)
        mount_partition(source_system_partition, mountpoint)
        try:
            version_file = mountpoint / 'live' / 'Tails.version'
            if not version_file.exists():
                raise SystemExit(f'source fixture missing live/Tails.version: {version_file}')
            version = version_file.read_text(encoding='utf-8').strip()
            if not version:
                raise SystemExit(f'source fixture has empty live/Tails.version: {version_file}')
            return version
        finally:
            unmount_quietly(mountpoint)


def validate_source_live_usb_fixture(source: str, target: str) -> str:
    assert_block_device(source)
    assert_block_device(target)
    if source == target:
        raise SystemExit('source and target must be different block devices')

    source_parts = partitions(source)
    labels = {str(part.get('label') or '') for part in source_parts}
    if 'TAILS_SRC' not in labels:
        raise SystemExit(f'source fixture missing TAILS_SRC label: {labels}')

    version = source_live_usb_version(source)
    target_facts = inspect_drive_tails_facts(target, allow_privileged_mount=False)
    if target_facts.running_tails_on_this_drive:
        raise SystemExit(f'target appears to be the running Tails source drive: {target}')
    return version


def dry_run_source_device_install(source: str, target: str) -> None:
    version = validate_source_live_usb_fixture(source, target)
    command = build_clone_command(source, target, use_pkexec=False)
    print('dry-run source-device install command:')
    print(f'source_live_usb_version={version}')
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


def validate_fresh_install_layout(target: str) -> None:
    assert_block_device(target)
    subprocess.run(['partprobe', target], check=False)
    subprocess.run(['udevadm', 'settle'], check=False)
    target_parts = partitions(target)
    if not target_parts:
        raise SystemExit(f'fresh install target has no partitions after write: {target}')

    vfat_parts = [part for part in target_parts if part.get('fstype') == 'vfat']
    if not vfat_parts:
        raise SystemExit(f'fresh install target has no vfat Tails system partition: {target_parts}')

    persistence_parts = [part for part in target_parts if part.get('label') == 'persistence']
    if persistence_parts:
        raise SystemExit(f'fresh install unexpectedly created a persistence partition: {persistence_parts}')

    print(f'fresh install layout validator passed; partitions={json.dumps(target_parts, sort_keys=True)}')


def destructive_install_validate(target: str, image: Path) -> None:
    destructive_install(target, image)
    validate_fresh_install_layout(target)


def destructive_source_device_install_validate(source: str, target: str) -> None:
    require_destructive_lab_flag()
    source_version = validate_source_live_usb_fixture(source, target)
    command = build_clone_command(source, target, use_pkexec=False)
    print('running destructive source-device lab install command:')
    print(f'source_live_usb_version={source_version}')
    print(' '.join(command))
    subprocess.run(command, check=True)
    subprocess.run(['sync'], check=True)
    subprocess.run(['blockdev', '--flushbufs', target], check=False)

    target_parts = partitions(target)
    labels = {str(part.get('label') or '') for part in target_parts}
    if 'TAILS_SRC' not in labels:
        raise SystemExit(f'source-device install target missing copied TAILS_SRC label: {target_parts}')
    target_version = source_live_usb_version(target)
    if target_version != source_version:
        raise SystemExit(f'source-device install version mismatch: source={source_version} target={target_version}')
    print(f'source-device install validator passed; version={target_version}; partitions={json.dumps(target_parts, sort_keys=True)}')


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


def assert_persistence_preserved(target: str, before_digest: str, scenario_name: str) -> None:
    after_parts = partitions(target)
    after_digest = persistence_marker_digest(target)
    print(f'after marker_sha256={after_digest}')
    print(f'after partitions={json.dumps(after_parts, sort_keys=True)}')

    if before_digest != after_digest:
        raise SystemExit(f'persistence marker digest changed during {scenario_name}')
    if not any(part.get('label') == 'persistence' and part.get('fstype') == 'ext4' for part in after_parts):
        raise SystemExit(f'persistence partition missing after {scenario_name}')


def simulate_internal_upgrade_preserve_persistence(state: dict[str, str], image: Path) -> None:
    """Run the real image-based partition-scoped upgrader and prove persistence survives."""
    require_destructive_lab_flag()
    target = state['target_upgrade_disk']
    assert_block_device(target)

    before_parts = partitions(target)
    before_digest = persistence_marker_digest(target)
    print(f'before marker_sha256={before_digest}')
    print(f'before partitions={json.dumps(before_parts, sort_keys=True)}')

    upgrade_tails_system_partition(image, target, progress_callback=print)

    assert_persistence_preserved(target, before_digest, 'internal image-based upgrade simulation')
    print('internal upgrade simulation passed: real upgrader preserved persistence partition and marker')


def source_device_upgrade_partitions(source: str, target: str) -> tuple[str, str]:
    source_partition = find_partition(source, label='TAILS_SRC', fstype='vfat')
    target_partition = find_partition(target, label='TAILS', fstype='vfat')
    source_size = block_device_size_bytes(source_partition)
    target_size = block_device_size_bytes(target_partition)
    if source_size > target_size:
        raise SystemExit(
            'source system partition is larger than target system partition: '
            f'{source_partition}={source_size} > {target_partition}={target_size}'
        )
    return source_partition, target_partition


def preflight_source_device_upgrade_preserve_persistence(state: dict[str, str]) -> None:
    """Validate the source-device upgrade plan without writing to the target."""
    source = state['source_disk']
    target = state['target_upgrade_disk']
    source_version = validate_source_live_usb_fixture(source, target)
    before_parts = partitions(target)
    before_digest = persistence_marker_digest(target)
    source_partition, target_partition = source_device_upgrade_partitions(source, target)
    command = build_partition_upgrade_command(source_partition, target_partition)
    print(f'source_live_usb_version={source_version}')
    print(f'before marker_sha256={before_digest}')
    print(f'before partitions={json.dumps(before_parts, sort_keys=True)}')
    print('preflight source-device upgrade command:')
    print(' '.join(command))
    print('preflight source-device upgrade passed: no writes performed')


def simulate_source_device_upgrade_preserve_persistence(state: dict[str, str]) -> None:
    """Run the real source-device partition-scoped upgrader and prove persistence survives."""
    require_destructive_lab_flag()
    source = state['source_disk']
    target = state['target_upgrade_disk']
    validate_source_live_usb_fixture(source, target)
    source_device_upgrade_partitions(source, target)
    assert_block_device(target)

    before_parts = partitions(target)
    before_digest = persistence_marker_digest(target)
    print(f'before marker_sha256={before_digest}')
    print(f'before partitions={json.dumps(before_parts, sort_keys=True)}')

    upgrade_tails_system_partition_from_device(source, target, progress_callback=print)

    assert_persistence_preserved(target, before_digest, 'source-device upgrade simulation')
    print('source-device upgrade simulation passed: attached live source upgraded system partition and preserved persistence')


def main() -> int:
    parser = argparse.ArgumentParser(description='Run guarded tails-cloner lab scenarios')
    parser.add_argument(
        'scenario',
        choices=[
            'dry-run-install',
            'dry-run-source-device-install',
            'destructive-install',
            'destructive-install-validate',
            'destructive-source-device-install-validate',
            'validate-layout',
            'simulate-internal-upgrade-preserve-persistence',
            'preflight-source-device-upgrade-preserve-persistence',
            'simulate-source-device-upgrade-preserve-persistence',
        ],
        help='Scenario to run against the provisioned Vagrant fixture disks.',
    )
    parser.add_argument('--target', choices=['fresh', 'upgrade', 'extra'], default='fresh')
    parser.add_argument('--version', default='7.7.2')
    args = parser.parse_args()

    state = load_fixture_state()
    if args.scenario == 'validate-layout':
        validate_fixture_layout(state)
        return 0

    image = choose_image(args.version)
    target_by_name = {
        'fresh': state['target_fresh_disk'],
        'upgrade': state['target_upgrade_disk'],
        'extra': state['target_extra_disk'],
    }
    target = target_by_name[args.target]
    if args.scenario == 'dry-run-install':
        dry_run_install(target, image)
        return 0
    if args.scenario == 'dry-run-source-device-install':
        dry_run_source_device_install(state['source_disk'], target)
        return 0
    if args.scenario == 'simulate-internal-upgrade-preserve-persistence':
        simulate_internal_upgrade_preserve_persistence(state, image)
        return 0
    if args.scenario == 'preflight-source-device-upgrade-preserve-persistence':
        preflight_source_device_upgrade_preserve_persistence(state)
        return 0
    if args.scenario == 'simulate-source-device-upgrade-preserve-persistence':
        simulate_source_device_upgrade_preserve_persistence(state)
        return 0
    if args.scenario == 'destructive-install-validate':
        destructive_install_validate(target, image)
        return 0
    if args.scenario == 'destructive-source-device-install-validate':
        destructive_source_device_install_validate(state['source_disk'], target)
        return 0
    destructive_install(target, image)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
