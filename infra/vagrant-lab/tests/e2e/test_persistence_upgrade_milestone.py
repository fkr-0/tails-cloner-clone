from __future__ import annotations

import json
import subprocess
from pathlib import Path

STATE_PATH = Path('/opt/tails-cloner-fixtures/fixture-state.json')
REPO_ROOT = Path('/workspace/tails-cloner')
if not REPO_ROOT.exists():
    REPO_ROOT = Path(__file__).resolve().parents[4]


def _run_json(cmd: list[str]) -> dict:
    return json.loads(subprocess.check_output(cmd, text=True))


def _parts(device: str) -> list[dict]:
    data = _run_json(['lsblk', '-J', '-o', 'PATH,FSTYPE,LABEL,TYPE', device])
    parts: list[dict] = []
    for block_device in data.get('blockdevices', []):
        parts.extend(block_device.get('children') or [])
    return parts


def test_primary_upgrade_milestone_uses_real_upgrader() -> None:
    script = REPO_ROOT / 'infra/vagrant-lab/scripts/run_lab_scenario.py'
    assert script.exists()
    text = script.read_text(encoding='utf-8')
    assert 'simulate-internal-upgrade-preserve-persistence' in text
    assert 'from tails_cloner.upgrader import (' in text
    assert 'upgrade_tails_system_partition,' in text
    assert 'upgrade_tails_system_partition(image, target, progress_callback=print)' in text
    assert 'copy_boot_partition_from_image(' not in text
    assert 'image_boot_partition_source(' not in text


def test_upgrade_fixture_models_internal_771_with_persistence() -> None:
    if not STATE_PATH.exists():
        return
    state = json.loads(STATE_PATH.read_text())
    target = state['target_upgrade_disk']
    parts = _parts(target)
    assert any(part.get('label') == 'TAILS' and part.get('fstype') == 'vfat' for part in parts)
    assert any(part.get('label') == 'persistence' and part.get('fstype') == 'ext4' for part in parts)
