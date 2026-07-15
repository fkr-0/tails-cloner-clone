from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path('/workspace/tails-cloner')
if not REPO_ROOT.exists():
    REPO_ROOT = Path(__file__).resolve().parents[4]

SCENARIO_PATH = REPO_ROOT / 'infra/vagrant-lab/scenarios.yml'
TODO_PATH = REPO_ROOT / 'infra/vagrant-lab/todos.yml'
REAL_BOOT_LANE_PATH = REPO_ROOT / 'infra/vagrant-lab/real-boot-lane/lane.yml'

REQUIRED_CASES = {
    'install.running_live_iso_usb',
    'install.not_running_live_iso_usb',
    'install.any_linux_download_to_disc',
    'upgrade.outdated_running_tails.source_not_running_live_iso_usb',
    'upgrade.outdated_running_tails.source_iso_on_disc',
    'upgrade.any_linux.source_running_live_iso_usb',
    'upgrade.any_linux.source_iso_on_disc',
}

ALLOWED_STATUSES = {'implemented', 'partial', 'planned', 'blocked'}


def _scenario_data() -> dict:
    assert SCENARIO_PATH.exists(), f'missing scenario matrix: {SCENARIO_PATH}'
    return yaml.safe_load(SCENARIO_PATH.read_text(encoding='utf-8'))


def _todo_data() -> dict:
    assert TODO_PATH.exists(), f'missing scenario TODO backlog: {TODO_PATH}'
    return yaml.safe_load(TODO_PATH.read_text(encoding='utf-8'))


def _real_boot_lane_data() -> dict:
    assert REAL_BOOT_LANE_PATH.exists(), f'missing real-boot lane manifest: {REAL_BOOT_LANE_PATH}'
    return yaml.safe_load(REAL_BOOT_LANE_PATH.read_text(encoding='utf-8'))


def test_scenario_matrix_documents_requested_install_and_upgrade_cases() -> None:
    data = _scenario_data()
    cases = set(data['cases'])
    assert cases >= REQUIRED_CASES


def test_scenario_matrix_has_status_and_next_step_for_every_case() -> None:
    data = _scenario_data()
    for case_name, case in data['cases'].items():
        assert case['current_status'] in ALLOWED_STATUSES, case_name
        assert case.get('group') in {'install', 'upgrade_with_persistence'}, case_name
        assert case.get('title'), case_name
        assert case.get('desired_flow'), case_name
        assert case.get('current_coverage'), case_name
        assert case.get('next_step'), case_name


def test_persistence_cases_are_marked_as_persistence_required() -> None:
    data = _scenario_data()
    for case_name, case in data['cases'].items():
        if case_name.startswith('upgrade.'):
            assert case.get('persistence_required') is True, case_name


def test_any_linux_iso_upgrade_is_the_current_implemented_persistence_e2e() -> None:
    data = _scenario_data()
    case = data['cases']['upgrade.any_linux.source_iso_on_disc']
    assert case['current_status'] == 'implemented'
    assert 'simulate-internal-upgrade-preserve-persistence' in case['command']
    assert any('marker hash' in item or 'marker' in item for item in case['current_coverage'])


def test_partial_and_planned_cases_have_backlog_todos() -> None:
    scenarios = _scenario_data()
    todos = _todo_data()
    todo_ids = set(todos['items'])
    for case_name, case in scenarios['cases'].items():
        if case['current_status'] in {'partial', 'planned'}:
            assert case.get('todo_refs'), case_name
            assert set(case['todo_refs']) <= todo_ids, case_name


def test_backlog_todos_point_to_existing_cases_and_have_acceptance() -> None:
    scenarios = _scenario_data()
    todos = _todo_data()
    cases = scenarios['cases']
    for todo_id, item in todos['items'].items():
        assert item['status'] in {'todo', 'in_progress', 'done', 'blocked'}, todo_id
        assert item['priority'] in {'high', 'medium', 'low'}, todo_id
        assert item.get('acceptance'), todo_id
        scenario_refs = item.get('scenario_refs', [])
        if item.get('kind') == 'lab_scenario':
            assert scenario_refs, todo_id
        for scenario_ref in scenario_refs:
            assert scenario_ref in cases, todo_id
            expected_statuses = {'implemented'} if item['status'] == 'done' else {'partial', 'planned'}
            assert cases[scenario_ref]['current_status'] in expected_statuses, todo_id


def test_not_running_live_usb_source_case_has_guarded_source_device_validation() -> None:
    scenarios = _scenario_data()
    case = scenarios['cases']['install.not_running_live_iso_usb']
    assert 'destructive-source-device-install-validate' in case['command']
    assert '--target extra' in case['command']
    assert any('dry-run-source-device-install' in item for item in case['current_coverage'])
    assert any('whole-source-device to whole-target-device' in item for item in case['current_coverage'])
    assert any('live/Tails.version' in item for item in case['current_coverage'])
    assert any('distinct block devices' in item for item in case['current_coverage'])
    assert case['current_status'] == 'implemented'
    assert 'todo_refs' not in case


def test_source_device_todo_records_completed_controller_execution() -> None:
    todos = _todo_data()
    todo = todos['items']['E2E-002']
    assert todo['status'] == 'done'
    assert todo['scenario_refs'] == ['install.not_running_live_iso_usb']
    assert any('whole source device to whole target device' in item for item in todo['implementation_notes'])
    assert 'post-write validation' in todo['completion']['evidence']


def test_any_linux_download_to_disc_has_verified_guarded_destructive_validation() -> None:
    scenarios = _scenario_data()
    case = scenarios['cases']['install.any_linux_download_to_disc']
    assert case['current_status'] == 'implemented'
    assert 'destructive-install-validate' in case['command']
    assert '--target extra' in case['command']
    assert 'TAILS_CLONER_LAB_ALLOW_DESTRUCTIVE=1' in case['command']
    assert any('vfat system partition' in item for item in case['current_coverage'])
    assert case['verified']['result'].startswith('passed')


def test_destructive_fresh_install_todo_is_done_with_evidence() -> None:
    todos = _todo_data()
    todo = todos['items']['E2E-003']
    assert todo['status'] == 'done'
    assert 'destructive-install-validate passed' in todo['completion']['evidence']
    assert any('validated in the running controller' in item for item in todo['implementation_notes'])


def test_real_boot_lane_manifest_is_safe_and_connected_to_running_live_cases() -> None:
    lane = _real_boot_lane_data()
    scenarios = _scenario_data()
    assert lane['lane'] == 'real_boot_qemu'
    assert lane['status'] == 'scaffolded'
    assert lane['safety']['default_destructive_writes'] is False
    assert lane['safety']['host_mutation_expected'] is False
    assert lane['acceptance_checks']
    assert scenarios['cases']['install.running_live_iso_usb']['real_boot_lane'].endswith('lane.yml')
    assert scenarios['cases']['upgrade.outdated_running_tails.source_iso_on_disc']['real_boot_lane'].endswith('lane.yml')


def test_running_live_todo_is_in_progress_and_points_to_real_boot_lane() -> None:
    todos = _todo_data()
    todo = todos['items']['E2E-001']
    assert todo['status'] == 'in_progress'
    assert any('real-boot-lane/lane.yml' in item for item in todo['implementation_notes'])
