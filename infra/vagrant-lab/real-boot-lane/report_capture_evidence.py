#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

import yaml

LANE_DIR = Path(__file__).resolve().parent
MATRIX_PATH = LANE_DIR / 'capture_session_matrix.yml'
VALIDATOR = LANE_DIR / 'validate_guest_probe_output.py'
PROBE_PREFIX = 'TAILS_CLONER_GUEST_PROBE='


def load_matrix() -> dict[str, Any]:
    return yaml.safe_load(MATRIX_PATH.read_text(encoding='utf-8'))


def parse_log_overrides(values: list[str]) -> dict[str, Path]:
    overrides: dict[str, Path] = {}
    for value in values:
        if '=' not in value:
            raise SystemExit(f'invalid log override {value!r}; expected VARIANT=PATH')
        variant, path = value.split('=', 1)
        if not variant or not path:
            raise SystemExit(f'invalid log override {value!r}; expected VARIANT=PATH')
        overrides[variant] = Path(path)
    return overrides


def marker_present(path: Path) -> bool:
    if not path.exists():
        return False
    return any(line.startswith(PROBE_PREFIX) for line in path.read_text(encoding='utf-8', errors='replace').splitlines())


def validate_log(path: Path) -> dict[str, Any]:
    result = subprocess.run(
        ['python3', str(VALIDATOR), '--log-file', str(path)],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        payload = json.loads(result.stdout or '{}')
    except json.JSONDecodeError:
        payload = {'success': False, 'failures': ['validator returned invalid JSON']}
    payload['returncode'] = result.returncode
    if result.stderr:
        payload['stderr'] = result.stderr
    return payload


def variant_log_path(variant: dict[str, Any], override: Path | None) -> Path:
    if override is not None:
        return override
    return Path(variant['recorder_args']['log_file'])


def report_evidence(log_overrides: dict[str, Path]) -> dict[str, Any]:
    matrix = load_matrix()
    rows = []
    for variant_name, variant in matrix['variants'].items():
        log_path = variant_log_path(variant, log_overrides.get(variant_name))
        row: dict[str, Any] = {
            'variant': variant_name,
            'scenario_ref': variant['scenario_ref'],
            'todo_ref': variant['todo_ref'],
            'log_file': str(log_path),
            'log_exists': log_path.exists(),
            'marker_present': marker_present(log_path),
            'validation': None,
            'status': 'missing_log',
        }
        if row['log_exists'] and not row['marker_present']:
            row['status'] = 'missing_marker'
        elif row['marker_present']:
            validation = validate_log(log_path)
            row['validation'] = validation
            row['status'] = 'valid' if validation.get('success') else 'invalid_marker'
        rows.append(row)
    valid_count = sum(1 for row in rows if row['status'] == 'valid')
    return {
        'valid_count': valid_count,
        'total_count': len(rows),
        'all_valid': valid_count == len(rows),
        'variants': rows,
    }


def print_human(report: dict[str, Any]) -> None:
    print('real-boot capture evidence')
    print(f"valid: {report['valid_count']}/{report['total_count']}")
    for row in report['variants']:
        print(f"- {row['variant']} [{row['todo_ref']}]: {row['status']} ({row['log_file']})")
        validation = row.get('validation') or {}
        if validation.get('failures'):
            print(f"  failures: {validation['failures']}")


def main() -> int:
    parser = argparse.ArgumentParser(description='Report captured real-boot guest-probe evidence status for all variants.')
    parser.add_argument('--log', action='append', default=[], help='Override a variant log path: VARIANT=PATH')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--require-all-valid', action='store_true')
    args = parser.parse_args()
    report = report_evidence(parse_log_overrides(args.log))
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_human(report)
    return 0 if not args.require_all_valid or report['all_valid'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
