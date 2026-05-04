from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from tails_cloner.source import get_parent_disk_path, get_running_tails_device, is_running_tails

LSBLK_INSPECT_COLUMNS = "PATH,TYPE,FSTYPE,LABEL,PTTYPE,SIZE,MOUNTPOINTS"


@dataclass(frozen=True, slots=True)
class DriveTailsFacts:
    drive_path: str
    tails_installed: bool
    tails_version: str | None
    running_tails_on_this_drive: bool
    persistence_configured: bool
    persistence_partition_size_bytes: int | None


def _run_json(cmd: list[str], run: callable) -> dict:
    result = run(cmd, check=True, text=True, capture_output=True)
    return json.loads(result.stdout)


def _collect_partitions(layout: dict) -> tuple[dict | None, list[dict]]:
    blockdevices = layout.get("blockdevices", [])
    if not blockdevices:
        return None, []

    disk = blockdevices[0]
    partitions = [child for child in disk.get("children", []) if child.get("type") == "part"]
    return disk, partitions


def has_tails_installation(device_info: dict, partitions: list[dict]) -> bool:
    if device_info.get("fstype") == "iso9660":
        return False

    if device_info.get("pttype") != "gpt":
        return False

    for part in partitions:
        if part.get("fstype") == "vfat" and str(part.get("label") or "").lower() == "tails":
            return True

    return False


def _mountpoints(part: dict) -> list[str]:
    mountpoints = part.get("mountpoints")
    if isinstance(mountpoints, list):
        return [mp for mp in mountpoints if isinstance(mp, str) and mp]
    mountpoint = part.get("mountpoint")
    if isinstance(mountpoint, str) and mountpoint:
        return [mountpoint]
    return []


def _read_tails_version_from_mounted_partition(partitions: list[dict]) -> str | None:
    for part in partitions:
        if part.get("fstype") != "vfat" or str(part.get("label") or "").lower() != "tails":
            continue
        for mountpoint in _mountpoints(part):
            version_file = Path(mountpoint) / "live" / "Tails.version"
            if version_file.exists():
                try:
                    return version_file.read_text(encoding="utf-8").strip()
                except OSError:
                    return None
    return None


def _is_persistence_partition(part: dict) -> bool:
    label = str(part.get("label") or "").lower()
    return label in {"persistence", "tailsdata_unlocked"}


def inspect_drive_tails_facts(
    drive_path: str,
    run: callable = subprocess.run,
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
    )
