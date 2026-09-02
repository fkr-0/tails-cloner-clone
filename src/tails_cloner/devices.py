from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tails_cloner.drive_inspector import has_tails_installation
from tails_cloner.models import BlockDevice

LSBLK_COLUMNS = (
    "NAME,PATH,PKNAME,SIZE,MODEL,VENDOR,SERIAL,WWN,MAJ:MIN,RM,HOTPLUG,TRAN,TYPE,RO,"
    "FSTYPE,LABEL,PARTTYPE,PTTYPE,MOUNTPOINTS"
)
MIN_INSTALLATION_SIZE_GB = 8
MIN_UPGRADE_SIZE_GB = 16
SYSTEM_MOUNTPOINTS = {"/", "/boot", "/boot/efi", "/home", "/usr", "/var"}
PROTECTED_LIVE_MOUNT_PREFIXES = ("/lib/live/mount", "/run/live")
PSEUDO_DISK_PATH = re.compile(r"^/dev/(?:zram|ram)\d+$")


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


def _is_protected_mountpoint(mountpoint: str) -> bool:
    return mountpoint in SYSTEM_MOUNTPOINTS or any(
        mountpoint == prefix or mountpoint.startswith(f"{prefix}/")
        for prefix in PROTECTED_LIVE_MOUNT_PREFIXES
    )


def format_bytes_as_gib(size_bytes: int) -> str:
    gib = size_bytes / (1024**3)
    return f"{gib:.1f} GiB"


RunCommand = Callable[..., subprocess.CompletedProcess[str]]


def find_stable_device_path(
    device_path: str,
    by_id_dir: Path = Path("/dev/disk/by-id"),
) -> str:
    """Return a whole-device /dev/disk/by-id alias when one is available."""
    try:
        resolved_device = Path(device_path).resolve(strict=True)
    except OSError:
        return ""
    if not by_id_dir.is_dir():
        return ""

    candidates: list[Path] = []
    try:
        entries = list(by_id_dir.iterdir())
    except OSError:
        return ""
    for candidate in entries:
        if re.search(r"-part\d+$", candidate.name):
            continue
        try:
            if candidate.resolve(strict=True) == resolved_device:
                candidates.append(candidate)
        except OSError:
            continue

    if not candidates:
        return ""

    def preference(candidate: Path) -> tuple[int, str]:
        name = candidate.name.casefold()
        if name.startswith(("wwn-", "nvme-eui.", "nvme-uuid.")):
            return (0, name)
        if name.startswith(("usb-", "ata-", "scsi-", "nvme-")):
            return (1, name)
        return (2, name)

    return str(min(candidates, key=preference))


def parse_lsblk_json(payload: dict[str, Any]) -> list[BlockDevice]:
    devices: list[BlockDevice] = []
    for raw_item in payload.get("blockdevices", []):
        if not isinstance(raw_item, dict):
            continue
        item: dict[str, Any] = raw_item
        if item.get("type") != "disk":
            continue
        path = str(item.get("path") or "")
        if PSEUDO_DISK_PATH.fullmatch(path):
            continue

        removable = bool(item.get("rm") or item.get("hotplug"))
        size_bytes = int(item.get("size") or 0)
        size_gb = size_bytes / (1024**3)

        children = item.get("children") or []
        child_nodes = [child for child in children if isinstance(child, dict)] if isinstance(children, list) else []
        all_descendants = _walk_nodes(child_nodes)
        partitions = [child for child in all_descendants if child.get("type") == "part"]
        is_host_system_device = any(
            any(_is_protected_mountpoint(mountpoint) for mountpoint in _mountpoints(node))
            for node in all_descendants
        )

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
                path=path,
                size_bytes=size_bytes,
                size_label=format_bytes_as_gib(size_bytes),
                model=str(item.get("model") or "").strip(),
                vendor=str(item.get("vendor") or "").strip(),
                transport=str(item.get("tran") or "").strip(),
                removable=removable,
                serial=str(item.get("serial") or "").strip(),
                wwn=str(item.get("wwn") or "").strip(),
                major_minor=str(item.get("maj:min") or "").strip(),
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
        devices = parse_lsblk_json(payload)
        for device in devices:
            device.stable_path = find_stable_device_path(device.path)
        return devices

    def list_removable_devices(self) -> list[BlockDevice]:
        """Backward-compatible alias; this now returns all disk devices."""
        return self.list_devices()
