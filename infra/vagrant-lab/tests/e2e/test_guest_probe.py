from __future__ import annotations

import json
import subprocess
from pathlib import Path

REPO_ROOT = Path('/workspace/tails-cloner')
if not REPO_ROOT.exists():
    REPO_ROOT = Path(__file__).resolve().parents[4]

GUEST_PROBE = REPO_ROOT / 'infra/vagrant-lab/real-boot-lane/guest_probe.py'
PROBE_PREFIX = 'TAILS_CLONER_GUEST_PROBE='


def test_guest_probe_emits_contract_shaped_json(tmp_path: Path) -> None:
    live_dir = tmp_path / 'live'
    live_dir.mkdir()
    version_file = live_dir / 'Tails.version'
    version_file.write_text('7.7.2-test\n', encoding='utf-8')
    project = tmp_path / 'project'
    (project / 'src' / 'tails_cloner').mkdir(parents=True)
    (project / 'src' / 'tails_cloner' / '__init__.py').write_text('', encoding='utf-8')

    result = subprocess.run(
        [
            'python3',
            str(GUEST_PROBE),
            '--scenario-variant',
            'running-live-install',
            '--transport',
            'unit',
            '--live-version-path',
            str(version_file),
            '--source-device',
            '/dev/sdb1',
            '--project-path',
            str(project),
        ],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )

    data = json.loads(result.stdout)
    assert data['transport'] == 'unit'
    assert data['scenario_variant'] == 'running-live-install'
    assert data['live_version_path']['exists'] is True
    assert data['live_version_path']['content'] == '7.7.2-test'
    assert data['running_tails_detection']['is_running_tails'] is True
    assert data['running_tails_detection']['running_tails_device'] == '/dev/sdb1'
    assert data['block_devices']['source_parent_disk'] == '/dev/sdb'
    assert data['project_access']['checkout_visible'] is True
    assert data['project_access']['python_import_tails_cloner'] is True


def test_guest_probe_can_emit_serial_marker_prefix(tmp_path: Path) -> None:
    live_dir = tmp_path / 'live'
    live_dir.mkdir()
    version_file = live_dir / 'Tails.version'
    version_file.write_text('7.7.2-test\n', encoding='utf-8')

    result = subprocess.run(
        [
            'python3',
            str(GUEST_PROBE),
            '--scenario-variant',
            'running-live-install',
            '--transport',
            'serial_marker',
            '--live-version-path',
            str(version_file),
            '--prefix',
        ],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )

    line = result.stdout.strip()
    assert line.startswith(PROBE_PREFIX)
    data = json.loads(line.removeprefix(PROBE_PREFIX))
    assert data['transport'] == 'serial_marker'
    assert data['live_version_path']['exists'] is True


def test_guest_probe_reports_missing_live_version_as_not_running_tails(tmp_path: Path) -> None:
    missing_version = tmp_path / 'missing' / 'Tails.version'

    result = subprocess.run(
        [
            'python3',
            str(GUEST_PROBE),
            '--scenario-variant',
            'running-live-install',
            '--transport',
            'unit',
            '--live-version-path',
            str(missing_version),
        ],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )

    data = json.loads(result.stdout)
    assert data['live_version_path']['exists'] is False
    assert data['running_tails_detection']['is_running_tails'] is False
