from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from typing import Any

from tails_cloner.drive_inspector import has_tails_installation
from tails_cloner.models import BlockDevice

LSBLK_COLUMNS = "PATH,SIZE,MODEL,VENDOR,RM,HOTPLUG,TRAN,TYPE,RO,FSTYPE,LABEL,PARTTYPE,PTTYPE,MOUNTPOINTS"
MIN_INSTALLATION_SIZE_GB = 8
MIN_UPGRADE_SIZE_GB = 16
SYSTEM_MOUNTPOINTS = {"/", "/boot", "/boot/efi", "/usr", "/var"}


def _walk_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for node in nodes:
        result.append(node)
        children = node.get("children") or []
        if isinstance(children, list):
            result.extend(_walk_nodes([child for child in children if isinstance(child, dict)]))
    return result


def _mountpoints(node: dict[str, Any]) -> set[str]:
    value = node.get("mountpoints")
    if isinstance(value, list):
        return {entry for entry in value if isinstance(entry, str)}
    if isinstance(value, str):
        return {value}
    return set()


def format_bytes_as_gib(size_bytes: int) -> str:
    gib = size_bytes / (1024**3)
    return f"{gib:.1f} GiB"


RunCommand = Callable[..., subprocess.CompletedProcess[str]]


def parse_lsblk_json(payload: dict[str, Any]) -> list[BlockDevice]:
    devices: list[BlockDevice] = []
    for raw_item in payload.get("blockdevices", []):
        if not isinstance(raw_item, dict):
            continue
        item: dict[str, Any] = raw_item
        if item.get("type") != "disk":
            continue

        removable = bool(item.get("rm") or item.get("hotplug"))
        size_bytes = int(item.get("size") or 0)
        size_gb = size_bytes / (1024**3)

        children = item.get("children") or []
        child_nodes = [child for child in children if isinstance(child, dict)] if isinstance(children, list) else []
        all_descendants = _walk_nodes(child_nodes)
        partitions = [child for child in all_descendants if child.get("type") == "part"]
        is_host_system_device = any(_mountpoints(node) & SYSTEM_MOUNTPOINTS for node in all_descendants)

        # Detect Tails installation
        has_tails = has_tails_installation(item, partitions)

        # Get filesystem info from first partition if available
        fstype = ""
        label = ""
        is_gpt = item.get("pttype") == "gpt"
        is_isohybrid = item.get("fstype") == "iso9660"

        if partitions:
            first_part = partitions[0]
            fstype = str(first_part.get("fstype") or "")
            label = str(first_part.get("label") or "")

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
                is_host_system_device=is_host_system_device,
                disabled_reason=(
                    "This device contains filesystems used by the currently running operating system."
                    if is_host_system_device
                    else ""
                ),
            )
        )
    return devices


class DeviceService:
    def __init__(self, run: RunCommand = subprocess.run) -> None:
        self._run = run

    def list_devices(self) -> list[BlockDevice]:
        result = self._run(
            ["lsblk", "--json", "--bytes", "--output", LSBLK_COLUMNS],
            check=True,
            text=True,
            capture_output=True,
        )
        payload = json.loads(result.stdout)
        if not isinstance(payload, dict):
            raise RuntimeError("lsblk returned a non-object JSON payload")
        return parse_lsblk_json(payload)

    def list_removable_devices(self) -> list[BlockDevice]:
        """Backward-compatible alias; this now returns all disk devices."""
        return self.list_devices()
