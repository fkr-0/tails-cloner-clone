#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
LANE_DIR = Path(__file__).resolve().parent
MATRIX_PATH = LANE_DIR / 'capture_session_matrix.yml'
CAPTURE_RUNNER = LANE_DIR / 'capture_guest_probe_from_qemu.py'
RECORDER = LANE_DIR / 'record_guest_probe_evidence.py'


def load_matrix(path: Path = MATRIX_PATH) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding='utf-8'))


def parse_role_mapping(values: list[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for value in values:
        if '=' not in value:
            raise SystemExit(f'invalid role mapping {value!r}; expected ROLE=PATH_OR_VERSION')
        role, resolved = value.split('=', 1)
        if not role or not resolved:
            raise SystemExit(f'invalid role mapping {value!r}; expected ROLE=PATH_OR_VERSION')
        mapping[role] = resolved
    return mapping


def validate_matrix(matrix: dict[str, Any]) -> None:
    variants = matrix.get('variants') or {}
    roles = matrix.get('attachment_roles') or {}
    required_variants = {
        'running-live-install',
        'outdated-running-iso-upgrade',
        'outdated-running-source-device-upgrade',
    }
    missing = sorted(required_variants - set(variants))
    if missing:
        raise SystemExit(f'missing capture variants: {missing}')
    for name, variant in variants.items():
        args = variant.get('capture_args') or {}
        version_role = args.get('version_role')
        if version_role not in roles:
            raise SystemExit(f'{name}: unknown version_role {version_role!r}')
        for role in args.get('extra_attachment_roles') or []:
            if role not in roles:
                raise SystemExit(f'{name}: unknown extra attachment role {role!r}')
        if not args.get('serial_log'):
            raise SystemExit(f'{name}: missing serial_log')


def build_capture_command(variant_name: str, variant: dict[str, Any], role_map: dict[str, str]) -> list[str]:
    args = variant['capture_args']
    version_role = args['version_role']
    if version_role not in role_map:
        raise SystemExit(f'{variant_name}: missing required mapping for {version_role}')
    command = [
        'python3',
        str(CAPTURE_RUNNER.relative_to(REPO_ROOT)),
        '--version',
        role_map[version_role],
        '--scenario-variant',
        args['scenario_variant'],
        '--serial-log',
        args['serial_log'],
    ]
    if args.get('interactive_display'):
        command.append('--interactive-display')
    if args.get('prepare_share'):
        command.append('--prepare-share')
    for role in args.get('extra_attachment_roles') or []:
        if role not in role_map:
            raise SystemExit(f'{variant_name}: missing required mapping for {role}')
        command.extend(['--extra-drive', role_map[role]])
    return command


def build_guest_command(share_tag: str = 'tailscloner', mount_point: str = '/mnt/tailscloner') -> str:
    return (
        f'sudo mkdir -p {mount_point} && '
        f'sudo mount -t 9p -o trans=virtio,version=9p2000.L,ro {share_tag} {mount_point} && '
        f'{mount_point}/run_guest_probe.sh'
    )


def build_record_command(variant: dict[str, Any]) -> list[str]:
    recorder_args = variant['recorder_args']
    command = [
        'python3',
        str(RECORDER.relative_to(REPO_ROOT)),
        '--log-file',
        recorder_args['log_file'],
    ]
    if recorder_args.get('mark_done'):
        command.append('--mark-done')
    return command


def plan_variant(variant_name: str, role_map: dict[str, str]) -> dict[str, Any]:
    matrix = load_matrix()
    validate_matrix(matrix)
    variants = matrix['variants']
    if variant_name not in variants:
        raise SystemExit(f'unknown capture variant {variant_name!r}')
    variant = variants[variant_name]
    return {
        'variant': variant_name,
        'scenario_ref': variant['scenario_ref'],
        'todo_ref': variant['todo_ref'],
        'capture_command': build_capture_command(variant_name, variant, role_map),
        'guest_step': variant['guest_step'],
        'guest_command': build_guest_command(),
        'record_command': build_record_command(variant),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description='Plan a real-boot guest-probe capture session for one variant.')
    parser.add_argument('--variant', required=True)
    parser.add_argument('--role', action='append', default=[], help='ROLE=VALUE mapping. Version roles use version strings; media roles use paths.')
    args = parser.parse_args()
    result = plan_variant(args.variant, parse_role_mapping(args.role))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
