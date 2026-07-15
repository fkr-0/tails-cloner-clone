from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_appimage_checksum_is_generated_with_relative_release_asset_name() -> None:
    script = (ROOT / "scripts" / "build_appimage.sh").read_text(encoding="utf-8")

    assert 'cd "$DIST_DIR"' in script
    assert 'sha256sum "$APPIMAGE_NAME" > "$APPIMAGE_NAME.sha256"' in script
    assert 'sha256sum "$DIST_DIR/$APPIMAGE_NAME" > "$DIST_DIR/$APPIMAGE_NAME.sha256"' not in script


def test_appimage_build_uses_local_virtualenv_for_pyinstaller() -> None:
    script = (ROOT / "scripts" / "build_appimage.sh").read_text(encoding="utf-8")

    assert 'VENV_DIR="$BUILD_DIR/venv"' in script
    assert 'python3 -m venv "$VENV_DIR"' in script
    assert '"$VENV_DIR/bin/python" -m pip install pyinstaller' in script
    assert '"$VENV_DIR/bin/pyinstaller"' in script
    assert 'python3 -m pip install pyinstaller' not in script
