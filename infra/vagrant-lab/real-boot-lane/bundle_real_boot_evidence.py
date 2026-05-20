#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import tarfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

LANE_DIR = Path(__file__).resolve().parent
OUT_DIR = LANE_DIR / 'out'
BUNDLE_DIR = OUT_DIR / 'evidence-bundles'
SNAPSHOTTER = LANE_DIR / 'snapshot_real_boot_status.py'
OPERATOR_DOC = LANE_DIR / 'OPERATOR.md'
RUNBOOK_DIR = OUT_DIR / 'capture-runbooks'
SERIAL_LOG_DIR = OUT_DIR / 'serial-logs'
SNAPSHOT_DIR = OUT_DIR / 'status-snapshots'
EXPECTED_SERIAL_LOGS = [
    SERIAL_LOG_DIR / 'running-live-install.log',
    SERIAL_LOG_DIR / 'outdated-running-iso-upgrade.log',
    SERIAL_LOG_DIR / 'outdated-running-source-device-upgrade.log',
]


def file_entry(path: Path) -> dict[str, Any]:
    exists = path.exists()
    return {
        'path': str(path),
        'exists': exists,
        'size_bytes': path.stat().st_size if exists and path.is_file() else None,
    }


def collect_files() -> list[Path]:
    files: list[Path] = []
    if OPERATOR_DOC.exists():
        files.append(OPERATOR_DOC)
    for directory in [RUNBOOK_DIR, SERIAL_LOG_DIR, SNAPSHOT_DIR]:
        if directory.exists():
            files.extend(sorted(path for path in directory.iterdir() if path.is_file()))
    return files


def build_manifest(files: list[Path]) -> dict[str, Any]:
    serial_logs = [file_entry(path) for path in EXPECTED_SERIAL_LOGS]
    return {
        'generated_at_utc': datetime.now(UTC).isoformat(timespec='seconds'),
        'lane_dir': str(LANE_DIR),
        'included_files': [file_entry(path) for path in files],
        'expected_serial_logs': serial_logs,
        'all_serial_logs_present': all(entry['exists'] for entry in serial_logs),
        'notes': [
            'Bundle includes persistent artifacts only; runtime serial logs are present only after booted-Tails capture.',
            'Use --require-serial-logs when promoting completed real-boot evidence.',
        ],
    }


def relname(path: Path) -> str:
    return str(path.relative_to(LANE_DIR.parent.parent.parent))


def write_bundle(files: list[Path], manifest: dict[str, Any], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')
    archive = output_dir / f'real-boot-evidence-{stamp}.tar.gz'
    manifest_bytes = json.dumps(manifest, indent=2, sort_keys=True).encode('utf-8')
    with tarfile.open(archive, 'w:gz') as tar:
        for path in files:
            tar.add(path, arcname=relname(path))
        info = tarfile.TarInfo('real-boot-evidence-manifest.json')
        info.size = len(manifest_bytes)
        info.mtime = int(datetime.now(UTC).timestamp())
        import io

        tar.addfile(info, io.BytesIO(manifest_bytes))
    return archive


def bundle(output_dir: Path, require_serial_logs: bool) -> dict[str, Any]:
    files = collect_files()
    manifest = build_manifest(files)
    if require_serial_logs and not manifest['all_serial_logs_present']:
        missing = [entry['path'] for entry in manifest['expected_serial_logs'] if not entry['exists']]
        return {
            'success': False,
            'missing_serial_logs': missing,
            'all_serial_logs_present': False,
        }
    archive = write_bundle(files, manifest, output_dir)
    latest = output_dir / 'latest.tar.gz'
    latest.write_bytes(archive.read_bytes())
    manifest_path = output_dir / 'latest.manifest.json'
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    return {
        'success': True,
        'archive': str(archive),
        'latest_archive': str(latest),
        'manifest': str(manifest_path),
        'file_count': len(files),
        'all_serial_logs_present': manifest['all_serial_logs_present'],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description='Bundle persistent real-boot evidence artifacts into a tar.gz archive.')
    parser.add_argument('--output-dir', type=Path, default=BUNDLE_DIR)
    parser.add_argument('--require-serial-logs', action='store_true')
    args = parser.parse_args()
    result = bundle(args.output_dir, args.require_serial_logs)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get('success') else 1


if __name__ == '__main__':
    raise SystemExit(main())
