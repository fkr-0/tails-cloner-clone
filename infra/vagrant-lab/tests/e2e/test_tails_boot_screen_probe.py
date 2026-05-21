from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "infra" / "vagrant-lab" / "real-boot-lane" / "probe_tails_boot_screen.py"

spec = importlib.util.spec_from_file_location("probe_tails_boot_screen", SCRIPT)
assert spec and spec.loader
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def test_insert_boot_usb_adds_usb_before_timeout() -> None:
    command = ["bash", "boot_tails_qemu.sh", "--headless", "--timeout", "30", "tails.img"]

    result = probe.insert_boot_usb(command.copy())

    assert "--boot-usb" in result
    assert result.index("--boot-usb") < result.index("--timeout")


def test_insert_boot_usb_is_idempotent() -> None:
    command = ["bash", "boot_tails_qemu.sh", "--boot-usb", "--timeout", "30", "tails.img"]

    result = probe.insert_boot_usb(command.copy())

    assert result.count("--boot-usb") == 1


def test_parse_waits_sorts_positive_values() -> None:
    assert probe.parse_waits("90,30,60") == [30, 60, 90]
