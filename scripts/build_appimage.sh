#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${1:-${GITHUB_REF_NAME:-dev}}"
DIST_DIR="$ROOT_DIR/dist"
BUILD_DIR="$ROOT_DIR/build/appimage"
APPDIR="$BUILD_DIR/AppDir"
APPIMAGE_TOOL="$BUILD_DIR/appimagetool-x86_64.AppImage"
APPIMAGE_NAME="tails-cloner-clone-${VERSION}-x86_64.AppImage"

rm -rf "$BUILD_DIR" "$DIST_DIR/tails-cloner-clone" "$DIST_DIR"/*.AppImage
mkdir -p "$BUILD_DIR" "$DIST_DIR" "$APPDIR/usr/lib" "$APPDIR/usr/share/icons/hicolor/scalable/apps"
for size in 16 24 32 48 64 128 256 512; do
  mkdir -p "$APPDIR/usr/share/icons/hicolor/${size}x${size}/apps"
done

VENV_DIR="$BUILD_DIR/venv"
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/python" -m pip install pyinstaller
"$VENV_DIR/bin/pyinstaller" "$ROOT_DIR/packaging/tails-cloner-clone.spec" --noconfirm --clean

cp -r "$DIST_DIR/tails-cloner-clone" "$APPDIR/usr/lib/tails-cloner-clone"

cat > "$APPDIR/AppRun" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$HERE/usr/lib/tails-cloner-clone/tails-cloner-clone" "$@"
EOF
chmod +x "$APPDIR/AppRun"

cat > "$APPDIR/tails-cloner-clone.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=Tails Cloner Clone
Comment=Standalone GUI app for safely installing or upgrading Tails on selected block devices
Exec=tails-cloner-clone
Icon=tails-cloner-clone
StartupWMClass=tails-cloner-clone
Categories=Utility;System;
Terminal=false
EOF

cp "$ROOT_DIR/assets/tails-cloner-clone.svg" "$APPDIR/tails-cloner-clone.svg"
cp "$ROOT_DIR/assets/tails-cloner-clone.svg" "$APPDIR/usr/share/icons/hicolor/scalable/apps/tails-cloner-clone.svg"
cp "$ROOT_DIR/assets/tails-cloner-clone-256.png" "$APPDIR/.DirIcon"
for size in 16 24 32 48 64 128 256 512; do
  cp "$ROOT_DIR/assets/tails-cloner-clone-${size}.png" "$APPDIR/usr/share/icons/hicolor/${size}x${size}/apps/tails-cloner-clone.png"
done

curl -fsSL -o "$APPIMAGE_TOOL" https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage
chmod +x "$APPIMAGE_TOOL"
ARCH=x86_64 "$APPIMAGE_TOOL" --appimage-extract-and-run "$APPDIR" "$DIST_DIR/$APPIMAGE_NAME"
(
  cd "$DIST_DIR"
  sha256sum "$APPIMAGE_NAME" > "$APPIMAGE_NAME.sha256"
)
printf '%s\n' "$DIST_DIR/$APPIMAGE_NAME"
