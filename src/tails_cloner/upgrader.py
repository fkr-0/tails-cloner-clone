"""Partition-scoped Tails upgrade primitives.

Install/reinstall rewrites a complete target disk. An upgrade must instead replace only
the existing Tails system partition so a separate Persistent Storage partition, when
present, remains untouched.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tails_cloner.source import LocalImageSource

ProgressCallback = Callable[[str], None]
RunCommand = Callable[[list[str]], subprocess.CompletedProcess[str]]
Sleep = Callable[[float], None]

LSBLK_COLUMNS = "NAME,PATH,FSTYPE,LABEL,PARTLABEL,SIZE,TYPE,RO,MOUNTPOINTS"
TAILS_FILESYSTEM_LABEL = "tails"
PERSISTENCE_LABELS = {"persistence", "tailsdata", "tailsdata_unlocked"}
PROTECTED_SYSTEM_MOUNTPOINTS = {"/", "/boot", "/boot/efi", "/home", "/usr", "/var"}
PROTECTED_LIVE_MOUNT_PREFIXES = ("/lib/live/mount", "/run/live")
LOOP_PARTITION_WAIT_TIMEOUT_SECONDS = 2.0
LOOP_PARTITION_POLL_INTERVAL_SECONDS = 0.1


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


def _best_effort_udev_settle(runner: RunCommand) -> str:
    """Ask udev to settle, but return diagnostics instead of making it authoritative."""
    try:
        result = runner(["udevadm", "settle"])
    except (OSError, subprocess.SubprocessError) as error:
        return f"{type(error).__name__}: {error}"
    if result.returncode != 0:
        stderr = result.stderr.strip() if result.stderr else "no stderr"
        return f"exit {result.returncode}: {stderr}"
    return ""


def _wait_for_loop_partitions(
    loopdev: str,
    runner: RunCommand,
    *,
    timeout_seconds: float = LOOP_PARTITION_WAIT_TIMEOUT_SECONDS,
    poll_interval_seconds: float = LOOP_PARTITION_POLL_INTERVAL_SECONDS,
    sleep: Sleep = time.sleep,
    settle_diagnostic: str = "",
) -> None:
    interval = max(0.001, poll_interval_seconds)
    attempts = max(1, int(max(0.0, timeout_seconds) / interval) + 1)
    last_diagnostic = "no partition nodes reported by lsblk"
    for attempt in range(attempts):
        try:
            visible = [partition.path for partition in partition_infos(loopdev, runner) if partition.path]
        except (OSError, subprocess.SubprocessError, RuntimeError, ValueError) as error:
            last_diagnostic = f"lsblk probe failed: {type(error).__name__}: {error}"
        else:
            if visible:
                return
            last_diagnostic = "lsblk reported the loop device but no partition nodes"
        if attempt + 1 < attempts:
            sleep(interval)

    settle_suffix = f"; udevadm settle also failed ({settle_diagnostic})" if settle_diagnostic else ""
    raise RuntimeError(
        f"Loop partitions for {loopdev} did not become visible within {timeout_seconds:.2f}s "
        f"after losetup --partscan: {last_diagnostic}{settle_suffix}"
    )


def attach_image(
    image_path: str | Path,
    runner: RunCommand = run_command,
    privileged_runner: RunCommand | None = None,
    *,
    partition_wait_timeout_seconds: float = LOOP_PARTITION_WAIT_TIMEOUT_SECONDS,
    partition_poll_interval_seconds: float = LOOP_PARTITION_POLL_INTERVAL_SECONDS,
    sleep: Sleep = time.sleep,
) -> str:
    mutate = _resolve_privileged_runner(runner, privileged_runner)
    result = mutate(["losetup", "--read-only", "--find", "--partscan", "--show", str(image_path)])
    loopdev = result.stdout.strip()
    if not loopdev:
        raise RuntimeError(f"losetup did not return a loop device for {image_path}")
    settle_diagnostic = _best_effort_udev_settle(runner)
    try:
        _wait_for_loop_partitions(
            loopdev,
            runner,
            timeout_seconds=partition_wait_timeout_seconds,
            poll_interval_seconds=partition_poll_interval_seconds,
            sleep=sleep,
            settle_diagnostic=settle_diagnostic,
        )
    except RuntimeError as error:
        try:
            mutate(["losetup", "--detach", loopdev])
        except (OSError, subprocess.SubprocessError) as cleanup_error:
            raise RuntimeError(
                f"{error}; additionally failed to detach {loopdev}: {type(cleanup_error).__name__}: {cleanup_error}"
            ) from error
        raise
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

    protected_mountpoints = sorted(
        mountpoint
        for mountpoint in target.mountpoints
        if mountpoint in PROTECTED_SYSTEM_MOUNTPOINTS
        or any(
            mountpoint == prefix or mountpoint.startswith(f"{prefix}/")
            for prefix in PROTECTED_LIVE_MOUNT_PREFIXES
        )
    )
    if protected_mountpoints:
        raise RuntimeError(
            "Refusing to upgrade a Tails partition used by the currently running operating system: "
            + ", ".join(protected_mountpoints)
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
    _best_effort_udev_settle(runner)

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
