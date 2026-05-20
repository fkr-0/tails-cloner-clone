#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
LANE_DIR = Path(__file__).resolve().parent
TAILS_IMAGE_CACHE = REPO_ROOT / '.cache/vagrant-lab/tails-images'
CAPTURE_MEDIA_DIR = REPO_ROOT / '.cache/vagrant-lab/capture-media'
RUNBOOK_DIR = LANE_DIR / 'out' / 'capture-runbooks'
SERIAL_LOG_DIR = LANE_DIR / 'out' / 'serial-logs'
README = RUNBOOK_DIR / 'README.md'
ARTIFACT_GROUPS = {
    'tails_images': TAILS_IMAGE_CACHE,
    'capture_media': CAPTURE_MEDIA_DIR,
    'capture_runbooks': RUNBOOK_DIR,
    'serial_logs': SERIAL_LOG_DIR,
}


def file_entry(path: Path) -> dict[str, Any]:
    exists = path.exists()
    return {
        'path': str(path),
        'exists': exists,
        'is_file': path.is_file() if exists else False,
        'is_dir': path.is_dir() if exists else False,
        'size_bytes': path.stat().st_size if exists and path.is_file() else None,
    }


def directory_entries(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    entries = []
    for child in sorted(path.iterdir()):
        if child.is_file():
            entries.append(file_entry(child))
    return entries


def report_artifacts() -> dict[str, Any]:
    groups: dict[str, Any] = {}
    for name, path in ARTIFACT_GROUPS.items():
        entries = directory_entries(path)
        groups[name] = {
            'path': str(path),
            'exists': path.exists(),
            'file_count': len(entries),
            'total_size_bytes': sum(entry['size_bytes'] or 0 for entry in entries),
            'files': entries,
        }
    expected_runbooks = [
        RUNBOOK_DIR / 'running-live-install.sh',
        RUNBOOK_DIR / 'outdated-running-iso-upgrade.sh',
        RUNBOOK_DIR / 'outdated-running-source-device-upgrade.sh',
    ]
    expected_logs = [
        SERIAL_LOG_DIR / 'running-live-install.log',
        SERIAL_LOG_DIR / 'outdated-running-iso-upgrade.log',
        SERIAL_LOG_DIR / 'outdated-running-source-device-upgrade.log',
    ]
    checks = {
        'tails_images_present': groups['tails_images']['file_count'] >= 2,
        'capture_media_present': groups['capture_media']['file_count'] >= 2,
        'runbooks_present': all(path.exists() for path in expected_runbooks),
        'operator_readme_present': README.exists(),
        'serial_log_dir_present': SERIAL_LOG_DIR.exists(),
        'serial_logs_present': all(path.exists() for path in expected_logs),
    }
    return {
        'groups': groups,
        'checks': checks,
        'ready_for_capture_attempt': all(
            checks[name]
            for name in [
                'tails_images_present',
                'capture_media_present',
                'runbooks_present',
                'operator_readme_present',
                'serial_log_dir_present',
            ]
        ),
        'all_serial_logs_present': checks['serial_logs_present'],
        'expected_runbooks': [str(path) for path in expected_runbooks],
        'expected_serial_logs': [str(path) for path in expected_logs],
    }


def print_human(report: dict[str, Any]) -> None:
    print('real-boot artifacts')
    print(f"ready_for_capture_attempt: {report['ready_for_capture_attempt']}")
    print(f"all_serial_logs_present: {report['all_serial_logs_present']}")
    print('checks:')
    for name, value in report['checks'].items():
        print(f'  {name}: {value}')
    print('groups:')
    for name, group in report['groups'].items():
        print(f"  {name}: exists={group['exists']} files={group['file_count']} bytes={group['total_size_bytes']}")
        print(f"    path: {group['path']}")


def main() -> int:
    parser = argparse.ArgumentParser(description='Report persistent real-boot lane artifacts and expected evidence files.')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--require-capture-ready', action='store_true')
    parser.add_argument('--require-serial-logs', action='store_true')
    args = parser.parse_args()
    report = report_artifacts()
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_human(report)
    if args.require_capture_ready and not report['ready_for_capture_attempt']:
        return 1
    if args.require_serial_logs and not report['all_serial_logs_present']:
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
