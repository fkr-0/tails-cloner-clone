from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from tails_cloner.source import get_parent_disk_path, get_running_tails_device, is_running_tails

LSBLK_INSPECT_COLUMNS = "PATH,TYPE,FSTYPE,LABEL,PTTYPE,SIZE,MOUNTPOINTS"
RunCommand = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True, slots=True)
class DriveTailsFacts:
    drive_path: str
    tails_installed: bool
    tails_version: str | None
    running_tails_on_this_drive: bool
    persistence_configured: bool
    persistence_partition_size_bytes: int | None
    version_detection_error: str | None = None
    version_detection_requires_privilege: bool = False


def _run_json(cmd: list[str], run: RunCommand) -> dict[str, Any]:
    result = run(cmd, check=True, text=True, capture_output=True)
    return json.loads(result.stdout)


def _collect_partitions(layout: dict[str, Any]) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    blockdevices = layout.get("blockdevices", [])
    if not blockdevices:
        return None, []

    disk = blockdevices[0]
    partitions = [child for child in disk.get("children", []) if child.get("type") == "part"]
    return disk, partitions


def has_tails_installation(device_info: dict[str, Any], partitions: list[dict[str, Any]]) -> bool:
    if device_info.get("fstype") == "iso9660":
        return False

    if device_info.get("pttype") != "gpt":
        return False

    for part in partitions:
        if part.get("fstype") == "vfat" and str(part.get("label") or "").lower() == "tails":
            return True

    return False


def _mountpoints(part: dict[str, Any]) -> list[str]:
    mountpoints = part.get("mountpoints")
    if isinstance(mountpoints, list):
        return [mp for mp in mountpoints if isinstance(mp, str) and mp]
    mountpoint = part.get("mountpoint")
    if isinstance(mountpoint, str) and mountpoint:
        return [mountpoint]
    return []


def _tails_partition(partitions: list[dict[str, Any]]) -> dict[str, Any] | None:
    for part in partitions:
        if part.get("fstype") == "vfat" and str(part.get("label") or "").lower() == "tails":
            return part
    return None


def _read_tails_version_from_mounted_partition(partitions: list[dict[str, Any]]) -> str | None:
    part = _tails_partition(partitions)
    if part is None:
        return None
    for mountpoint in _mountpoints(part):
        version_file = Path(mountpoint) / "live" / "Tails.version"
        if version_file.exists():
            try:
                return version_file.read_text(encoding="utf-8").strip()
            except OSError:
                return None
    return None


def _read_tails_version_with_direct_mount(partition_path: str) -> str | None:
    with TemporaryDirectory(prefix="tails-cloner-version-") as mount_dir:
        mount_path = Path(mount_dir)
        try:
            subprocess.run(["mount", "-o", "ro", partition_path, str(mount_path)], check=True, text=True, capture_output=True)
            version_file = mount_path / "live" / "Tails.version"
            return version_file.read_text(encoding="utf-8").strip() if version_file.exists() else None
        finally:
            subprocess.run(["umount", str(mount_path)], check=False, text=True, capture_output=True)


def _read_tails_version_with_pkexec(partition_path: str, run: RunCommand) -> tuple[str | None, str | None]:
    script = """
set -eu
partition="$1"
mount_dir="$(mktemp -d /tmp/tails-cloner-version.XXXXXX)"
cleanup() {
  umount "$mount_dir" >/dev/null 2>&1 || true
  rmdir "$mount_dir" >/dev/null 2>&1 || true
}
trap cleanup EXIT
mount -o ro "$partition" "$mount_dir"
if [ -f "$mount_dir/live/Tails.version" ]; then
  cat "$mount_dir/live/Tails.version"
fi
""".strip()
    result = run(
        ["pkexec", "sh", "-c", script, "sh", partition_path],
        check=False,
        text=True,
        capture_output=True,
    )
    if result.returncode == 0:
        version = result.stdout.strip()
        return (version or None, None)
    error = result.stderr.strip() or result.stdout.strip() or f"pkexec exited with {result.returncode}"
    return None, error


def read_tails_version_from_unmounted_partition(
    partition_path: str,
    *,
    allow_privileged_mount: bool = False,
    prompt_for_privilege: bool = False,
    run: RunCommand = subprocess.run,
) -> tuple[str | None, str | None, bool]:
    """Read Tails.version from an unmounted Tails partition.

    Returns (version, error, requires_privilege). By default this function does
    not attempt privileged operations; callers can surface the requires flag and
    ask the user before retrying with prompt_for_privilege=True.
    """
    if not allow_privileged_mount:
        return None, "Tails version is on an unmounted partition; privileged read-only mount is required.", True

    try:
        return _read_tails_version_with_direct_mount(partition_path), None, False
    except Exception as direct_error:  # noqa: BLE001 - converted into user-visible state
        if not prompt_for_privilege:
            return None, f"Read-only mount failed: {direct_error}", True

    version, error = _read_tails_version_with_pkexec(partition_path, run)
    return version, error, error is not None


def _is_persistence_partition(part: dict[str, Any]) -> bool:
    label = str(part.get("label") or "").lower()
    return label in {"persistence", "tailsdata_unlocked"}


def inspect_drive_tails_facts(
    drive_path: str,
    run: RunCommand = subprocess.run,
    *,
    allow_privileged_mount: bool = False,
    prompt_for_privilege: bool = False,
) -> DriveTailsFacts:
    layout = _run_json(
        ["lsblk", "--json", "--bytes", "--output", LSBLK_INSPECT_COLUMNS, drive_path],
        run,
    )
    disk, partitions = _collect_partitions(layout)

    if disk is None:
        return DriveTailsFacts(
            drive_path=drive_path,
            tails_installed=False,
            tails_version=None,
            running_tails_on_this_drive=False,
            persistence_configured=False,
            persistence_partition_size_bytes=None,
        )

    tails_installed = has_tails_installation(disk, partitions)
    tails_version = _read_tails_version_from_mounted_partition(partitions) if tails_installed else None
    version_error = None
    version_requires_privilege = False
    tails_part = _tails_partition(partitions)
    if tails_installed and tails_version is None and tails_part is not None:
        part_path = str(tails_part.get("path") or "")
        if part_path:
            tails_version, version_error, version_requires_privilege = read_tails_version_from_unmounted_partition(
                part_path,
                allow_privileged_mount=allow_privileged_mount,
                prompt_for_privilege=prompt_for_privilege,
                run=run,
            )

    persistence_partition = next((part for part in partitions if _is_persistence_partition(part)), None)
    persistence_size = int(persistence_partition.get("size") or 0) if persistence_partition else None

    running_from_drive = False
    if is_running_tails():
        running_dev = get_running_tails_device()
        if running_dev:
            running_from_drive = get_parent_disk_path(running_dev) == get_parent_disk_path(drive_path)

    return DriveTailsFacts(
        drive_path=drive_path,
        tails_installed=tails_installed,
        tails_version=tails_version,
        running_tails_on_this_drive=running_from_drive,
        persistence_configured=persistence_partition is not None,
        persistence_partition_size_bytes=persistence_size,
        version_detection_error=version_error,
        version_detection_requires_privilege=version_requires_privilege,
    )
