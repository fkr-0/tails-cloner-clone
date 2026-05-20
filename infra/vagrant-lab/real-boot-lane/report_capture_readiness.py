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
RESOLVER = LANE_DIR / 'resolve_capture_roles.py'


def load_matrix() -> dict[str, Any]:
    return yaml.safe_load(MATRIX_PATH.read_text(encoding='utf-8'))


def parse_role_overrides(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if '=' not in value:
            raise SystemExit(f'invalid role override {value!r}; expected ROLE=VALUE')
        result.append(value)
    return result


def resolve_variant(variant: str, roles: list[str], require_existing_media: bool) -> dict[str, Any]:
    command = ['python3', str(RESOLVER), '--variant', variant, '--emit-plan']
    if require_existing_media:
        command.append('--require-existing-media')
    for role in roles:
        command.extend(['--role', role])
    completed = subprocess.run(command, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    payload = json.loads(completed.stdout or '{}')
    payload['returncode'] = completed.returncode
    payload['stderr'] = completed.stderr
    return payload


def readiness_report(roles: list[str], require_existing_media: bool) -> dict[str, Any]:
    matrix = load_matrix()
    variants = matrix['variants']
    rows = []
    for variant in variants:
        resolved = resolve_variant(variant, roles, require_existing_media)
        rows.append(
            {
                'variant': variant,
                'scenario_ref': variants[variant]['scenario_ref'],
                'todo_ref': variants[variant]['todo_ref'],
                'ready': bool(resolved.get('ready')),
                'missing_roles': resolved.get('missing_roles', []),
                'missing_media_paths': resolved.get('missing_media_paths', []),
                'roles': resolved.get('roles', {}),
                'returncode': resolved.get('returncode'),
            }
        )
    return {
        'require_existing_media': require_existing_media,
        'ready_count': sum(1 for row in rows if row['ready']),
        'total_count': len(rows),
        'variants': rows,
    }


def print_human(report: dict[str, Any]) -> None:
    print('real-boot capture readiness')
    print(f"ready: {report['ready_count']}/{report['total_count']}")
    print(f"strict media existence: {report['require_existing_media']}")
    for row in report['variants']:
        status = 'READY' if row['ready'] else 'BLOCKED'
        print(f"- {row['variant']} [{row['todo_ref']}]: {status}")
        if row['missing_roles']:
            print(f"  missing roles: {', '.join(row['missing_roles'])}")
        if row['missing_media_paths']:
            print(f"  missing media paths: {', '.join(row['missing_media_paths'])}")


def main() -> int:
    parser = argparse.ArgumentParser(description='Report readiness for all real-boot capture variants.')
    parser.add_argument('--role', action='append', default=[], help='ROLE=VALUE mapping shared across variants.')
    parser.add_argument('--require-existing-media', action='store_true')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    report = readiness_report(parse_role_overrides(args.role), args.require_existing_media)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_human(report)
    return 0 if report['ready_count'] == report['total_count'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
