#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
LANE_DIR = Path(__file__).resolve().parent
LANE_PATH = LANE_DIR / 'lane.yml'
BOOT_MATRIX_PATH = LANE_DIR / 'boot_matrix.yml'
GUEST_PROBE_CONTRACT_PATH = LANE_DIR / 'guest_probe_contract.yml'
DEFAULT_CACHE_DIRS = [
    REPO_ROOT / '.cache/vagrant-lab/tails-images',
    Path('/workspace/tails-cloner/.cache/vagrant-lab/tails-images'),
    Path('/opt/tails-cloner-fixtures/tails-images'),
]
EXPECTED_LIVE_VERSION_PATH = '/lib/live/mount/medium/live/Tails.version'


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f'missing required real-boot metadata file: {path}')
    return yaml.safe_load(path.read_text(encoding='utf-8'))


def cached_images(cache_dirs: list[Path]) -> list[Path]:
    images: list[Path] = []
    for cache_dir in cache_dirs:
        if cache_dir.exists():
            images.extend(sorted(cache_dir.glob('tails-amd64-*.img')))
    return sorted(set(images))


def qemu_available() -> bool:
    return shutil.which('qemu-system-x86_64') is not None


def build_plan(variant_name: str, variant: dict[str, Any], images: list[Path]) -> dict[str, Any]:
    image_names = [image.name for image in images]
    return {
        'variant': variant_name,
        'scenario_refs': variant['scenario_refs'],
        'status': variant['status'],
        'controller_image_role': variant['controller_image_role'],
        'source_role': variant['source_role'],
        'target_role': variant['target_role'],
        'destructive_by_default': variant['destructive_by_default'],
        'qemu_binary_available': qemu_available(),
        'cached_images': image_names,
        'expected_live_version_path': EXPECTED_LIVE_VERSION_PATH,
        'uses_snapshot_mode': True,
        'host_mutation_expected': False,
        'ready_for_manual_boot_attempt': bool(images) and qemu_available(),
        'next_step': variant['next_step'],
    }


def validate_lane(lane: dict[str, Any]) -> None:
    assert lane['lane'] == 'real_boot_qemu'
    assert lane['safety']['default_destructive_writes'] is False
    assert lane['safety']['host_mutation_expected'] is False
    assert lane['safety']['uses_temporary_overlay_or_snapshot'] is True
    assert lane['acceptance_checks']


def validate_guest_probe_contract(contract: dict[str, Any], matrix: dict[str, Any]) -> None:
    if contract.get('contract') != 'tails_guest_readiness_probe':
        raise SystemExit('guest probe contract has unexpected contract name')
    variants = set((matrix.get('variants') or {}).keys())
    requirement_variants = set((contract.get('variant_requirements') or {}).keys())
    missing = sorted(variants - requirement_variants)
    if missing:
        raise SystemExit(f'guest probe contract missing variant requirements: {missing}')
    gate = contract.get('completion_gate', {}).get('implemented_status_requires') or []
    if not any('qmp_probe.success' in item for item in gate):
        raise SystemExit('guest probe completion gate must require qmp_probe.success')
    if not any('guest_probe_output' in item for item in gate):
        raise SystemExit('guest probe completion gate must require guest_probe_output')


def validate_boot_matrix(matrix: dict[str, Any]) -> None:
    required_variants = {
        'running-live-install',
        'outdated-running-iso-upgrade',
        'outdated-running-source-device-upgrade',
    }
    variants = matrix.get('variants') or {}
    missing = sorted(required_variants - set(variants))
    if missing:
        raise SystemExit(f'boot matrix missing variants: {missing}')
    for name, variant in variants.items():
        if variant.get('destructive_by_default') is not False:
            raise SystemExit(f'{name}: real-boot variants must be non-destructive by default')
        if not variant.get('scenario_refs'):
            raise SystemExit(f'{name}: missing scenario_refs')
        if not variant.get('in_guest_checks'):
            raise SystemExit(f'{name}: missing in_guest_checks')
        if not variant.get('next_step'):
            raise SystemExit(f'{name}: missing next_step')


def main() -> int:
    parser = argparse.ArgumentParser(description='Validate real-boot lane metadata and print non-executing boot plans.')
    parser.add_argument('--json', action='store_true', help='Print machine-readable JSON instead of human text.')
    args = parser.parse_args()

    lane = load_yaml(LANE_PATH)
    matrix = load_yaml(BOOT_MATRIX_PATH)
    guest_probe_contract = load_yaml(GUEST_PROBE_CONTRACT_PATH)
    validate_lane(lane)
    validate_boot_matrix(matrix)
    validate_guest_probe_contract(guest_probe_contract, matrix)
    images = cached_images(DEFAULT_CACHE_DIRS)
    plans = [build_plan(name, variant, images) for name, variant in matrix['variants'].items()]
    result = {
        'lane': lane['lane'],
        'status': 'preflight_passed',
        'metadata_files': [
            str(LANE_PATH.relative_to(REPO_ROOT)),
            str(BOOT_MATRIX_PATH.relative_to(REPO_ROOT)),
            str(GUEST_PROBE_CONTRACT_PATH.relative_to(REPO_ROOT)),
        ],
        'guest_probe_contract': guest_probe_contract['contract'],
        'guest_probe_status': guest_probe_contract['status'],
        'qemu_binary_available': qemu_available(),
        'cached_image_count': len(images),
        'plans': plans,
    }

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"real-boot lane preflight: {result['status']}")
        print(f"qemu-system-x86_64 available: {result['qemu_binary_available']}")
        print(f"cached Tails images: {result['cached_image_count']}")
        for plan in plans:
            readiness = 'ready' if plan['ready_for_manual_boot_attempt'] else 'blocked'
            print(f"- {plan['variant']}: {readiness}; scenarios={','.join(plan['scenario_refs'])}; next={plan['next_step']}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
