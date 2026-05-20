#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAB_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
APP_REL="${1:-dist/tails-cloner-clone-dev-x86_64.AppImage}"
APP_NAME="$(basename "$APP_REL")"
SHA_NAME="$APP_NAME.sha256"

cd "$LAB_DIR"
timeout 120 scripts/vagrant_safe.sh ssh controller -c "set -euo pipefail
cd /workspace/tails-cloner
if [ ! -f '$APP_REL' ]; then
  echo 'missing AppImage: $APP_REL' >&2
  exit 1
fi
if [ ! -f '$(dirname "$APP_REL")/$SHA_NAME' ]; then
  echo 'missing sha256 file: $(dirname "$APP_REL")/$SHA_NAME' >&2
  exit 1
fi
cd '$(dirname "$APP_REL")'
echo '## checksum'
sha256sum -c '$SHA_NAME'
echo '## direct execution probe'
set +e
timeout 20 './$APP_NAME' --help >/tmp/tc-appimage-help.out 2>/tmp/tc-appimage-help.err
direct_rc=\$?
set -e
cat /tmp/tc-appimage-help.out || true
cat /tmp/tc-appimage-help.err || true
echo direct_returncode=\$direct_rc
if grep -q 'libfuse.so.2' /tmp/tc-appimage-help.err; then
  echo direct_execution_requires_fuse=true
else
  echo direct_execution_requires_fuse=false
fi
echo '## extract'
rm -rf squashfs-root
timeout 60 './$APP_NAME' --appimage-extract >/tmp/tc-appimage-extract.out 2>/tmp/tc-appimage-extract.err
cat /tmp/tc-appimage-extract.err || true
tail -n 8 /tmp/tc-appimage-extract.out || true
test -d squashfs-root
echo extract_ok=true
"
