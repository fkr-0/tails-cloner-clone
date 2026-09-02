import hashlib
import tomllib
from pathlib import Path

from tails_cloner import __version__

ROOT = Path(__file__).resolve().parents[1]


def test_appimage_checksum_is_generated_with_relative_release_asset_name() -> None:
    script = (ROOT / "scripts" / "build_appimage.sh").read_text(encoding="utf-8")

    assert 'cd "$DIST_DIR"' in script
    assert 'sha256sum "$APPIMAGE_NAME" > "$APPIMAGE_NAME.sha256"' in script
    assert 'sha256sum "$DIST_DIR/$APPIMAGE_NAME" > "$DIST_DIR/$APPIMAGE_NAME.sha256"' not in script


def test_appimage_packages_pinned_tails_signing_key() -> None:
    key = ROOT / "assets/tails-signing-minimal.key"
    spec = (ROOT / "packaging/tails-cloner-clone.spec").read_text(encoding="utf-8")

    assert key.is_file()
    assert hashlib.sha256(key.read_bytes()).hexdigest() == (
        "a2a78b018e2745d36a9ed9e3139b8ebb6d71e8efd65a84554fe9e905e7d6c858"
    )
    assert 'ROOT / "assets" / "tails-signing-minimal.key"' in spec


def test_package_versions_are_converged() -> None:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert metadata["project"]["version"] == __version__ == "0.5.3"


def test_appimage_build_uses_local_virtualenv_for_pyinstaller() -> None:
    script = (ROOT / "scripts" / "build_appimage.sh").read_text(encoding="utf-8")

    assert 'VENV_DIR="$BUILD_DIR/venv"' in script
    assert 'python3 -m venv "$VENV_DIR"' in script
    assert '"$VENV_DIR/bin/python" -m pip install pyinstaller' in script
    assert '"$VENV_DIR/bin/pyinstaller"' in script
    assert 'python3 -m pip install pyinstaller' not in script
