from __future__ import annotations

import json
import subprocess
from copy import deepcopy
from pathlib import Path

REPO_ROOT = Path('/workspace/tails-cloner')
if not REPO_ROOT.exists():
    REPO_ROOT = Path(__file__).resolve().parents[4]

VALIDATOR = REPO_ROOT / 'infra/vagrant-lab/real-boot-lane/validate_guest_probe_output.py'
PROBE_PREFIX = 'TAILS_CLONER_GUEST_PROBE='


def valid_probe(variant: str = 'running-live-install') -> dict:
    return {
        'transport': 'unit',
        'timestamp_utc': '2026-05-20T00:00:00+00:00',
        'scenario_variant': variant,
        'live_version_path': {
            'path': '/lib/live/mount/medium/live/Tails.version',
            'exists': True,
            'content': '7.7.2-test',
        },
        'running_tails_detection': {
            'is_running_tails': True,
            'running_tails_version': '7.7.2-test',
            'running_tails_device': '/dev/sdb1',
            'running_tails_size_bytes': 8589934592,
        },
        'block_devices': {
            'source_parent_disk': '/dev/sdb',
            'target_candidates': [
                {'path': '/dev/sdb', 'has_tails': True, 'excluded_because_source': True},
                {'path': '/dev/sdc', 'has_tails': False, 'excluded_because_source': False},
                {'path': '/dev/sdd', 'has_tails': True, 'excluded_because_source': False},
            ],
        },
        'fsuuid_boot': {
            'proc_cmdline': 'boot=live live-media=removable',
            'cmdline_options': {'boot': 'live', 'live-media': 'removable'},
            'fsuuid': '',
            'fsuuid_resolution': {},
            'live_medium': {
                'medium_path': '/lib/live/mount/medium',
                'mount_source': '/dev/sdb1',
                'mount_source_parent_disk': '/dev/sdb',
                'blkid': {'UUID': 'A690-20D2'},
            },
            'tails_media_devices': [
                {
                    'path': '/dev/sdb1',
                    'parent_disk': '/dev/sdb',
                    'fstype': 'vfat',
                    'label': 'TAILS',
                    'uuid': 'A690-20D2',
                    'size': 2033188864,
                },
                {
                    'path': '/dev/sdd1',
                    'parent_disk': '/dev/sdd',
                    'fstype': 'vfat',
                    'label': 'TAILS',
                    'uuid': 'BEEF-1234',
                    'size': 2033188864,
                },
            ],
            'live_medium_matches_fsuuid': False,
        },
        'project_access': {
            'checkout_visible': True,
            'python_import_tails_cloner': True,
        },
    }


def run_validator(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ['python3', str(VALIDATOR), *args],
        check=False,
        text=True,
        capture_output=True,
    )


def test_validator_accepts_json_probe_file(tmp_path: Path) -> None:
    probe_file = tmp_path / 'probe.json'
    probe_file.write_text(json.dumps(valid_probe()), encoding='utf-8')

    result = run_validator('--json-file', str(probe_file))

    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data['success'] is True
    assert data['scenario_variant'] == 'running-live-install'
    assert data['failures'] == []


def test_validator_accepts_serial_marker_log(tmp_path: Path) -> None:
    log_file = tmp_path / 'serial.log'
    log_file.write_text('noise\n' + PROBE_PREFIX + json.dumps(valid_probe()) + '\n', encoding='utf-8')

    result = run_validator('--log-file', str(log_file))

    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data['success'] is True


def test_validator_rejects_missing_live_version(tmp_path: Path) -> None:
    probe = deepcopy(valid_probe())
    probe['live_version_path']['exists'] = False
    probe['live_version_path']['content'] = ''
    probe['running_tails_detection']['is_running_tails'] = False
    probe_file = tmp_path / 'probe.json'
    probe_file.write_text(json.dumps(probe), encoding='utf-8')

    result = run_validator('--json-file', str(probe_file))

    assert result.returncode == 1
    data = json.loads(result.stdout)
    assert data['success'] is False
    assert 'live_version_path.exists must be true' in data['failures']


def test_validator_rejects_unknown_variant(tmp_path: Path) -> None:
    probe = valid_probe(variant='unknown-variant')
    probe_file = tmp_path / 'probe.json'
    probe_file.write_text(json.dumps(probe), encoding='utf-8')

    result = run_validator('--json-file', str(probe_file))

    assert result.returncode == 1
    data = json.loads(result.stdout)
    assert data['success'] is False
    assert any('unknown or unsupported scenario_variant' in failure for failure in data['failures'])


def test_validator_rejects_log_without_probe_marker(tmp_path: Path) -> None:
    log_file = tmp_path / 'serial.log'
    log_file.write_text('noise only\n', encoding='utf-8')

    result = run_validator('--log-file', str(log_file))

    assert result.returncode == 1
    data = json.loads(result.stdout)
    assert data['success'] is False
    assert any('TAILS_CLONER_GUEST_PROBE=' in failure for failure in data['failures'])



def test_record_guest_probe_evidence_dry_run_maps_validated_marker(tmp_path: Path) -> None:
    recorder = REPO_ROOT / 'infra/vagrant-lab/real-boot-lane/record_guest_probe_evidence.py'
    log_file = tmp_path / 'serial.log'
    log_file.write_text('noise\n' + PROBE_PREFIX + json.dumps(valid_probe()) + '\n', encoding='utf-8')

    result = subprocess.run(
        ['python3', str(recorder), '--log-file', str(log_file), '--mark-done', '--dry-run'],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )

    data = json.loads(result.stdout)
    assert data['success'] is True
    assert data['dry_run'] is True
    assert data['mark_done'] is True
    assert data['scenario_variant'] == 'running-live-install'
    assert data['case'] == 'install.running_live_iso_usb'
    assert data['todo'] == 'E2E-001'
    assert data['live_version'] == '7.7.2-test'
    assert data['running_tails_device'] == '/dev/sdb1'
