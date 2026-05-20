#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
SCENARIOS_PATH = REPO_ROOT / 'infra/vagrant-lab/scenarios.yml'
RUNNER_PATH = REPO_ROOT / 'infra/vagrant-lab/scripts/run_lab_scenario.py'
DESTRUCTIVE_FLAG = 'TAILS_CLONER_LAB_ALLOW_DESTRUCTIVE'


def load_scenarios() -> dict[str, Any]:
    return yaml.safe_load(SCENARIOS_PATH.read_text(encoding='utf-8'))


def runner_text() -> str:
    return RUNNER_PATH.read_text(encoding='utf-8')


def scenario_runner_capabilities(text: str) -> dict[str, bool]:
    names = [
        'destructive-install-validate',
        'destructive-source-device-install-validate',
        'simulate-internal-upgrade-preserve-persistence',
        'preflight-source-device-upgrade-preserve-persistence',
        'simulate-source-device-upgrade-preserve-persistence',
    ]
    return {name: name in text for name in names}


def command_is_guarded(command: str | None) -> bool | None:
    if not command:
        return None
    if 'destructive' in command or 'simulate-' in command:
        return DESTRUCTIVE_FLAG in command
    return None


def summarize_case(name: str, case: dict[str, Any]) -> dict[str, Any]:
    command = case.get('command')
    preflight = case.get('preflight_command')
    verified = case.get('verified') or case.get('completion')
    status = case.get('current_status') or case.get('status')
    return {
        'case': name,
        'group': case.get('group'),
        'status': status,
        'default_e2e': case.get('default_e2e'),
        'persistence_required': bool(case.get('persistence_required')),
        'has_command': bool(command),
        'has_preflight_command': bool(preflight),
        'has_verified_evidence': bool(verified),
        'command_guarded': command_is_guarded(command),
        'next_step': case.get('next_step'),
    }


def report() -> dict[str, Any]:
    scenarios = load_scenarios()
    cases = [summarize_case(name, case) for name, case in sorted(scenarios.get('cases', {}).items())]
    by_status: dict[str, int] = {}
    for case in cases:
        status = str(case.get('status') or 'unknown')
        by_status[status] = by_status.get(status, 0) + 1
    guarded_commands = [case for case in cases if case['command_guarded'] is True]
    unguarded_destructive = [case for case in cases if case['command_guarded'] is False]
    text = runner_text()
    return {
        'case_count': len(cases),
        'by_status': by_status,
        'implemented_or_done_count': sum(1 for case in cases if case.get('status') in {'implemented', 'done'}),
        'runtime_real_boot_gap_count': sum(1 for case in cases if case.get('status') in {'partial', 'planned'}),
        'guarded_command_count': len(guarded_commands),
        'unguarded_destructive_cases': unguarded_destructive,
        'runner_capabilities': scenario_runner_capabilities(text),
        'destructive_flag': DESTRUCTIVE_FLAG,
        'cases': cases,
    }


def print_human(payload: dict[str, Any]) -> None:
    print('vagrant lab scenario coverage')
    print(f"cases: {payload['case_count']}")
    print(f"implemented_or_done: {payload['implemented_or_done_count']}")
    print(f"runtime_real_boot_gap: {payload['runtime_real_boot_gap_count']}")
    print(f"guarded_commands: {payload['guarded_command_count']}")
    print(f"destructive_flag: {payload['destructive_flag']}")
    print('runner capabilities:')
    for name, present in payload['runner_capabilities'].items():
        print(f'  {name}: {present}')
    print('cases:')
    for case in payload['cases']:
        print(
            f"  {case['case']}: status={case['status']} group={case['group']} "
            f"preflight={case['has_preflight_command']} verified={case['has_verified_evidence']} guarded={case['command_guarded']}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description='Report Vagrant lab scenario coverage and guarded destructive flow declarations.')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--require-no-unguarded-destructive', action='store_true')
    args = parser.parse_args()
    payload = report()
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print_human(payload)
    if args.require_no_unguarded_destructive and payload['unguarded_destructive_cases']:
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
