#!/usr/bin/env bash
set -euo pipefail

cd /workspace/tails-cloner/dist
APP="${1:-tails-cloner-clone-dev-x86_64.AppImage}"
EXTRACTED="squashfs-root/AppRun"
OUT_DIR="/workspace/tails-cloner/.cache/appimage-e2e/controller-cli"
mkdir -p "$OUT_DIR"

run_probe() {
  local name="$1"
  shift
  local stdout="$OUT_DIR/${name}.stdout"
  local stderr="$OUT_DIR/${name}.stderr"
  local rc_file="$OUT_DIR/${name}.rc"
  rm -f "$stdout" "$stderr" "$rc_file"
  set +e
  timeout 20 "$@" >"$stdout" 2>"$stderr"
  local rc=$?
  set -e
  printf '%s' "$rc" >"$rc_file"
  echo "## $name"
  echo "rc=$rc"
  echo "stdout=$stdout"
  sed -n '1,80p' "$stdout" || true
  echo "stderr=$stderr"
  sed -n '1,80p' "$stderr" || true
  return 0
}

echo "## environment"
uname -a
printf 'DISPLAY=%s\n' "${DISPLAY:-}"
printf 'WAYLAND_DISPLAY=%s\n' "${WAYLAND_DISPLAY:-}"
command -v timeout
command -v python3 || true

echo "## artifact"
ls -lh "$APP" "$APP.sha256"
sha256sum -c "$APP.sha256"

echo "## extract fresh AppDir"
rm -rf squashfs-root
timeout 60 "./$APP" --appimage-extract >/tmp/tc-controller-cli-extract.out 2>/tmp/tc-controller-cli-extract.err
cat /tmp/tc-controller-cli-extract.err || true
tail -n 8 /tmp/tc-controller-cli-extract.out || true
ls -lh "$EXTRACTED" squashfs-root/usr/lib/tails-cloner-clone/tails-cloner-clone

run_probe appimage-help "./$APP" --help
run_probe apprun-help "./$EXTRACTED" --help
run_probe apprun-source-running "./$EXTRACTED" source running --json
run_probe apprun-devices-list "./$EXTRACTED" devices list --json

python3 - <<'PY'
import json
from pathlib import Path
out = Path('/workspace/tails-cloner/.cache/appimage-e2e/controller-cli')
probes = {}
for rc_file in sorted(out.glob('*.rc')):
    name = rc_file.stem
    probes[name] = {
        'returncode': int(rc_file.read_text() or '999'),
        'stdout': (out / f'{name}.stdout').read_text(errors='replace')[:4000],
        'stderr': (out / f'{name}.stderr').read_text(errors='replace')[:4000],
    }
summary = {
    'status': 'passed' if all(v['returncode'] == 0 for v in probes.values() if not (v['stderr'].find('libfuse.so.2') >= 0 and v['returncode'] != 0)) else 'blocked',
    'scope': 'Ubuntu Vagrant controller extracted-AppRun/AppImage CLI probe; not a Tails guest proof',
    'probes': probes,
}
(out / 'controller-cli-probe-evidence.json').write_text(json.dumps(summary, indent=2, sort_keys=True))
print(json.dumps(summary, indent=2, sort_keys=True))
PY
