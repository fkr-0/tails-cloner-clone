from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "infra" / "vagrant-lab" / "scripts" / "report_appimage_tails_smoke_readiness.py"

spec = importlib.util.spec_from_file_location("report_appimage_tails_smoke_readiness", SCRIPT)
assert spec and spec.loader
readiness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(readiness)


def test_command_catalog_contains_bridge_command() -> None:
    assert readiness.command_catalog_contains("appimage-release-smoke")
    assert readiness.command_catalog_contains("appimage-local-smoke")
    assert readiness.command_catalog_contains("appimage-tails-smoke-readiness")
    assert readiness.command_catalog_contains("appimage-controller-smoke")
    assert readiness.command_catalog_contains("appimage-controller-cli-probe")


def test_cli_helpers_are_present() -> None:
    assert readiness.cli_contains("devices")
    assert readiness.cli_contains("running")
    assert readiness.cli_contains("validate-attached")
    assert readiness.cli_contains("plan")


def test_latest_local_appimage_returns_none_or_appimage_path() -> None:
    result = readiness.latest_local_appimage()
    assert result is None or result.name.endswith("x86_64.AppImage")
