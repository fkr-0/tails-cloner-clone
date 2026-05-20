#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

LANE_DIR = Path(__file__).resolve().parent
OUT_DIR = LANE_DIR / 'out'
SNAPSHOT_DIR = OUT_DIR / 'status-snapshots'
VARIANTS = [
    'running-live-install',
    'outdated-running-iso-upgrade',
    'outdated-running-source-device-upgrade',
]
COMMANDS = {
    'preflight': ['python3', str(LANE_DIR / 'report_real_boot_preflight.py'), '--json'],
    'artifacts': ['python3', str(LANE_DIR / 'report_real_boot_artifacts.py'), '--json'],
    'state': ['python3', str(LANE_DIR / 'report_real_boot_state.py'), '--json'],
    'evidence': ['python3', str(LANE_DIR / 'report_capture_evidence.py'), '--json'],
}


def run_json(command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(command, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        payload = json.loads(completed.stdout or '{}')
    except json.JSONDecodeError as error:
        return {
            'success': False,
            'returncode': completed.returncode,
            'error': f'invalid JSON: {error}',
            'stdout_tail': completed.stdout[-2000:],
            'stderr_tail': completed.stderr[-2000:],
        }
    payload['_returncode'] = completed.returncode
    if completed.stderr:
        payload['_stderr_tail'] = completed.stderr[-2000:]
    return payload


def next_action(variant: str) -> dict[str, Any]:
    return run_json(['python3', str(LANE_DIR / 'next_real_boot_action.py'), '--variant', variant, '--json'])


def build_snapshot() -> dict[str, Any]:
    generated_at = datetime.now(UTC).isoformat(timespec='seconds')
    sections = {name: run_json(command) for name, command in COMMANDS.items()}
    next_actions = {variant: next_action(variant) for variant in VARIANTS}
    state = sections.get('state', {})
    artifacts = sections.get('artifacts', {})
    preflight = sections.get('preflight', {})
    evidence = sections.get('evidence', {})
    return {
        'generated_at_utc': generated_at,
        'summary': {
            'preflight_ready_for_attempt': preflight.get('ready_for_attempt'),
            'artifacts_ready_for_capture_attempt': artifacts.get('ready_for_capture_attempt'),
            'all_ready': state.get('all_ready'),
            'ready_count': state.get('ready_count'),
            'valid_evidence_count': state.get('valid_evidence_count'),
            'total_count': state.get('total_count'),
            'all_evidence_valid': state.get('all_evidence_valid'),
            'evidence_valid_count': evidence.get('valid_count'),
            'serial_logs_present': artifacts.get('checks', {}).get('serial_logs_present'),
        },
        'sections': sections,
        'next_actions': next_actions,
    }


def write_json(snapshot: dict[str, Any], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / 'latest.json'
    path.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    return path


def markdown_for(snapshot: dict[str, Any]) -> str:
    summary = snapshot['summary']
    lines = [
        '# Real-boot status snapshot',
        '',
        f"Generated UTC: `{snapshot['generated_at_utc']}`",
        '',
        '## Summary',
        '',
        '```yaml',
        f"preflight_ready_for_attempt: {summary.get('preflight_ready_for_attempt')}",
        f"artifacts_ready_for_capture_attempt: {summary.get('artifacts_ready_for_capture_attempt')}",
        f"all_ready: {summary.get('all_ready')}",
        f"ready_count: {summary.get('ready_count')}",
        f"valid_evidence_count: {summary.get('valid_evidence_count')}",
        f"total_count: {summary.get('total_count')}",
        f"all_evidence_valid: {summary.get('all_evidence_valid')}",
        f"serial_logs_present: {summary.get('serial_logs_present')}",
        '```',
        '',
        '## Next actions',
        '',
    ]
    for variant in VARIANTS:
        action = snapshot['next_actions'][variant]
        lines.extend(
            [
                f'### {variant}',
                '',
                f"Status: `{action.get('status')}`",
                f"Evidence: `{action.get('evidence_status')}`",
                '',
                '```bash',
                *action.get('commands', []),
                '```',
                '',
            ]
        )
    return '\n'.join(lines)


def write_markdown(snapshot: dict[str, Any], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / 'latest.md'
    path.write_text(markdown_for(snapshot) + '\n', encoding='utf-8')
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description='Write a persistent consolidated status snapshot for the real-boot lane.')
    parser.add_argument('--output-dir', type=Path, default=SNAPSHOT_DIR)
    parser.add_argument('--json-only', action='store_true')
    args = parser.parse_args()
    snapshot = build_snapshot()
    json_path = write_json(snapshot, args.output_dir)
    markdown_path = None if args.json_only else write_markdown(snapshot, args.output_dir)
    result = {
        'success': True,
        'json_path': str(json_path),
        'markdown_path': str(markdown_path) if markdown_path else None,
        'summary': snapshot['summary'],
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
