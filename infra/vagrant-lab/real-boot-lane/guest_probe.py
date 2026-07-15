#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import shlex
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_LIVE_VERSION_PATH = Path('/lib/live/mount/medium/live/Tails.version')
DEFAULT_PROJECT_PATHS = [
    Path('/workspace/tails-cloner'),
    Path('/home/amnesia/tails-cloner'),
    Path('/mnt/tails-cloner'),
]
PROBE_PREFIX = 'TAILS_CLONER_GUEST_PROBE='


def read_text_if_present(path: Path) -> str:
    try:
        if path.is_file():
            return path.read_text(encoding='utf-8', errors='replace').strip()
    except OSError:
        return ''
    return ''


def run_json(command: list[str]) -> dict[str, Any]:
    try:
        result = subprocess.run(command, check=False, text=True, capture_output=True)
    except OSError:
        return {}
    if result.returncode != 0:
        return {}
    try:
        return json.loads(result.stdout or '{}')
    except json.JSONDecodeError:
        return {}


def parent_disk_path(path: str) -> str:
    if not path.startswith('/dev/'):
        return path
    name = Path(path).name
    if name.startswith(('nvme', 'mmcblk')) and 'p' in name:
        return '/dev/' + name.rsplit('p', 1)[0]
    while name and name[-1].isdigit():
        name = name[:-1]
    return '/dev/' + name


def live_version_info(live_version_path: Path) -> dict[str, Any]:
    content = read_text_if_present(live_version_path)
    return {
        'path': str(live_version_path),
        'exists': bool(content),
        'content': content,
    }


def running_tails_detection(live_version_path: Path, source_device: str | None) -> dict[str, Any]:
    version = read_text_if_present(live_version_path)
    running_device = source_device or ''
    size_bytes = 0
    if running_device:
        try:
            result = subprocess.run(
                ['blockdev', '--getsize64', parent_disk_path(running_device)],
                check=False,
                text=True,
                capture_output=True,
            )
            if result.returncode == 0:
                size_bytes = int(result.stdout.strip() or '0')
        except (OSError, ValueError):
            size_bytes = 0
    return {
        'is_running_tails': bool(version),
        'running_tails_version': version,
        'running_tails_device': running_device,
        'running_tails_size_bytes': size_bytes,
    }


def lsblk_devices() -> list[dict[str, Any]]:
    data = run_json(['lsblk', '--json', '--bytes', '--output', 'PATH,TYPE,FSTYPE,LABEL,SIZE'])
    return list(data.get('blockdevices') or [])


def flatten_disks(devices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for device in devices:
        if device.get('path'):
            flattened.append(device)
        for child in device.get('children') or []:
            if child.get('path'):
                flattened.append(child)
    return flattened


def block_device_info(source_device: str | None) -> dict[str, Any]:
    source_parent = parent_disk_path(source_device) if source_device else ''
    candidates = []
    for device in flatten_disks(lsblk_devices()):
        path = str(device.get('path') or '')
        if not path.startswith('/dev/'):
            continue
        if device.get('type') not in {'disk', 'part'}:
            continue
        label = str(device.get('label') or '')
        has_tails = label in {'TAILS', 'TAILS_SRC'}
        candidates.append(
            {
                'path': path,
                'has_tails': has_tails,
                'excluded_because_source': bool(source_parent and parent_disk_path(path) == source_parent),
            }
        )
    return {
        'source_parent_disk': source_parent,
        'target_candidates': candidates,
    }


def proc_cmdline() -> str:
    return read_text_if_present(Path('/proc/cmdline'))


def cmdline_options() -> dict[str, str | bool]:
    options: dict[str, str | bool] = {}
    for token in shlex.split(proc_cmdline()):
        if '=' in token:
            key, value = token.split('=', 1)
            options[key] = value
        else:
            options[token] = True
    return options


def filesystem_uuid_resolution(uuid: str) -> dict[str, Any]:
    by_uuid = Path('/dev/disk/by-uuid') / uuid
    exists = by_uuid.exists()
    resolved = ''
    if exists:
        try:
            resolved = str(by_uuid.resolve())
        except OSError:
            resolved = ''
    return {
        'uuid': uuid,
        'path': str(by_uuid),
        'exists': exists,
        'resolved': resolved,
        'parent_disk': parent_disk_path(resolved) if resolved else '',
    }


def mount_source_for(path: Path) -> str:
    try:
        result = subprocess.run(
            ['findmnt', '--noheadings', '--output', 'SOURCE', '--target', str(path)],
            check=False,
            text=True,
            capture_output=True,
        )
    except OSError:
        return ''
    if result.returncode != 0:
        return ''
    return result.stdout.strip().splitlines()[0] if result.stdout.strip() else ''


def blkid_export(path: str) -> dict[str, str]:
    if not path:
        return {}
    try:
        result = subprocess.run(
            ['blkid', '-o', 'export', path],
            check=False,
            text=True,
            capture_output=True,
        )
    except OSError:
        return {}
    if result.returncode != 0:
        return {}
    data: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if '=' in line:
            key, value = line.split('=', 1)
            data[key] = value
    return data


def live_medium_identity(live_version_path: Path) -> dict[str, Any]:
    medium_path = live_version_path.parent.parent
    source = mount_source_for(medium_path)
    blkid = blkid_export(source)
    return {
        'medium_path': str(medium_path),
        'mount_source': source,
        'mount_source_parent_disk': parent_disk_path(source) if source else '',
        'blkid': blkid,
    }


def tails_media_devices() -> list[dict[str, Any]]:
    devices = []
    data = run_json(['lsblk', '--json', '--bytes', '--output', 'PATH,TYPE,FSTYPE,LABEL,UUID,SIZE'])
    for device in flatten_disks(list(data.get('blockdevices') or [])):
        path = str(device.get('path') or '')
        if not path.startswith('/dev/'):
            continue
        label = str(device.get('label') or '')
        if label not in {'TAILS', 'TAILS_SRC'}:
            continue
        devices.append(
            {
                'path': path,
                'parent_disk': parent_disk_path(path),
                'fstype': str(device.get('fstype') or ''),
                'label': label,
                'uuid': str(device.get('uuid') or ''),
                'size': int(device.get('size') or 0),
            }
        )
    return devices


def fsuuid_boot_evidence(live_version_path: Path) -> dict[str, Any]:
    options = cmdline_options()
    fsuuid = str(options.get('FSUUID') or '')
    resolution = filesystem_uuid_resolution(fsuuid) if fsuuid else {}
    live_medium = live_medium_identity(live_version_path)
    return {
        'proc_cmdline': proc_cmdline(),
        'cmdline_options': options,
        'fsuuid': fsuuid,
        'fsuuid_resolution': resolution,
        'live_medium': live_medium,
        'tails_media_devices': tails_media_devices(),
        'live_medium_matches_fsuuid': bool(
            fsuuid
            and resolution.get('parent_disk')
            and live_medium.get('mount_source_parent_disk')
            and resolution.get('parent_disk') == live_medium.get('mount_source_parent_disk')
        ),
    }


def project_access(project_path: Path | None) -> dict[str, Any]:
    if project_path is None:
        for candidate in DEFAULT_PROJECT_PATHS:
            if candidate.exists():
                project_path = candidate
                break
    checkout_visible = bool(project_path and project_path.exists())
    importable = False
    if project_path:
        source_init = project_path / 'src' / 'tails_cloner' / '__init__.py'
        importable = source_init.exists() or importlib.util.find_spec('tails_cloner') is not None
    return {
        'checkout_visible': checkout_visible,
        'python_import_tails_cloner': importable,
    }


def collect_probe(
    *,
    scenario_variant: str,
    transport: str,
    live_version_path: Path,
    source_device: str | None,
    project_path: Path | None,
) -> dict[str, Any]:
    return {
        'transport': transport,
        'timestamp_utc': datetime.now(UTC).isoformat(),
        'scenario_variant': scenario_variant,
        'live_version_path': live_version_info(live_version_path),
        'running_tails_detection': running_tails_detection(live_version_path, source_device),
        'block_devices': block_device_info(source_device),
        'fsuuid_boot': fsuuid_boot_evidence(live_version_path),
        'project_access': project_access(project_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description='Emit tails-cloner real-boot guest readiness probe JSON.')
    parser.add_argument('--scenario-variant', required=True)
    parser.add_argument('--transport', default='manual')
    parser.add_argument('--live-version-path', type=Path, default=DEFAULT_LIVE_VERSION_PATH)
    parser.add_argument('--source-device', help='Expected running/source device or partition path, if known.')
    parser.add_argument('--project-path', type=Path)
    parser.add_argument('--prefix', action='store_true', help='Prefix JSON with TAILS_CLONER_GUEST_PROBE= for serial logs.')
    args = parser.parse_args()

    probe = collect_probe(
        scenario_variant=args.scenario_variant,
        transport=args.transport,
        live_version_path=args.live_version_path,
        source_device=args.source_device,
        project_path=args.project_path,
    )
    output = json.dumps(probe, sort_keys=True)
    if args.prefix:
        print(PROBE_PREFIX + output)
    else:
        print(output)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
