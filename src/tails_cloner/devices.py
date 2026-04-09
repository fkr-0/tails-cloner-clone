from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence

from tails_cloner.models import BlockDevice


LSBLK_COLUMNS = "PATH,SIZE,MODEL,VENDOR,RM,HOTPLUG,TRAN,TYPE,RO,FSTYPE,LABEL,PARTTYPE,PTTYPE"
MIN_INSTALLATION_SIZE_GB = 8
MIN_UPGRADE_SIZE_GB = 16


def format_bytes_as_gib(size_bytes: int) -> str:
    gib = size_bytes / (1024**3)
    return f"{gib:.1f} GiB"


def device_has_tails(device_info: dict, partitions: list) -> bool:
    """Check if a device has Tails installed.

    Following the legacy installer logic:
    - Not isohybrid (not iso9660 filesystem)
    - Is GPT partition table
    - vfat filesystem on partition
    - Label is "Tails"
    """
    # Check if the device itself is isohybrid (ISO image)
    if device_info.get("fstype") == "iso9660":
        return False

    # Check for GPT partition table
    if device_info.get("pttype") != "gpt":
        return False

    # Check partitions for Tails filesystem
    for part in partitions:
        if part.get("fstype") == "vfat" and part.get("label") == "Tails":
            return True

    return False


def parse_lsblk_json(payload: dict) -> list[BlockDevice]:
    devices: list[BlockDevice] = []
    for item in payload.get("blockdevices", []):
        if item.get("type") != "disk":
            continue

        removable = bool(item.get("rm") or item.get("hotplug"))
        size_bytes = int(item.get("size") or 0)
        size_gb = size_bytes / (1024**3)

        # Get partitions for this disk
        partitions = []
        if "children" in item:
            partitions = [child for child in item["children"] if child.get("type") == "part"]

        # Detect Tails installation
        has_tails = device_has_tails(item, partitions)

        # Get filesystem info from first partition if available
        fstype = ""
        label = ""
        is_gpt = item.get("pttype") == "gpt"
        is_isohybrid = item.get("fstype") == "iso9660"

        if partitions:
            first_part = partitions[0]
            fstype = first_part.get("fstype", "")
            label = first_part.get("label", "")

        devices.append(
            BlockDevice(
                path=item.get("path") or "",
                size_bytes=size_bytes,
                size_label=format_bytes_as_gib(size_bytes),
                model=str(item.get("model") or "").strip(),
                vendor=str(item.get("vendor") or "").strip(),
                transport=str(item.get("tran") or "").strip(),
                removable=removable,
                read_only=bool(item.get("ro", False)),
                fstype=fstype,
                label=label,
                is_gpt=is_gpt,
                is_isohybrid=is_isohybrid,
                has_tails=has_tails,
                is_big_enough_for_installation=size_gb >= MIN_INSTALLATION_SIZE_GB,
                is_big_enough_for_upgrade=size_gb >= MIN_UPGRADE_SIZE_GB,
            )
        )
    return devices


class DeviceService:
    def __init__(self, run: callable = subprocess.run) -> None:
        self._run = run

    def list_removable_devices(self) -> list[BlockDevice]:
        result = self._run(
            ["lsblk", "--json", "--bytes", "--output", LSBLK_COLUMNS],
            check=True,
            text=True,
            capture_output=True,
        )
        payload = json.loads(result.stdout)
        return parse_lsblk_json(payload)
