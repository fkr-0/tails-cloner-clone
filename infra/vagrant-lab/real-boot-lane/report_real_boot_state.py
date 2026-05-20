#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
LANE_DIR = Path(__file__).resolve().parent
MATRIX_PATH = LANE_DIR / 'capture_session_matrix.yml'
PREPARED_READINESS = LANE_DIR / 'report_prepared_capture_readiness.py'
EVIDENCE = LANE_DIR / 'report_capture_evidence.py'
RUNBOOK_DIR = LANE_DIR / 'out' / 'capture-runbooks'
README = RUNBOOK_DIR / 'README.md'
FSUUID_SMOKE_EVIDENCE = LANE_DIR / 'out' / 'fsuuid' / 'fsuuid-direct-kernel-evidence.json'


def run_json(command: list[str], *, allow_failure: bool = False) -> dict[str, Any]:
    result = subprocess.run(command, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        payload = json.loads(result.stdout or '{}')
    except json.JSONDecodeError as error:
        raise SystemExit(f"command returned invalid JSON: {' '.join(command)}\n{error}\nstdout={result.stdout}\nstderr={result.stderr}") from error
    payload['_returncode'] = result.returncode
    if result.stderr:
        payload['_stderr'] = result.stderr
    if result.returncode != 0 and not allow_failure:
        raise SystemExit(json.dumps(payload, indent=2, sort_keys=True))
    return payload


def load_matrix() -> dict[str, Any]:
    return yaml.safe_load(MATRIX_PATH.read_text(encoding='utf-8'))


def report_readiness() -> dict[str, Any]:
    return run_json(['python3', str(PREPARED_READINESS), '--json'], allow_failure=True)


def report_evidence() -> dict[str, Any]:
    return run_json(['python3', str(EVIDENCE), '--json'], allow_failure=True)



def report_fsuuid_smoke() -> dict[str, Any]:
    if not FSUUID_SMOKE_EVIDENCE.exists():
        return {
            'status': 'missing',
            'evidence_json': str(FSUUID_SMOKE_EVIDENCE),
            'next_action': 'run real-boot-fsuuid-smoke',
        }
    try:
        data = json.loads(FSUUID_SMOKE_EVIDENCE.read_text(encoding='utf-8'))
    except json.JSONDecodeError as error:
        return {
            'status': 'invalid_json',
            'evidence_json': str(FSUUID_SMOKE_EVIDENCE),
            'error': str(error),
            'next_action': 'rerun real-boot-fsuuid-smoke',
        }
    analysis = data.get('analysis', {})
    return {
        'status': data.get('status', 'unknown'),
        'stop_reason': data.get('stop_reason'),
        'evidence_json': str(FSUUID_SMOKE_EVIDENCE),
        'serial_log': data.get('serial_log'),
        'selected_system_partition': analysis.get('selected_system_partition'),
        'early_fsuuid_evidence_complete': bool(analysis.get('early_fsuuid_evidence_complete')),
        'scope': 'bounded direct-kernel smoke for early initramfs FSUUID selection',
        'next_action': 'full graphical/in-guest probe remains separate' if data.get('status') == 'passed' else 'rerun real-boot-fsuuid-smoke',
    }

def runbook_path(variant: str) -> Path:
    return RUNBOOK_DIR / f'{variant}.sh'


def merge_state() -> dict[str, Any]:
    matrix = load_matrix()
    readiness = report_readiness()
    evidence = report_evidence()
    readiness_by_variant = {row['variant']: row for row in readiness.get('variants', [])}
    evidence_by_variant = {row['variant']: row for row in evidence.get('variants', [])}
    variants = []
    for variant, meta in matrix['variants'].items():
        ready_row = readiness_by_variant.get(variant, {})
        evidence_row = evidence_by_variant.get(variant, {})
        runbook = runbook_path(variant)
        variants.append(
            {
                'variant': variant,
                'scenario_ref': meta['scenario_ref'],
                'todo_ref': meta['todo_ref'],
                'ready': bool(ready_row.get('ready')),
                'readiness_missing_roles': ready_row.get('missing_roles', []),
                'readiness_missing_media_paths': ready_row.get('missing_media_paths', []),
                'evidence_status': evidence_row.get('status', 'unknown'),
                'evidence_log_file': evidence_row.get('log_file'),
                'runbook': str(runbook),
                'runbook_exists': runbook.exists(),
                'next_action': next_action(ready_row, evidence_row, runbook),
            }
        )
    ready_count = sum(1 for row in variants if row['ready'])
    valid_count = sum(1 for row in variants if row['evidence_status'] == 'valid')
    return {
        'ready_count': ready_count,
        'valid_evidence_count': valid_count,
        'total_count': len(variants),
        'all_ready': ready_count == len(variants),
        'all_evidence_valid': valid_count == len(variants),
        'operator_readme': str(README),
        'operator_readme_exists': README.exists(),
        'fsuuid_smoke': report_fsuuid_smoke(),
        'variants': variants,
    }


def next_action(ready_row: dict[str, Any], evidence_row: dict[str, Any], runbook: Path) -> str:
    if evidence_row.get('status') == 'valid':
        return 'evidence valid; scenario can be promoted/kept as completed evidence'
    if not ready_row.get('ready'):
        return 'run real-boot-prepare-runbooks, then real-boot-readiness'
    if not runbook.exists():
        return 'run real-boot-prepare-runbooks to generate the runbook'
    return f'run {runbook} capture, paste guest command inside Tails, then run {runbook} record'


def print_human(state: dict[str, Any]) -> None:
    print('real-boot state')
    print(f"readiness: {state['ready_count']}/{state['total_count']} ready")
    print(f"evidence: {state['valid_evidence_count']}/{state['total_count']} valid")
    print(f"operator README: {state['operator_readme']} exists={state['operator_readme_exists']}")
    fsuuid = state.get('fsuuid_smoke', {})
    print(f"FSUUID smoke: {fsuuid.get('status')} selected={fsuuid.get('selected_system_partition')} evidence={fsuuid.get('early_fsuuid_evidence_complete')}")
    for row in state['variants']:
        print(f"- {row['variant']} [{row['todo_ref']}]")
        print(f"  ready: {row['ready']}")
        print(f"  evidence: {row['evidence_status']} ({row['evidence_log_file']})")
        print(f"  runbook: {row['runbook']} exists={row['runbook_exists']}")
        print(f"  next: {row['next_action']}")


def main() -> int:
    parser = argparse.ArgumentParser(description='Summarize real-boot readiness, evidence, and next actions.')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--require-all-evidence-valid', action='store_true')
    args = parser.parse_args()
    state = merge_state()
    if args.json:
        print(json.dumps(state, indent=2, sort_keys=True))
    else:
        print_human(state)
    return 0 if not args.require_all_evidence_valid or state['all_evidence_valid'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
