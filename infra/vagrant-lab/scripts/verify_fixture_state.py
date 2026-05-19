#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
from pathlib import Path

STATE_PATH = Path('/opt/tails-cloner-fixtures/fixture-state.json')
ARTIFACT_DIR = Path('/opt/tails-cloner-fixtures/tails-images')
REQUIRED_ARTIFACTS = [
    'tails-amd64-7.7.1.img',
    'tails-amd64-7.7.2.img',
]


def run(cmd: list[str]) -> str:
    return subprocess.check_output(cmd, text=True).strip()


def main() -> int:
    if not STATE_PATH.exists():
        raise SystemExit('fixture state missing: run ansible provisioning first')

    data = json.loads(STATE_PATH.read_text())
    upgrade_disk = data['target_upgrade_disk']
    lsblk = run(['lsblk', '-J', upgrade_disk])

    missing = [name for name in REQUIRED_ARTIFACTS if not (ARTIFACT_DIR / name).exists()]
    if missing:
        raise SystemExit(f'missing Tails artifacts: {missing}')

    print('Fixture state loaded')
    print(f'Upgrade disk: {upgrade_disk}')
    print(lsblk)
    print('Required Tails test artifacts are present')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
