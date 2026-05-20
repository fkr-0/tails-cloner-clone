#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

LANE_DIR = Path(__file__).resolve().parent
REPO_ROOT = LANE_DIR.parents[2]
GUEST_PROBE = LANE_DIR / 'guest_probe.py'
DEFAULT_OUTPUT = LANE_DIR / 'out' / 'guest-probe-share'
DEFAULT_MOUNT_POINT = '/mnt/tailscloner'
DEFAULT_TAG = 'tailscloner'


def write_run_script(output_dir: Path, *, scenario_variant: str, tag: str, mount_point: str) -> None:
    script = f'''#!/usr/bin/env bash
set -euo pipefail

TAG="${{1:-{tag}}}"
MOUNT_POINT="${{2:-{mount_point}}}"
SCENARIO_VARIANT="${{3:-{scenario_variant}}}"
SERIAL_DEVICE="${{TAILS_CLONER_SERIAL_DEVICE:-/dev/ttyS0}}"

sudo mkdir -p "$MOUNT_POINT"
if ! findmnt -rno TARGET "$MOUNT_POINT" >/dev/null 2>&1; then
  sudo mount -t 9p -o trans=virtio,version=9p2000.L,ro "$TAG" "$MOUNT_POINT"
fi

python3 "$MOUNT_POINT/guest_probe.py" \
  --scenario-variant "$SCENARIO_VARIANT" \
  --transport serial_marker \
  --project-path "$MOUNT_POINT/project" \
  --prefix | sudo tee "$SERIAL_DEVICE"
'''
    run_path = output_dir / 'run_guest_probe.sh'
    run_path.write_text(script, encoding='utf-8')
    run_path.chmod(0o755)


def write_readme(output_dir: Path, *, scenario_variant: str, tag: str, mount_point: str) -> None:
    readme = f'''# tails-cloner real-boot guest probe share

This directory is meant to be exposed to a QEMU-booted Tails guest with:

    boot_tails_qemu.sh --share-dir {output_dir},{tag} --serial-log serial.log ...

Inside the Tails guest, run:

    {mount_point}/run_guest_probe.sh

If the share is not mounted yet, run this once first:

    sudo mkdir -p {mount_point}
    sudo mount -t 9p -o trans=virtio,version=9p2000.L,ro {tag} {mount_point}
    {mount_point}/run_guest_probe.sh

The script emits a line beginning with:

    TAILS_CLONER_GUEST_PROBE=

The host validates the serial log with:

    python3 infra/vagrant-lab/real-boot-lane/validate_guest_probe_output.py --log-file serial.log

Defaults:

- scenario_variant: {scenario_variant}
- mount_tag: {tag}
- mount_point: {mount_point}
- serial_device: /dev/ttyS0, override with TAILS_CLONER_SERIAL_DEVICE
'''
    (output_dir / 'README.md').write_text(readme, encoding='utf-8')


def prepare_share(output_dir: Path, *, scenario_variant: str, tag: str, mount_point: str, project_path: Path) -> Path:
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(GUEST_PROBE, output_dir / 'guest_probe.py')
    project_marker = output_dir / 'project' / 'src' / 'tails_cloner'
    project_marker.mkdir(parents=True, exist_ok=True)
    (project_marker / '__init__.py').write_text('', encoding='utf-8')
    (output_dir / 'project' / 'PROJECT_SOURCE.txt').write_text(str(project_path.resolve()) + '\n', encoding='utf-8')
    write_run_script(output_dir, scenario_variant=scenario_variant, tag=tag, mount_point=mount_point)
    write_readme(output_dir, scenario_variant=scenario_variant, tag=tag, mount_point=mount_point)
    return output_dir


def main() -> int:
    parser = argparse.ArgumentParser(description='Prepare a read-only 9p guest-probe share for Tails real-boot tests.')
    parser.add_argument('--output-dir', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--scenario-variant', default='running-live-install')
    parser.add_argument('--tag', default=DEFAULT_TAG)
    parser.add_argument('--mount-point', default=DEFAULT_MOUNT_POINT)
    parser.add_argument('--project-path', type=Path, default=REPO_ROOT)
    args = parser.parse_args()
    output = prepare_share(
        output_dir=args.output_dir,
        scenario_variant=args.scenario_variant,
        tag=args.tag,
        mount_point=args.mount_point,
        project_path=args.project_path,
    )
    print(output)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
