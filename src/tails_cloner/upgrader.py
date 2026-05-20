from __future__ import annotations

"""Persistence-preserving Tails upgrade primitives.

The whole-device cloner path intentionally rewrites the target disk. Upgrades must not do
that: an existing Tails installation can contain a Persistent Storage partition that must
survive. This module provides a non-interactive backend primitive that replaces only the
Tails system/boot partition from a newer Tails disk image.
"""

import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

ProgressCallback = Callable[[str], None]
RunCommand = Callable[[list[str]], subprocess.CompletedProcess[str]]


def run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _emit(callback: ProgressCallback | None, message: str) -> None:
    if callback is not None:
        callback(message)


def _run_json(command: list[str], runner: RunCommand) -> dict[str, Any]:
    result = runner(command)
    return json.loads(result.stdout)


def list_partitions(device: str, runner: RunCommand = run_command) -> list[dict[str, Any]]:
    data = _run_json(['lsblk', '-J', '-o', 'NAME,PATH,FSTYPE,LABEL,PARTLABEL,SIZE,TYPE', device], runner)
    parts: list[dict[str, Any]] = []
    for block_device in data.get('blockdevices', []):
        parts.extend(block_device.get('children') or [])
    return parts


def find_partition(
    device: str,
    *,
    fstype: str | None = None,
    label: str | None = None,
    runner: RunCommand = run_command,
) -> str:
    for part in list_partitions(device, runner):
        if fstype is not None and part.get('fstype') != fstype:
            continue
        if label is not None and part.get('label') != label:
            continue
        path = str(part.get('path') or '')
        if path:
            return path
    raise RuntimeError(f'No matching partition found on {device}: fstype={fstype!r}, label={label!r}')


def has_persistence_partition(device: str, runner: RunCommand = run_command) -> bool:
    for part in list_partitions(device, runner):
        label_value = str(part.get('label') or '').lower()
        fstype_value = str(part.get('fstype') or '').lower()
        if label_value in {'persistence', 'tailsdata_unlocked'} and fstype_value in {'ext4', 'crypto_luks'}:
            return True
    return False


def attach_image(image_path: str | Path, runner: RunCommand = run_command) -> str:
    result = runner(['losetup', '--find', '--partscan', '--show', str(image_path)])
    loopdev = result.stdout.strip()
    if not loopdev:
        raise RuntimeError(f'losetup did not return a loop device for {image_path}')
    return loopdev


def detach_image(loopdev: str, runner: RunCommand = run_command) -> None:
    runner(['losetup', '-d', loopdev])


def build_partition_upgrade_command(source_partition: str, target_partition: str) -> list[str]:
    return [
        'dd',
        f'if={source_partition}',
        f'of={target_partition}',
        'bs=4M',
        'status=progress',
        'conv=fsync',
    ]


def _upgrade_from_source_partition(
    source_partition: str,
    target_device: str,
    *,
    runner: RunCommand = run_command,
    progress_callback: ProgressCallback | None = None,
) -> None:
    _emit(progress_callback, f'Checking persistence on {target_device}')
    if not has_persistence_partition(target_device, runner):
        raise RuntimeError(f'Target device has no persistence partition: {target_device}')

    target_partition = find_partition(target_device, fstype='vfat', runner=runner)
    _emit(progress_callback, f'Using target Tails system partition {target_partition}')

    command = build_partition_upgrade_command(source_partition, target_partition)
    _emit(progress_callback, 'Running partition-scoped Tails upgrade')
    runner(command)
    runner(['sync'])
    runner(['blockdev', '--flushbufs', target_device])

    if not has_persistence_partition(target_device, runner):
        raise RuntimeError(f'Persistence partition disappeared after upgrade: {target_device}')
    _emit(progress_callback, 'Tails system partition upgraded; persistence partition still present')


def upgrade_tails_system_partition_from_device(
    source_device: str,
    target_device: str,
    *,
    runner: RunCommand = run_command,
    progress_callback: ProgressCallback | None = None,
) -> None:
    """Upgrade an existing Tails target from another attached Tails live device.

    This is the source-device counterpart to upgrade_tails_system_partition().
    It never rewrites the whole target disk: only the target Tails system
    partition is overwritten from the source live system partition.
    """
    if source_device == target_device:
        raise RuntimeError('Source and target devices must be different for a persistence-preserving upgrade')
    source_partition = find_partition(source_device, fstype='vfat', runner=runner)
    _emit(progress_callback, f'Using source Tails system partition {source_partition}')
    _upgrade_from_source_partition(
        source_partition,
        target_device,
        runner=runner,
        progress_callback=progress_callback,
    )


def upgrade_tails_system_partition(
    image_path: str | Path,
    target_device: str,
    *,
    runner: RunCommand = run_command,
    progress_callback: ProgressCallback | None = None,
) -> None:
    """Upgrade an existing Tails target without rewriting persistence.

    The real upgrade operation is intentionally partition-scoped:
    - source: first vfat partition from the newer Tails IMG
    - target: existing vfat Tails system partition on the target disk
    - persistence: required before and after, never mounted or written here
    """
    image = Path(image_path)
    if not image.exists():
        raise FileNotFoundError(image)

    loopdev = attach_image(image, runner)
    _emit(progress_callback, f'Attached source image {image} as {loopdev}')
    try:
        source_partition = find_partition(loopdev, fstype='vfat', runner=runner)
        _upgrade_from_source_partition(
            source_partition,
            target_device,
            runner=runner,
            progress_callback=progress_callback,
        )
    finally:
        try:
            detach_image(loopdev, runner)
        except subprocess.CalledProcessError as error:
            _emit(progress_callback, f'Warning: failed to detach {loopdev}: {error}')
