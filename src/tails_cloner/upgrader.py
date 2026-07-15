"""Partition-scoped Tails upgrade primitives.

Install/reinstall rewrites a complete target disk. An upgrade must instead replace only
the existing Tails system partition so a separate Persistent Storage partition, when
present, remains untouched.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tails_cloner.source import LocalImageSource

ProgressCallback = Callable[[str], None]
RunCommand = Callable[[list[str]], subprocess.CompletedProcess[str]]

LSBLK_COLUMNS = "NAME,PATH,FSTYPE,LABEL,PARTLABEL,SIZE,TYPE,RO,MOUNTPOINTS"
TAILS_FILESYSTEM_LABEL = "tails"
PERSISTENCE_LABELS = {"persistence", "tailsdata", "tailsdata_unlocked"}


@dataclass(frozen=True, slots=True)
class PartitionInfo:
    path: str
    fstype: str
    label: str
    partlabel: str
    size_bytes: int
    read_only: bool
    mountpoints: tuple[str, ...]


def run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, text=True, capture_output=True)


def build_privileged_command(command: list[str], *, effective_uid: int | None = None) -> list[str]:
    """Return a command routed through polkit unless already running as root."""
    uid = os.geteuid() if effective_uid is None else effective_uid
    return list(command) if uid == 0 else ["pkexec", *command]


def run_privileged_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    return run_command(build_privileged_command(command))


def _resolve_privileged_runner(runner: RunCommand, privileged_runner: RunCommand | None) -> RunCommand:
    if privileged_runner is not None:
        return privileged_runner
    # Preserve the lightweight injected-runner API used by tests and callers.
    # Production uses a distinct polkit-backed mutation runner.
    return run_privileged_command if runner is run_command else runner


def _emit(callback: ProgressCallback | None, message: str) -> None:
    if callback is not None:
        callback(message)


def _run_json(command: list[str], runner: RunCommand) -> dict[str, Any]:
    result = runner(command)
    payload = json.loads(result.stdout)
    if not isinstance(payload, dict):
        raise RuntimeError(f"Expected JSON object from {' '.join(command)}")
    return payload


def _walk_block_nodes(nodes: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    for node in nodes:
        yield node
        children = node.get("children") or []
        if isinstance(children, list):
            yield from _walk_block_nodes([child for child in children if isinstance(child, dict)])


def list_partitions(device: str, runner: RunCommand = run_command) -> list[dict[str, Any]]:
    data = _run_json(["lsblk", "--json", "--bytes", "--output", LSBLK_COLUMNS, device], runner)
    roots = [node for node in data.get("blockdevices", []) if isinstance(node, dict)]
    result: list[dict[str, Any]] = []
    for root in roots:
        for node in _walk_block_nodes([root]):
            if node is root:
                continue
            if node.get("type") in {"part", "crypt"}:
                result.append(node)
    return result


def _mountpoints(value: object) -> tuple[str, ...]:
    if isinstance(value, list):
        return tuple(item for item in value if isinstance(item, str) and item.startswith("/"))
    if isinstance(value, str) and value.startswith("/"):
        return (value,)
    return ()


def _partition_info(raw: dict[str, Any]) -> PartitionInfo:
    return PartitionInfo(
        path=str(raw.get("path") or ""),
        fstype=str(raw.get("fstype") or "").casefold(),
        label=str(raw.get("label") or "").casefold(),
        partlabel=str(raw.get("partlabel") or "").casefold(),
        size_bytes=int(raw.get("size") or 0),
        read_only=bool(raw.get("ro", False)),
        mountpoints=_mountpoints(raw.get("mountpoints")),
    )


def partition_infos(device: str, runner: RunCommand = run_command) -> list[PartitionInfo]:
    return [_partition_info(partition) for partition in list_partitions(device, runner)]


def find_partition(
    device: str,
    *,
    fstype: str | None = None,
    label: str | None = None,
    runner: RunCommand = run_command,
) -> str:
    expected_fstype = fstype.casefold() if fstype is not None else None
    expected_label = label.casefold() if label is not None else None
    for partition in partition_infos(device, runner):
        if expected_fstype is not None and partition.fstype != expected_fstype:
            continue
        if expected_label is not None and partition.label != expected_label:
            continue
        if partition.path:
            return partition.path
    raise RuntimeError(f"No matching partition found on {device}: fstype={fstype!r}, label={label!r}")


def find_tails_system_partition(device: str, runner: RunCommand = run_command) -> PartitionInfo:
    for partition in partition_infos(device, runner):
        if partition.fstype not in {"vfat", "fat", "msdos"}:
            continue
        if TAILS_FILESYSTEM_LABEL not in {partition.label, partition.partlabel}:
            continue
        if partition.path:
            return partition
    raise RuntimeError(f"No VFAT partition labelled Tails found on {device}")


def has_persistence_partition(device: str, runner: RunCommand = run_command) -> bool:
    for partition in partition_infos(device, runner):
        if not ({partition.label, partition.partlabel} & PERSISTENCE_LABELS):
            continue
        if partition.fstype in {"crypto_luks", "ext4"}:
            return True
    return False


def attach_image(
    image_path: str | Path,
    runner: RunCommand = run_command,
    privileged_runner: RunCommand | None = None,
) -> str:
    mutate = _resolve_privileged_runner(runner, privileged_runner)
    result = mutate(["losetup", "--read-only", "--find", "--partscan", "--show", str(image_path)])
    loopdev = result.stdout.strip()
    if not loopdev:
        raise RuntimeError(f"losetup did not return a loop device for {image_path}")
    runner(["udevadm", "settle"])
    return loopdev


def detach_image(
    loopdev: str,
    runner: RunCommand = run_command,
    privileged_runner: RunCommand | None = None,
) -> None:
    mutate = _resolve_privileged_runner(runner, privileged_runner)
    mutate(["losetup", "--detach", loopdev])


def build_partition_upgrade_command(source_partition: str, target_partition: str) -> list[str]:
    return [
        "dd",
        f"if={source_partition}",
        f"of={target_partition}",
        "bs=4M",
        "status=progress",
        "conv=fsync",
    ]


def _validate_partition_pair(source: PartitionInfo, target: PartitionInfo) -> None:
    if source.path == target.path:
        raise RuntimeError("Source and target Tails system partitions must be different")
    if target.read_only:
        raise RuntimeError(f"Target Tails system partition is read-only: {target.path}")
    if source.size_bytes > 0 and target.size_bytes > 0 and source.size_bytes > target.size_bytes:
        raise RuntimeError(
            "Source Tails system partition is larger than the target partition: "
            f"{source.size_bytes} > {target.size_bytes} bytes"
        )


def _unmount_target_partition(
    target: PartitionInfo,
    mutate: RunCommand,
    progress_callback: ProgressCallback | None,
) -> None:
    for mountpoint in target.mountpoints:
        _emit(progress_callback, f"Unmounting target system partition from {mountpoint}")
        mutate(["umount", "--", mountpoint])


def _upgrade_from_source_partition(
    source_partition: PartitionInfo,
    target_device: str,
    *,
    runner: RunCommand = run_command,
    privileged_runner: RunCommand | None = None,
    progress_callback: ProgressCallback | None = None,
) -> None:
    mutate = _resolve_privileged_runner(runner, privileged_runner)
    had_persistence = has_persistence_partition(target_device, runner)
    target_partition = find_tails_system_partition(target_device, runner)
    _validate_partition_pair(source_partition, target_partition)

    _emit(progress_callback, f"Using target Tails system partition {target_partition.path}")
    _unmount_target_partition(target_partition, mutate, progress_callback)

    command = build_partition_upgrade_command(source_partition.path, target_partition.path)
    _emit(progress_callback, "Running partition-scoped Tails upgrade")
    mutate(command)
    runner(["sync"])
    mutate(["blockdev", "--flushbufs", target_device])
    runner(["udevadm", "settle"])

    # Ensure the system partition is still recognisable and persistence was not lost.
    find_tails_system_partition(target_device, runner)
    has_persistence_after = has_persistence_partition(target_device, runner)
    if had_persistence and not has_persistence_after:
        raise RuntimeError(f"Persistent Storage partition disappeared after upgrade: {target_device}")
    if had_persistence:
        _emit(progress_callback, "Tails system partition upgraded; Persistent Storage is still present")
    else:
        _emit(progress_callback, "Tails system partition upgraded; target had no Persistent Storage partition")


def upgrade_tails_system_partition_from_device(
    source_device: str,
    target_device: str,
    *,
    runner: RunCommand = run_command,
    privileged_runner: RunCommand | None = None,
    progress_callback: ProgressCallback | None = None,
) -> None:
    """Upgrade a Tails target from another attached Tails live device."""
    if os.path.realpath(source_device) == os.path.realpath(target_device):
        raise RuntimeError("Source and target devices must be different for a partition-scoped upgrade")

    source_partition = find_tails_system_partition(source_device, runner)
    _emit(progress_callback, f"Using source Tails system partition {source_partition.path}")
    _upgrade_from_source_partition(
        source_partition,
        target_device,
        runner=runner,
        privileged_runner=privileged_runner,
        progress_callback=progress_callback,
    )


def upgrade_tails_system_partition(
    image_path: str | Path,
    target_device: str,
    *,
    runner: RunCommand = run_command,
    privileged_runner: RunCommand | None = None,
    progress_callback: ProgressCallback | None = None,
) -> None:
    """Upgrade an existing Tails target without rewriting its partition table."""
    image = LocalImageSource(Path(image_path))
    image.validate()

    loopdev = attach_image(image.path, runner, privileged_runner)
    _emit(progress_callback, f"Attached source image {image.path} read-only as {loopdev}")
    try:
        source_partition = find_tails_system_partition(loopdev, runner)
        _upgrade_from_source_partition(
            source_partition,
            target_device,
            runner=runner,
            privileged_runner=privileged_runner,
            progress_callback=progress_callback,
        )
    finally:
        try:
            detach_image(loopdev, runner, privileged_runner)
        except subprocess.CalledProcessError as error:
            _emit(progress_callback, f"Warning: failed to detach {loopdev}: {error}")
