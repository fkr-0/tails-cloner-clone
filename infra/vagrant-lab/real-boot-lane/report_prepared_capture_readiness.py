#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
LANE_DIR = Path(__file__).resolve().parent
REPORT_READINESS = LANE_DIR / 'report_capture_readiness.py'
DEFAULT_MEDIA_DIR = REPO_ROOT / '.cache/vagrant-lab/capture-media'
DEFAULT_NEWER_IMG = REPO_ROOT / '.cache/vagrant-lab/tails-images/tails-amd64-7.7.2.img'
DEFAULT_ROLES = {
    'newer_img_on_disc': DEFAULT_NEWER_IMG,
    'persistent_target_media': DEFAULT_MEDIA_DIR / 'persistent-target-media.img',
    'newer_attached_source_media': DEFAULT_MEDIA_DIR / 'newer-attached-source-media.img',
}


def run_report(roles: dict[str, Path]) -> dict[str, Any]:
    command = ['python3', str(REPORT_READINESS), '--require-existing-media', '--json']
    for role, path in sorted(roles.items()):
        command.extend(['--role', f'{role}={path}'])
    result = subprocess.run(command, check=False, text=True, capture_output=True)
    try:
        payload = json.loads(result.stdout or '{}')
    except json.JSONDecodeError as error:
        raise SystemExit(f'readiness reporter returned invalid JSON: {error}\nstdout={result.stdout}\nstderr={result.stderr}') from error
    payload['returncode'] = result.returncode
    payload['prepared_roles'] = {role: str(path) for role, path in roles.items()}
    payload['prepared_role_exists'] = {role: path.exists() for role, path in roles.items()}
    return payload


def print_human(report: dict[str, Any]) -> None:
    print('prepared real-boot capture readiness')
    print(f"ready: {report['ready_count']}/{report['total_count']}")
    print('prepared roles:')
    for role, path in report['prepared_roles'].items():
        exists = report['prepared_role_exists'][role]
        print(f'  {role}: {path} exists={exists}')
    print('variants:')
    for row in report['variants']:
        status = 'READY' if row['ready'] else 'BLOCKED'
        print(f"  {row['variant']} [{row['todo_ref']}]: {status}")
        if row['missing_roles']:
            print(f"    missing roles: {', '.join(row['missing_roles'])}")
        if row['missing_media_paths']:
            print(f"    missing media paths: {', '.join(row['missing_media_paths'])}")


def main() -> int:
    parser = argparse.ArgumentParser(description='Report readiness using the default prepared capture media paths without creating files.')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--require-all-ready', action='store_true')
    args = parser.parse_args()
    report = run_report(DEFAULT_ROLES)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_human(report)
    return 0 if not args.require_all_ready or report['ready_count'] == report['total_count'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
