#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${1:-${GITHUB_REF_NAME:-dev}}"
DIST_DIR="$ROOT_DIR/dist"
BUILD_DIR="$ROOT_DIR/build/appimage"
APPDIR="$BUILD_DIR/AppDir"
APPIMAGE_TOOL="$BUILD_DIR/appimagetool-x86_64.AppImage"
APPIMAGE_NAME="tails-cloner-${VERSION}-x86_64.AppImage"

rm -rf "$BUILD_DIR" "$DIST_DIR/tails-cloner" "$DIST_DIR"/*.AppImage
mkdir -p "$BUILD_DIR" "$DIST_DIR" "$APPDIR/usr/lib" "$APPDIR/usr/share/icons/hicolor/scalable/apps"

python3 -m pip install --upgrade pip
python3 -m pip install pyinstaller
pyinstaller "$ROOT_DIR/packaging/tails-cloner.spec" --noconfirm --clean

cp -r "$DIST_DIR/tails-cloner" "$APPDIR/usr/lib/tails-cloner"

cat > "$APPDIR/AppRun" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$HERE/usr/lib/tails-cloner/tails-cloner" "$@"
EOF
chmod +x "$APPDIR/AppRun"

cat > "$APPDIR/tails-cloner.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=Tails Cloner
Comment=Refreshed standalone GUI app for cloning Tails images onto removable devices
Exec=tails-cloner
Icon=tails-cloner
Categories=Utility;System;
Terminal=false
EOF

cp "$ROOT_DIR/assets/tails-cloner.svg" "$APPDIR/tails-cloner.svg"
cp "$ROOT_DIR/assets/tails-cloner.svg" "$APPDIR/usr/share/icons/hicolor/scalable/apps/tails-cloner.svg"

curl -fsSL -o "$APPIMAGE_TOOL" https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage
chmod +x "$APPIMAGE_TOOL"
ARCH=x86_64 "$APPIMAGE_TOOL" --appimage-extract-and-run "$APPDIR" "$DIST_DIR/$APPIMAGE_NAME"
sha256sum "$DIST_DIR/$APPIMAGE_NAME" > "$DIST_DIR/$APPIMAGE_NAME.sha256"
printf '%s\n' "$DIST_DIR/$APPIMAGE_NAME"
