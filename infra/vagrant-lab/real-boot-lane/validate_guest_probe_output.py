#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml

LANE_DIR = Path(__file__).resolve().parent
CONTRACT_PATH = LANE_DIR / 'guest_probe_contract.yml'
PROBE_PREFIX = 'TAILS_CLONER_GUEST_PROBE='


class ValidationError(RuntimeError):
    pass


def load_contract(path: Path = CONTRACT_PATH) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding='utf-8'))


def extract_probe_from_log(path: Path) -> dict[str, Any]:
    for line in reversed(path.read_text(encoding='utf-8', errors='replace').splitlines()):
        if line.startswith(PROBE_PREFIX):
            return json.loads(line.removeprefix(PROBE_PREFIX))
    raise ValidationError(f'no {PROBE_PREFIX!r} line found in {path}')


def load_probe(json_file: Path | None, log_file: Path | None) -> dict[str, Any]:
    if json_file and log_file:
        raise ValidationError('choose either --json-file or --log-file, not both')
    if json_file:
        return json.loads(json_file.read_text(encoding='utf-8'))
    if log_file:
        return extract_probe_from_log(log_file)
    raise ValidationError('missing --json-file or --log-file')


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def validate_required_schema(probe: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    require(isinstance(probe.get('transport'), str) and bool(probe.get('transport')), 'transport must be a non-empty string', failures)
    require(isinstance(probe.get('timestamp_utc'), str) and bool(probe.get('timestamp_utc')), 'timestamp_utc must be a non-empty string', failures)
    require(isinstance(probe.get('scenario_variant'), str) and bool(probe.get('scenario_variant')), 'scenario_variant must be a non-empty string', failures)

    live = probe.get('live_version_path')
    require(isinstance(live, dict), 'live_version_path must be an object', failures)
    if isinstance(live, dict):
        require(isinstance(live.get('path'), str), 'live_version_path.path must be a string', failures)
        require(isinstance(live.get('exists'), bool), 'live_version_path.exists must be a bool', failures)
        require(isinstance(live.get('content'), str), 'live_version_path.content must be a string', failures)

    running = probe.get('running_tails_detection')
    require(isinstance(running, dict), 'running_tails_detection must be an object', failures)
    if isinstance(running, dict):
        require(isinstance(running.get('is_running_tails'), bool), 'running_tails_detection.is_running_tails must be a bool', failures)
        require(isinstance(running.get('running_tails_version'), str), 'running_tails_detection.running_tails_version must be a string', failures)
        require(isinstance(running.get('running_tails_device'), str), 'running_tails_detection.running_tails_device must be a string', failures)
        require(isinstance(running.get('running_tails_size_bytes'), int), 'running_tails_detection.running_tails_size_bytes must be an integer', failures)

    block = probe.get('block_devices')
    require(isinstance(block, dict), 'block_devices must be an object', failures)
    if isinstance(block, dict):
        require(isinstance(block.get('source_parent_disk'), str), 'block_devices.source_parent_disk must be a string', failures)
        candidates = block.get('target_candidates')
        require(isinstance(candidates, list), 'block_devices.target_candidates must be a list', failures)
        if isinstance(candidates, list):
            for index, candidate in enumerate(candidates):
                require(isinstance(candidate, dict), f'target_candidates[{index}] must be an object', failures)
                if isinstance(candidate, dict):
                    require(isinstance(candidate.get('path'), str), f'target_candidates[{index}].path must be a string', failures)
                    require(isinstance(candidate.get('has_tails'), bool), f'target_candidates[{index}].has_tails must be a bool', failures)
                    require(isinstance(candidate.get('excluded_because_source'), bool), f'target_candidates[{index}].excluded_because_source must be a bool', failures)

    fsuuid_boot = probe.get('fsuuid_boot')
    require(isinstance(fsuuid_boot, dict), 'fsuuid_boot must be an object', failures)
    if isinstance(fsuuid_boot, dict):
        require(isinstance(fsuuid_boot.get('proc_cmdline'), str), 'fsuuid_boot.proc_cmdline must be a string', failures)
        require(isinstance(fsuuid_boot.get('fsuuid'), str), 'fsuuid_boot.fsuuid must be a string', failures)
        require(isinstance(fsuuid_boot.get('fsuuid_resolution'), dict), 'fsuuid_boot.fsuuid_resolution must be an object', failures)
        require(isinstance(fsuuid_boot.get('live_medium'), dict), 'fsuuid_boot.live_medium must be an object', failures)
        require(isinstance(fsuuid_boot.get('tails_media_devices'), list), 'fsuuid_boot.tails_media_devices must be a list', failures)
        require(isinstance(fsuuid_boot.get('live_medium_matches_fsuuid'), bool), 'fsuuid_boot.live_medium_matches_fsuuid must be a bool', failures)

    project = probe.get('project_access')
    require(isinstance(project, dict), 'project_access must be an object', failures)
    if isinstance(project, dict):
        require(isinstance(project.get('checkout_visible'), bool), 'project_access.checkout_visible must be a bool', failures)
        require(isinstance(project.get('python_import_tails_cloner'), bool), 'project_access.python_import_tails_cloner must be a bool', failures)
    return failures


def any_source_target_excluded(probe: dict[str, Any]) -> bool:
    return any(
        bool(candidate.get('excluded_because_source'))
        for candidate in probe.get('block_devices', {}).get('target_candidates', [])
        if isinstance(candidate, dict)
    )


def any_tail_like_non_source_target(probe: dict[str, Any]) -> bool:
    return any(
        bool(candidate.get('has_tails')) and not bool(candidate.get('excluded_because_source'))
        for candidate in probe.get('block_devices', {}).get('target_candidates', [])
        if isinstance(candidate, dict)
    )


def validate_variant_requirements(probe: dict[str, Any], contract: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    variant = probe.get('scenario_variant')
    requirements = (contract.get('variant_requirements') or {}).get(variant)
    if not requirements:
        return [f'unknown or unsupported scenario_variant: {variant!r}']

    live = probe.get('live_version_path', {})
    running = probe.get('running_tails_detection', {})
    project = probe.get('project_access', {})

    require(live.get('exists') is True, 'live_version_path.exists must be true', failures)
    require(running.get('is_running_tails') is True, 'running_tails_detection.is_running_tails must be true', failures)
    require(bool(running.get('running_tails_device')), 'running_tails_detection.running_tails_device must be non-empty', failures)
    require(project.get('checkout_visible') is True, 'project_access.checkout_visible must be true', failures)
    require(project.get('python_import_tails_cloner') is True, 'project_access.python_import_tails_cloner must be true', failures)

    if variant == 'running-live-install':
        require(any_source_target_excluded(probe), 'source parent disk must be excluded from target candidates', failures)
    elif variant == 'outdated-running-iso-upgrade':
        require(any_tail_like_non_source_target(probe), 'persistent target disk must be visible as a non-source Tails target candidate', failures)
    elif variant == 'outdated-running-source-device-upgrade':
        require(any_source_target_excluded(probe), 'booted controller source must be represented and excluded', failures)
        require(any_tail_like_non_source_target(probe), 'newer source/target Tails-like disk must be visible as a non-source candidate', failures)
    elif variant == 'fsuuid-two-valid-media':
        fsuuid_boot = probe.get('fsuuid_boot', {})
        resolution = fsuuid_boot.get('fsuuid_resolution', {}) if isinstance(fsuuid_boot, dict) else {}
        media = fsuuid_boot.get('tails_media_devices', []) if isinstance(fsuuid_boot, dict) else []
        require(bool(fsuuid_boot.get('fsuuid')), 'fsuuid_boot.fsuuid must be non-empty', failures)
        require(resolution.get('exists') is True, 'fsuuid_boot.fsuuid_resolution.exists must be true', failures)
        require(bool(resolution.get('parent_disk')), 'fsuuid_boot.fsuuid_resolution.parent_disk must be non-empty', failures)
        require(len(media) >= 2, 'at least two TAILS-like media must be visible', failures)
        require(fsuuid_boot.get('live_medium_matches_fsuuid') is True, 'live medium must match FSUUID device', failures)
    return failures


def validate_probe(probe: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    schema_failures = validate_required_schema(probe)
    variant_failures = [] if schema_failures else validate_variant_requirements(probe, contract)
    failures = schema_failures + variant_failures
    return {
        'success': not failures,
        'scenario_variant': probe.get('scenario_variant'),
        'failures': failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description='Validate Tails real-boot guest probe output against the lane contract.')
    parser.add_argument('--json-file', type=Path, help='Path to raw guest_probe.py JSON output.')
    parser.add_argument('--log-file', type=Path, help='Path to a log containing TAILS_CLONER_GUEST_PROBE= JSON line.')
    parser.add_argument('--contract', type=Path, default=CONTRACT_PATH)
    args = parser.parse_args()

    try:
        probe = load_probe(args.json_file, args.log_file)
        result = validate_probe(probe, load_contract(args.contract))
    except (ValidationError, json.JSONDecodeError, OSError) as error:
        result = {'success': False, 'scenario_variant': None, 'failures': [str(error)]}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
