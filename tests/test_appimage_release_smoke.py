from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "smoke_appimage_release.py"

spec = importlib.util.spec_from_file_location("smoke_appimage_release", SCRIPT)
assert spec and spec.loader
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


def test_parse_sha256_file_reads_hash_and_filename(tmp_path) -> None:
    checksum = tmp_path / "asset.AppImage.sha256"
    checksum.write_text("abc123  asset.AppImage\n", encoding="utf-8")

    assert smoke.parse_sha256_file(checksum) == ("abc123", "asset.AppImage")


def test_parse_sha256_file_preserves_absolute_path_for_policy_check(tmp_path) -> None:
    checksum = tmp_path / "asset.AppImage.sha256"
    checksum.write_text("abc123  /home/runner/work/project/dist/asset.AppImage\n", encoding="utf-8")

    expected_hash, filename = smoke.parse_sha256_file(checksum)

    assert expected_hash == "abc123"
    assert filename == "/home/runner/work/project/dist/asset.AppImage"
    assert filename != "asset.AppImage"


def test_default_asset_name_uses_release_tag() -> None:
    assert smoke.DEFAULT_ASSET.format(tag="v1.2.3") == "tails-cloner-clone-v1.2.3-x86_64.AppImage"
