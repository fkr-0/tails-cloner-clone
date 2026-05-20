from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "infra" / "vagrant-lab" / "real-boot-lane" / "validate_appimage_guest_smoke_output.py"

spec = importlib.util.spec_from_file_location("validate_appimage_guest_smoke_output", SCRIPT)
assert spec and spec.loader
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)


def valid_payload(**overrides):
    payload = {
        "status": "passed",
        "scope": "Tails guest AppImage smoke via 9p share",
        "device_count": 3,
        "running_tails_available": True,
        "running_parent_device": "/dev/sdb",
        "running_parent_visible": True,
        "running_parent_selectable": False,
        "probes": {
            "apprun-help": {"returncode": 0},
            "source-running": {"returncode": 0},
            "devices-list": {"returncode": 0},
        },
    }
    payload.update(overrides)
    return payload


def test_extract_marker_reads_last_marker() -> None:
    first = valid_payload(device_count=1)
    second = valid_payload(device_count=2)
    log = "noise\nTAILS_CLONER_APPIMAGE_SMOKE=" + json.dumps(first) + "\nmore\nTAILS_CLONER_APPIMAGE_SMOKE=" + json.dumps(second)

    assert validator.extract_marker(log)["device_count"] == 2


def test_validate_payload_accepts_running_source_visible_but_disabled() -> None:
    assert validator.validate_payload(valid_payload()) == []


def test_validate_payload_rejects_running_source_selectable() -> None:
    errors = validator.validate_payload(valid_payload(running_parent_selectable=True))

    assert any("not marked non-selectable" in error for error in errors)


def test_validate_payload_allows_non_tails_environment_for_controller_like_logs() -> None:
    payload = valid_payload(
        running_tails_available=False,
        running_parent_device="",
        running_parent_visible=False,
        running_parent_selectable=None,
    )

    assert validator.validate_payload(payload) == []
