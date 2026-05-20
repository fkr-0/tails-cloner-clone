#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

LANE_DIR = Path(__file__).resolve().parent
REPO_ROOT = LANE_DIR.parents[2]
DEFAULT_OUTPUT = LANE_DIR / 'out' / 'appimage-guest-smoke-share'
DEFAULT_MOUNT_POINT = '/mnt/tailscloner-appimage'
DEFAULT_TAG = 'tailsclonerappimage'
DEFAULT_APPIMAGE = REPO_ROOT / 'dist' / 'tails-cloner-clone-dev-x86_64.AppImage'


def write_run_script(output_dir: Path, *, tag: str, mount_point: str, appimage_name: str) -> None:
    script = f'''#!/usr/bin/env bash
set -euo pipefail

TAG="${{1:-{tag}}}"
MOUNT_POINT="${{2:-{mount_point}}}"
SERIAL_DEVICE="${{TAILS_CLONER_SERIAL_DEVICE:-/dev/ttyS0}}"
APPIMAGE_NAME="${{TAILS_CLONER_APPIMAGE_NAME:-{appimage_name}}}"
WORK_DIR="${{TAILS_CLONER_APPIMAGE_WORK_DIR:-/tmp/tails-cloner-appimage-smoke}}"

sudo mkdir -p "$MOUNT_POINT"
if ! findmnt -rno TARGET "$MOUNT_POINT" >/dev/null 2>&1; then
  sudo mount -t 9p -o trans=virtio,version=9p2000.L,ro "$TAG" "$MOUNT_POINT"
fi

rm -rf "$WORK_DIR"
mkdir -p "$WORK_DIR"
cp "$MOUNT_POINT/$APPIMAGE_NAME" "$WORK_DIR/$APPIMAGE_NAME"
cp "$MOUNT_POINT/$APPIMAGE_NAME.sha256" "$WORK_DIR/$APPIMAGE_NAME.sha256"
chmod +x "$WORK_DIR/$APPIMAGE_NAME"
cd "$WORK_DIR"

run_probe() {{
  local name="$1"
  shift
  local stdout="$WORK_DIR/${{name}}.stdout"
  local stderr="$WORK_DIR/${{name}}.stderr"
  local rc_file="$WORK_DIR/${{name}}.rc"
  rm -f "$stdout" "$stderr" "$rc_file"
  set +e
  timeout 45 "$@" >"$stdout" 2>"$stderr"
  local rc=$?
  set -e
  printf '%s' "$rc" >"$rc_file"
  return 0
}}

sha256sum -c "$APPIMAGE_NAME.sha256" > checksum.stdout 2> checksum.stderr
rm -rf squashfs-root
run_probe direct-help "./$APPIMAGE_NAME" --help
run_probe extract "./$APPIMAGE_NAME" --appimage-extract
if [ -x squashfs-root/AppRun ]; then
  run_probe apprun-help ./squashfs-root/AppRun --help
  run_probe source-running ./squashfs-root/AppRun source running --json
  run_probe devices-list ./squashfs-root/AppRun devices list --json
else
  printf '999' > apprun-help.rc
  printf 'missing squashfs-root/AppRun' > apprun-help.stderr
  printf '999' > source-running.rc
  printf 'missing squashfs-root/AppRun' > source-running.stderr
  printf '999' > devices-list.rc
  printf 'missing squashfs-root/AppRun' > devices-list.stderr
fi

python3 - <<'PY' | sudo tee "$SERIAL_DEVICE"
import json
from pathlib import Path

work = Path.cwd()

def read_text(path: Path) -> str:
    return path.read_text(errors='replace') if path.exists() else ''

def probe(name: str) -> dict:
    rc_text = read_text(work / f'{{name}}.rc') or '999'
    try:
        rc = int(rc_text.strip())
    except ValueError:
        rc = 999
    return {{
        'returncode': rc,
        'stdout': read_text(work / f'{{name}}.stdout')[:4000],
        'stderr': read_text(work / f'{{name}}.stderr')[:4000],
    }}

source = probe('source-running')
devices = probe('devices-list')
running_payload = {{}}
devices_payload = {{}}
try:
    running_payload = json.loads(source['stdout']) if source['stdout'].strip().startswith('{{') else {{}}
except json.JSONDecodeError:
    running_payload = {{}}
try:
    devices_payload = json.loads(devices['stdout']) if devices['stdout'].strip().startswith('{{') else {{}}
except json.JSONDecodeError:
    devices_payload = {{}}

running_parent = running_payload.get('parent_device') or ''
device_rows = devices_payload.get('devices') or []
running_device_rows = [row for row in device_rows if row.get('path') == running_parent]

summary = {{
    'status': 'passed' if source['returncode'] == 0 and devices['returncode'] == 0 and probe('apprun-help')['returncode'] == 0 else 'failed',
    'scope': 'Tails guest AppImage smoke via 9p share',
    'checksum': {{
        'stdout': read_text(work / 'checksum.stdout'),
        'stderr': read_text(work / 'checksum.stderr'),
    }},
    'direct_execution_requires_fuse': 'libfuse.so.2' in probe('direct-help')['stderr'],
    'running_tails_available': bool(running_payload.get('running_tails_available')),
    'running_parent_device': running_parent,
    'running_parent_visible': bool(running_device_rows),
    'running_parent_selectable': running_device_rows[0].get('selectable') if running_device_rows else None,
    'device_count': len(device_rows),
    'probes': {{
        'direct-help': probe('direct-help'),
        'extract': probe('extract'),
        'apprun-help': probe('apprun-help'),
        'source-running': source,
        'devices-list': devices,
    }},
}}
print('TAILS_CLONER_APPIMAGE_SMOKE=' + json.dumps(summary, sort_keys=True))
PY
'''
    run_path = output_dir / 'run_appimage_guest_smoke.sh'
    run_path.write_text(script, encoding='utf-8')
    run_path.chmod(0o755)


def write_readme(output_dir: Path, *, tag: str, mount_point: str, appimage_name: str) -> None:
    readme = f'''# tails-cloner AppImage guest smoke share

Expose this directory to a QEMU-booted Tails guest with:

    --share-dir {output_dir},{tag}

Inside the Tails guest, run:

    sudo mkdir -p {mount_point}
    sudo mount -t 9p -o trans=virtio,version=9p2000.L,ro {tag} {mount_point}
    {mount_point}/run_appimage_guest_smoke.sh

The script copies `{appimage_name}` and its `.sha256` file to `/tmp`, verifies the checksum, extracts the AppImage if direct execution needs FUSE, runs read-only CLI probes, and emits a serial marker:

    TAILS_CLONER_APPIMAGE_SMOKE={{...json...}}

The marker is meant to be captured from `/dev/ttyS0` just like the existing guest probe marker.
'''
    (output_dir / 'README.md').write_text(readme, encoding='utf-8')


def prepare_share(output_dir: Path, *, appimage: Path, tag: str, mount_point: str) -> Path:
    if not appimage.exists():
        raise FileNotFoundError(f'AppImage not found: {appimage}')
    sha_file = appimage.with_name(f'{appimage.name}.sha256')
    if not sha_file.exists():
        raise FileNotFoundError(f'Checksum file not found: {sha_file}')
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(appimage, output_dir / appimage.name)
    shutil.copy2(sha_file, output_dir / sha_file.name)
    (output_dir / appimage.name).chmod(0o755)
    write_run_script(output_dir, tag=tag, mount_point=mount_point, appimage_name=appimage.name)
    write_readme(output_dir, tag=tag, mount_point=mount_point, appimage_name=appimage.name)
    return output_dir


def main() -> int:
    parser = argparse.ArgumentParser(description='Prepare a read-only 9p AppImage guest smoke share for Tails real-boot tests.')
    parser.add_argument('--output-dir', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--appimage', type=Path, default=DEFAULT_APPIMAGE)
    parser.add_argument('--tag', default=DEFAULT_TAG)
    parser.add_argument('--mount-point', default=DEFAULT_MOUNT_POINT)
    args = parser.parse_args()
    output = prepare_share(output_dir=args.output_dir, appimage=args.appimage, tag=args.tag, mount_point=args.mount_point)
    print(output)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
