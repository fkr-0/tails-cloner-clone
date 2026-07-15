#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
LANE_DIR = Path(__file__).resolve().parent
VALIDATOR = LANE_DIR / 'validate_guest_probe_output.py'
SCENARIOS_PATH = REPO_ROOT / 'infra/vagrant-lab/scenarios.yml'
TODOS_PATH = REPO_ROOT / 'infra/vagrant-lab/todos.yml'
PROBE_PREFIX = 'TAILS_CLONER_GUEST_PROBE='

VARIANT_TO_CASE_AND_TODO = {
    'running-live-install': ('install.running_live_iso_usb', 'E2E-001'),
    'outdated-running-iso-upgrade': ('upgrade.outdated_running_tails.source_iso_on_disc', 'E2E-004'),
    'outdated-running-source-device-upgrade': (
        'upgrade.outdated_running_tails.source_not_running_live_iso_usb',
        'E2E-005',
    ),
}


def load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding='utf-8'))


def write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.write_text(yaml.safe_dump(data, sort_keys=False, width=120), encoding='utf-8')


def extract_probe_from_log(path: Path) -> dict[str, Any]:
    for line in reversed(path.read_text(encoding='utf-8', errors='replace').splitlines()):
        if line.startswith(PROBE_PREFIX):
            return json.loads(line.removeprefix(PROBE_PREFIX))
    raise SystemExit(f'no {PROBE_PREFIX!r} marker found in {path}')


def validate_log(log_file: Path) -> dict[str, Any]:
    result = subprocess.run(
        ['python3', str(VALIDATOR), '--log-file', str(log_file)],
        check=False,
        text=True,
        capture_output=True,
    )
    try:
        payload = json.loads(result.stdout or '{}')
    except json.JSONDecodeError as error:
        raise SystemExit(f'validator returned invalid JSON: {error}') from error
    if result.returncode != 0 or not payload.get('success'):
        failures = payload.get('failures') or [result.stderr.strip() or 'unknown validation failure']
        raise SystemExit(f'guest probe validation failed: {failures}')
    return payload


def append_unique(items: list[str], value: str) -> list[str]:
    if value not in items:
        items.append(value)
    return items


def update_records(
    *,
    log_file: Path,
    validator_result: dict[str, Any],
    probe: dict[str, Any],
    mark_done: bool,
    evidence_date: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    variant = str(validator_result['scenario_variant'])
    if variant not in VARIANT_TO_CASE_AND_TODO:
        raise SystemExit(f'validated guest probe variant is not mapped: {variant}')
    case_name, todo_id = VARIANT_TO_CASE_AND_TODO[variant]
    scenarios = load_yaml(SCENARIOS_PATH)
    todos = load_yaml(TODOS_PATH)

    case = scenarios['cases'][case_name]
    coverage = list(case.get('current_coverage', []))
    for item in [
        'validated guest probe evidence was recorded from a captured serial marker',
        'guest_probe_output proved live version path, running Tails detection, project access, and variant-specific gates',
    ]:
        append_unique(coverage, item)
    case['current_coverage'] = coverage
    case['guest_probe_evidence'] = {
        'date': evidence_date,
        'log_file': str(log_file),
        'scenario_variant': variant,
        'transport': probe.get('transport'),
        'live_version': probe.get('live_version_path', {}).get('content'),
        'running_tails_device': probe.get('running_tails_detection', {}).get('running_tails_device'),
        'validator_result': validator_result,
    }
    if mark_done:
        case['current_status'] = 'implemented'
        case.pop('todo_refs', None)
        case['next_step'] = 'keep as optional/manual real-boot evidence path unless a fully automatic guest execution transport is added'
    else:
        case['next_step'] = 'review guest_probe_evidence and rerun with --mark-done when ready to promote the scenario'
    scenarios['cases'][case_name] = case

    todo = todos['items'][todo_id]
    notes = list(todo.get('implementation_notes', []))
    for item in [
        f'validated guest probe evidence recorded from {log_file}',
        f'guest probe variant {variant} satisfied validate_guest_probe_output.py',
    ]:
        append_unique(notes, item)
    todo['implementation_notes'] = notes
    if mark_done:
        todo['status'] = 'done'
        todo['completion'] = {
            'date': evidence_date,
            'evidence': f'validated guest probe serial marker for {variant} from {log_file}',
        }
    return scenarios, todos


def main() -> int:
    parser = argparse.ArgumentParser(description='Record validated real-boot guest probe evidence into scenario/TODO metadata.')
    parser.add_argument('--log-file', type=Path, required=True)
    parser.add_argument('--mark-done', action='store_true', help='Promote the matching scenario/TODO to implemented/done.')
    parser.add_argument('--dry-run', action='store_true', help='Validate and print the planned metadata update without writing files.')
    args = parser.parse_args()

    validator_result = validate_log(args.log_file)
    probe = extract_probe_from_log(args.log_file)
    evidence_date = datetime.now(UTC).date().isoformat()
    scenarios, todos = update_records(
        log_file=args.log_file,
        validator_result=validator_result,
        probe=probe,
        mark_done=args.mark_done,
        evidence_date=evidence_date,
    )
    variant = validator_result['scenario_variant']
    case_name, todo_id = VARIANT_TO_CASE_AND_TODO[variant]
    result = {
        'success': True,
        'dry_run': args.dry_run,
        'mark_done': args.mark_done,
        'scenario_variant': variant,
        'case': case_name,
        'todo': todo_id,
        'live_version': probe.get('live_version_path', {}).get('content'),
        'running_tails_device': probe.get('running_tails_detection', {}).get('running_tails_device'),
    }
    if not args.dry_run:
        write_yaml(SCENARIOS_PATH, scenarios)
        write_yaml(TODOS_PATH, todos)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
