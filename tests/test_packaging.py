from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_appimage_checksum_is_generated_with_relative_release_asset_name() -> None:
    script = (ROOT / "scripts" / "build_appimage.sh").read_text(encoding="utf-8")

    assert 'cd "$DIST_DIR"' in script
    assert 'sha256sum "$APPIMAGE_NAME" > "$APPIMAGE_NAME.sha256"' in script
    assert 'sha256sum "$DIST_DIR/$APPIMAGE_NAME" > "$DIST_DIR/$APPIMAGE_NAME.sha256"' not in script
