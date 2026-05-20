#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
LANE_DIR = Path(__file__).resolve().parent
RESOLVER = LANE_DIR / 'resolve_capture_roles.py'
DEFAULT_OUTPUT_DIR = LANE_DIR / 'out' / 'capture-runbooks'


def shell_join(command: list[str]) -> str:
    return ' '.join(shlex.quote(part) for part in command)


def parse_role_values(values: list[str]) -> list[str]:
    for value in values:
        if '=' not in value:
            raise SystemExit(f'invalid role mapping {value!r}; expected ROLE=VALUE')
    return values


def resolve_plan(variant: str, roles: list[str], require_existing_media: bool) -> dict[str, Any]:
    command = ['python3', str(RESOLVER), '--variant', variant, '--emit-plan']
    if require_existing_media:
        command.append('--require-existing-media')
    for role in roles:
        command.extend(['--role', role])
    result = subprocess.run(command, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        payload = json.loads(result.stdout or '{}')
    except json.JSONDecodeError as error:
        raise SystemExit(f'resolver returned invalid JSON: {error}\n{result.stdout}\n{result.stderr}') from error
    if result.returncode != 0 or not payload.get('ready') or not payload.get('plan', {}).get('success'):
        raise SystemExit(json.dumps({'resolver_returncode': result.returncode, 'resolver': payload, 'stderr': result.stderr}, indent=2))
    return payload


def runbook_text(payload: dict[str, Any]) -> str:
    plan = payload['plan']
    capture_command = shell_join(plan['capture_command'])
    record_command = shell_join(plan['record_command'])
    guest_step = plan['guest_step']
    guest_command = plan.get('guest_command', '<guest command unavailable>')
    variant = payload['variant']
    lines = [
        '#!/usr/bin/env bash',
        'set -euo pipefail',
        '',
        f'# Real-boot capture runbook for {variant}',
        f'# Scenario: {plan["scenario_ref"]}',
        f'# TODO: {plan["todo_ref"]}',
        '',
        'ACTION="${1:-print}"',
        '',
        'print_instructions() {',
        'cat <<\'INSTRUCTIONS\'',
        f'Variant: {variant}',
        f'Scenario: {plan["scenario_ref"]}',
        f'TODO: {plan["todo_ref"]}',
        '',
        'Usage:',
        f'  {variant}.sh print',
        f'  {variant}.sh capture',
        f'  {variant}.sh record',
        '',
        'Step 1: Run capture. It boots QEMU and waits for the guest serial marker.',
        'Step 2: Inside the booted Tails guest, run the guest step printed by the capture command.',
        f'Guest step summary: {guest_step}',
        'Guest command to run inside Tails:',
        guest_command,
        'Step 3: Run record after the marker is captured to validate and promote evidence.',
        'INSTRUCTIONS',
        'echo',
        'echo "Capture command:"',
        f'echo {shlex.quote(capture_command)}',
        'echo',
        'echo "Record command:"',
        f'echo {shlex.quote(record_command)}',
        '}',
        '',
        'case "$ACTION" in',
        '  print)',
        '    print_instructions',
        '    ;;',
        '  capture)',
        f'    exec {capture_command}',
        '    ;;',
        '  record)',
        f'    exec {record_command}',
        '    ;;',
        '  *)',
        '    echo "Unknown action: $ACTION" >&2',
        '    echo "Expected one of: print, capture, record" >&2',
        '    exit 2',
        '    ;;',
        'esac',
        '',
    ]
    return '\n'.join(lines) + '\n'


def write_runbook(payload: dict[str, Any], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{payload['variant']}.sh"
    path.write_text(runbook_text(payload), encoding='utf-8')
    path.chmod(0o755)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description='Emit a runnable shell runbook for a READY real-boot capture variant.')
    parser.add_argument('--variant', required=True)
    parser.add_argument('--role', action='append', default=[])
    parser.add_argument('--require-existing-media', action='store_true')
    parser.add_argument('--output-dir', type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    payload = resolve_plan(args.variant, parse_role_values(args.role), args.require_existing_media)
    path = write_runbook(payload, args.output_dir)
    print(json.dumps({'success': True, 'variant': args.variant, 'runbook': str(path)}, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
