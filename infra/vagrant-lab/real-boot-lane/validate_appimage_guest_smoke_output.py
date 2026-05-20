#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

MARKER = 'TAILS_CLONER_APPIMAGE_SMOKE='


def extract_marker(log_text: str) -> dict[str, Any]:
    for line in reversed(log_text.splitlines()):
        if MARKER in line:
            payload = line.split(MARKER, 1)[1].strip()
            return json.loads(payload)
    raise ValueError(f'missing marker: {MARKER}')


def validate_payload(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if payload.get('status') != 'passed':
        errors.append(f"status is not passed: {payload.get('status')!r}")
    if payload.get('scope') != 'Tails guest AppImage smoke via 9p share':
        errors.append('unexpected scope')
    probes = payload.get('probes') or {}
    for name in ['apprun-help', 'source-running', 'devices-list']:
        rc = (probes.get(name) or {}).get('returncode')
        if rc != 0:
            errors.append(f'{name} returncode is not 0: {rc!r}')
    if not isinstance(payload.get('device_count'), int):
        errors.append('device_count is not an integer')
    if 'running_tails_available' not in payload:
        errors.append('missing running_tails_available')
    if payload.get('running_tails_available'):
        if not payload.get('running_parent_device'):
            errors.append('running Tails reported available but running_parent_device is empty')
        if payload.get('running_parent_visible') is not True:
            errors.append('running parent device is not visible in devices list')
        if payload.get('running_parent_selectable') is not False:
            errors.append('running parent device is not marked non-selectable')
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description='Validate TAILS_CLONER_APPIMAGE_SMOKE marker from a Tails guest serial log.')
    parser.add_argument('--log-file', type=Path, required=True)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()

    payload = extract_marker(args.log_file.read_text(errors='replace'))
    errors = validate_payload(payload)
    result = {
        'valid': not errors,
        'errors': errors,
        'payload': payload,
    }
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        if errors:
            print('AppImage guest smoke marker invalid')
            for error in errors:
                print(f'- {error}')
        else:
            print('AppImage guest smoke marker valid')
            print(f"device_count={payload.get('device_count')}")
            print(f"running_tails_available={payload.get('running_tails_available')}")
            print(f"running_parent_device={payload.get('running_parent_device')}")
    return 0 if not errors else 1


if __name__ == '__main__':
    raise SystemExit(main())
