#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
DIST = ROOT / "dist"
APPIMAGE_E2E_PLAN = ROOT / "docs" / "appimage-vagrant-e2e-plan.md"
SMOKE_SCRIPT = ROOT / "scripts" / "smoke_appimage_release.py"
CLI_MODULE = ROOT / "src" / "tails_cloner" / "cli.py"
BRIDGE = ROOT / "bridge.yml"
VAGRANT_DIR = ROOT / "infra" / "vagrant-lab"
VAGRANT_SAFE = VAGRANT_DIR / "scripts" / "vagrant_safe.sh"


def run(command: list[str], *, cwd: Path = ROOT, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=str(cwd),
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def latest_local_appimage() -> Path | None:
    candidates = sorted(DIST.glob("tails-cloner-clone-*-x86_64.AppImage"))
    return candidates[-1] if candidates else None


def command_catalog_contains(name: str) -> bool:
    if not BRIDGE.exists():
        return False
    return f"  {name}:" in BRIDGE.read_text(encoding="utf-8")


def cli_contains(command: str) -> bool:
    if not CLI_MODULE.exists():
        return False
    return command in CLI_MODULE.read_text(encoding="utf-8")


def vagrant_status() -> dict[str, Any]:
    if not VAGRANT_SAFE.exists():
        return {"available": False, "reason": "vagrant_safe.sh missing"}
    if not shutil.which("vagrant"):
        return {"available": False, "reason": "vagrant executable missing"}
    result = run([str(VAGRANT_SAFE), "status"], cwd=VAGRANT_DIR, timeout=60)
    return {
        "available": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": result.stdout[-4000:],
        "stderr": result.stderr[-4000:],
    }


def main() -> int:
    local_appimage = latest_local_appimage()
    local_sha = local_appimage.with_name(f"{local_appimage.name}.sha256") if local_appimage else None
    payload: dict[str, Any] = {
        "status": "ready-for-manual-vm-smoke",
        "scope": "readiness only; does not boot or mutate Vagrant/Tails VMs",
        "docs": {
            "appimage_vagrant_e2e_plan_exists": APPIMAGE_E2E_PLAN.exists(),
            "path": str(APPIMAGE_E2E_PLAN),
        },
        "host_smoke": {
            "script_exists": SMOKE_SCRIPT.exists(),
            "release_command_present": command_catalog_contains("appimage-release-smoke"),
            "local_command_present": command_catalog_contains("appimage-local-smoke"),
            "controller_smoke_command_present": command_catalog_contains("appimage-controller-smoke"),
            "controller_cli_probe_command_present": command_catalog_contains("appimage-controller-cli-probe"),
        },
        "local_artifact": {
            "appimage": str(local_appimage or ""),
            "appimage_exists": bool(local_appimage and local_appimage.exists()),
            "sha256_file": str(local_sha or ""),
            "sha256_file_exists": bool(local_sha and local_sha.exists()),
        },
        "cli_helpers": {
            "devices_list": cli_contains("devices"),
            "source_running": cli_contains("running"),
            "source_validate_attached": cli_contains("validate-attached"),
            "plan_install_upgrade": cli_contains("plan"),
        },
        "vagrant": vagrant_status(),
        "next_actions": [
            "build-appimage",
            "appimage-local-smoke",
            "boot or reuse a Tails VM in the Vagrant/real-boot lane",
            "copy the AppImage and .sha256 into the guest",
            "run AppImage --help, source running --json, devices list --json, and plan refusal checks inside Tails",
        ],
    }
    blocking = []
    if not payload["docs"]["appimage_vagrant_e2e_plan_exists"]:
        blocking.append("missing AppImage Vagrant e2e plan")
    if not payload["host_smoke"]["script_exists"]:
        blocking.append("missing AppImage smoke script")
    if not payload["host_smoke"]["local_command_present"]:
        blocking.append("missing appimage-local-smoke bridge command")
    if not payload["host_smoke"]["controller_smoke_command_present"]:
        blocking.append("missing appimage-controller-smoke bridge command")
    if not payload["host_smoke"]["controller_cli_probe_command_present"]:
        blocking.append("missing appimage-controller-cli-probe bridge command")
    if not all(payload["cli_helpers"].values()):
        blocking.append("missing one or more CLI helper commands")
    payload["blocking_readiness_issues"] = blocking
    if blocking:
        payload["status"] = "not-ready"

    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if not blocking else 1


if __name__ == "__main__":
    raise SystemExit(main())
