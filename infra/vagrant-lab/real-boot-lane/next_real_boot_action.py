#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

LANE_DIR = Path(__file__).resolve().parent
STATE_REPORTER = LANE_DIR / 'report_real_boot_state.py'


def load_state() -> dict[str, Any]:
    result = subprocess.run(
        ['python3', str(STATE_REPORTER), '--json'],
        check=False,
        text=True,
        capture_output=True,
    )
    try:
        payload = json.loads(result.stdout or '{}')
    except json.JSONDecodeError as error:
        raise SystemExit(f'state reporter returned invalid JSON: {error}\nstdout={result.stdout}\nstderr={result.stderr}') from error
    if result.returncode != 0:
        raise SystemExit(f'state reporter failed: {result.stderr or result.stdout}')
    return payload


def choose_variant(state: dict[str, Any], requested_variant: str | None) -> dict[str, Any]:
    variants = state.get('variants', [])
    if requested_variant is not None:
        for row in variants:
            if row['variant'] == requested_variant:
                return row
        raise SystemExit(f'unknown real-boot variant: {requested_variant}')
    for row in variants:
        if row.get('evidence_status') != 'valid':
            return row
    raise SystemExit('all real-boot variants already have valid evidence')


def action_for(row: dict[str, Any]) -> dict[str, Any]:
    runbook = Path(row['runbook'])
    if row.get('evidence_status') == 'valid':
        status = 'done'
        commands: list[str] = []
        next_step = 'evidence is already valid for this variant'
    elif not row.get('ready'):
        status = 'blocked'
        commands = ['real-boot-prepare-runbooks', 'real-boot-preflight', 'real-boot-state']
        next_step = 'prepare runbooks/media and re-run preflight before capture'
    elif not row.get('runbook_exists'):
        status = 'blocked'
        commands = ['real-boot-prepare-runbooks', 'real-boot-state']
        next_step = 'generate the missing runbook before capture'
    else:
        status = 'ready_for_runtime_capture'
        commands = [
            f'{runbook} print',
            f'{runbook} capture',
            '# paste the printed guest command inside Tails',
            f'{runbook} record',
            'real-boot-state',
            'real-boot-evidence-strict',
        ]
        next_step = 'execute capture, run the printed guest command inside booted Tails, then record evidence'
    return {
        'variant': row['variant'],
        'todo_ref': row['todo_ref'],
        'scenario_ref': row['scenario_ref'],
        'status': status,
        'evidence_status': row.get('evidence_status'),
        'evidence_log_file': row.get('evidence_log_file'),
        'runbook': row.get('runbook'),
        'commands': commands,
        'next_step': next_step,
    }


def print_human(action: dict[str, Any]) -> None:
    print(f"next real-boot action: {action['variant']} [{action['todo_ref']}]")
    print(f"status: {action['status']}")
    print(f"evidence: {action['evidence_status']} ({action['evidence_log_file']})")
    print(f"next: {action['next_step']}")
    if action['commands']:
        print('commands:')
        for command in action['commands']:
            print(f'  {command}')


def main() -> int:
    parser = argparse.ArgumentParser(description='Print the next concrete real-boot runtime action.')
    parser.add_argument('--variant', help='Choose a specific variant instead of the first missing-evidence variant.')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    action = action_for(choose_variant(load_state(), args.variant))
    if args.json:
        print(json.dumps(action, indent=2, sort_keys=True))
    else:
        print_human(action)
    return 0 if action['status'] != 'blocked' else 1


if __name__ == '__main__':
    raise SystemExit(main())
