#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
LANE_DIR = Path(__file__).resolve().parent
MATRIX_PATH = LANE_DIR / 'capture_session_matrix.yml'
PLANNER = LANE_DIR / 'plan_capture_session.py'
CACHE_DIRS = [
    REPO_ROOT / '.cache/vagrant-lab/tails-images',
    Path('/workspace/tails-cloner/.cache/vagrant-lab/tails-images'),
    Path('/opt/tails-cloner-fixtures/tails-images'),
]
VERSION_RE = re.compile(r'tails-amd64-(?P<version>[^/]+)\.img$')


def image_version(path: Path) -> str:
    match = VERSION_RE.search(path.name)
    return match.group('version') if match else path.stem


def sort_key(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for token in re.split(r'[^0-9]+', version):
        if token:
            parts.append(int(token))
    return tuple(parts or [0])


def cached_images(cache_dirs: list[Path] | None = None) -> list[dict[str, str]]:
    images: list[Path] = []
    for cache_dir in cache_dirs or CACHE_DIRS:
        if cache_dir.exists():
            images.extend(sorted(cache_dir.glob('tails-amd64-*.img')))
    unique = sorted(set(images), key=lambda path: sort_key(image_version(path)))
    return [{'version': image_version(path), 'path': str(path)} for path in unique]


def parse_role_overrides(values: list[str]) -> dict[str, str]:
    roles: dict[str, str] = {}
    for value in values:
        if '=' not in value:
            raise SystemExit(f'invalid role override {value!r}; expected ROLE=VALUE')
        role, resolved = value.split('=', 1)
        if not role or not resolved:
            raise SystemExit(f'invalid role override {value!r}; expected ROLE=VALUE')
        roles[role] = resolved
    return roles


def load_matrix(path: Path = MATRIX_PATH) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding='utf-8'))


def automatic_version_roles(images: list[dict[str, str]]) -> dict[str, str]:
    if not images:
        return {}
    newest = images[-1]['version']
    oldest = images[0]['version']
    return {
        'current_tails_img': newest,
        'outdated_controller_img': oldest,
    }


def required_roles(matrix: dict[str, Any], variant_name: str) -> list[str]:
    variant = matrix['variants'][variant_name]
    args = variant['capture_args']
    roles = [args['version_role']]
    roles.extend(args.get('extra_attachment_roles') or [])
    return roles


def role_kind(matrix: dict[str, Any], role: str) -> str:
    return str((matrix.get('attachment_roles') or {}).get(role, {}).get('value_kind') or 'unknown')


def media_path_status(matrix: dict[str, Any], role_map: dict[str, str], roles: list[str]) -> dict[str, dict[str, Any]]:
    status: dict[str, dict[str, Any]] = {}
    for role in roles:
        if role_kind(matrix, role) != 'media_path' or role not in role_map:
            continue
        path = Path(role_map[role])
        status[role] = {
            'path': str(path),
            'exists': path.exists(),
            'is_file': path.is_file(),
            'is_block_device': path.is_block_device(),
        }
    return status


def missing_media_paths(media_status: dict[str, dict[str, Any]]) -> list[str]:
    return [role for role, status in media_status.items() if not status['exists']]


def plan_command(variant: str, role_map: dict[str, str]) -> dict[str, Any] | None:
    command = ['python3', str(PLANNER), '--variant', variant]
    for role, value in role_map.items():
        command.extend(['--role', f'{role}={value}'])
    result = subprocess.run(command, check=False, text=True, capture_output=True)
    if result.returncode != 0:
        return {
            'success': False,
            'stderr': result.stderr,
            'stdout': result.stdout,
        }
    payload = json.loads(result.stdout)
    payload['success'] = True
    return payload


def resolve_variant(variant: str, overrides: dict[str, str], *, emit_plan: bool, require_existing: bool = False) -> dict[str, Any]:
    matrix = load_matrix()
    if variant not in matrix['variants']:
        raise SystemExit(f'unknown capture variant {variant!r}')
    images = cached_images()
    role_map = automatic_version_roles(images)
    role_map.update(overrides)
    roles = required_roles(matrix, variant)
    missing = [role for role in roles if role not in role_map]
    attachment_roles = set((matrix.get('attachment_roles') or {}).keys())
    unknown_overrides = sorted(set(overrides) - attachment_roles)
    path_status = media_path_status(matrix, role_map, roles)
    missing_paths = missing_media_paths(path_status)
    result: dict[str, Any] = {
        'variant': variant,
        'cached_images': images,
        'roles': {role: role_map[role] for role in roles if role in role_map},
        'missing_roles': missing,
        'unknown_overrides': unknown_overrides,
        'media_path_status': path_status,
        'missing_media_paths': missing_paths,
        'ready': not missing and not unknown_overrides and (not require_existing or not missing_paths),
    }
    if emit_plan and result['ready']:
        result['plan'] = plan_command(variant, {role: role_map[role] for role in roles})
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description='Resolve capture-session role mappings from cached artifacts and explicit media paths.')
    parser.add_argument('--variant', required=True)
    parser.add_argument('--role', action='append', default=[], help='Explicit ROLE=VALUE mapping for media paths or version overrides.')
    parser.add_argument('--emit-plan', action='store_true', help='Include the planned capture and record commands when all roles are resolved.')
    parser.add_argument('--require-existing-media', action='store_true', help='Require supplied media-path roles to exist before reporting ready.')
    args = parser.parse_args()
    result = resolve_variant(args.variant, parse_role_overrides(args.role), emit_plan=args.emit_plan, require_existing=args.require_existing_media)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result['ready'] or not args.require_existing_media else 1


if __name__ == '__main__':
    raise SystemExit(main())
