from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "infra" / "vagrant-lab" / "real-boot-lane" / "prepare_appimage_guest_smoke_share.py"

spec = importlib.util.spec_from_file_location("prepare_appimage_guest_smoke_share", SCRIPT)
assert spec and spec.loader
share = importlib.util.module_from_spec(spec)
spec.loader.exec_module(share)


def test_prepare_appimage_guest_smoke_share(tmp_path) -> None:
    appimage = tmp_path / "tails-cloner-clone-test-x86_64.AppImage"
    appimage.write_text("fake-appimage", encoding="utf-8")
    sha = tmp_path / "tails-cloner-clone-test-x86_64.AppImage.sha256"
    sha.write_text("abc123  tails-cloner-clone-test-x86_64.AppImage\n", encoding="utf-8")
    output = tmp_path / "share"

    share.prepare_share(output_dir=output, appimage=appimage, tag="tcapp", mount_point="/mnt/tcapp")

    assert (output / appimage.name).exists()
    assert (output / sha.name).exists()
    run_script = (output / "run_appimage_guest_smoke.sh").read_text(encoding="utf-8")
    readme = (output / "README.md").read_text(encoding="utf-8")
    assert "TAILS_CLONER_APPIMAGE_SMOKE=" in run_script
    assert "source running --json" in run_script
    assert "devices list --json" in run_script
    assert "sha256sum -c" in run_script
    assert "tcapp" in readme
    assert "/mnt/tcapp" in readme
