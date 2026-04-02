from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence

from tails_cloner.models import BlockDevice


LSBLK_COLUMNS = "PATH,SIZE,MODEL,VENDOR,RM,HOTPLUG,TRAN,TYPE"



def format_bytes_as_gib(size_bytes: int) -> str:
    gib = size_bytes / (1024**3)
    return f"{gib:.1f} GiB"



def parse_lsblk_json(payload: dict) -> list[BlockDevice]:
    devices: list[BlockDevice] = []
    for item in payload.get("blockdevices", []):
        if item.get("type") != "disk":
            continue
        removable = bool(item.get("rm") or item.get("hotplug"))
        if not removable:
            continue
        size_bytes = int(item.get("size") or 0)
        devices.append(
            BlockDevice(
                path=item.get("path") or "",
                size_bytes=size_bytes,
                size_label=format_bytes_as_gib(size_bytes),
                model=str(item.get("model") or "").strip(),
                vendor=str(item.get("vendor") or "").strip(),
                transport=str(item.get("tran") or "").strip(),
                removable=removable,
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
