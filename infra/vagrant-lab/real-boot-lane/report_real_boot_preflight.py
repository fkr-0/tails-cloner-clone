#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
LANE_DIR = Path(__file__).resolve().parent
TAILS_IMAGE_CACHE = REPO_ROOT / '.cache/vagrant-lab/tails-images'
CAPTURE_MEDIA_DIR = REPO_ROOT / '.cache/vagrant-lab/capture-media'
RUNBOOK_DIR = LANE_DIR / 'out' / 'capture-runbooks'
README = RUNBOOK_DIR / 'README.md'
REQUIRED_TOOLS = [
    'qemu-system-x86_64',
    'timeout',
    'python3',
    'parted',
    'mkfs.vfat',
    'mcopy',
]
RUNBOOK_VARIANTS = [
    'running-live-install',
    'outdated-running-iso-upgrade',
    'outdated-running-source-device-upgrade',
]
MEDIA_FILES = {
    'persistent_target_media': CAPTURE_MEDIA_DIR / 'persistent-target-media.img',
    'newer_attached_source_media': CAPTURE_MEDIA_DIR / 'newer-attached-source-media.img',
}


def tool_status() -> dict[str, dict[str, Any]]:
    return {
        tool: {
            'found': shutil.which(tool) is not None,
            'path': shutil.which(tool),
        }
        for tool in REQUIRED_TOOLS
    }


def cached_images() -> list[dict[str, Any]]:
    if not TAILS_IMAGE_CACHE.exists():
        return []
    return [
        {
            'path': str(path),
            'name': path.name,
            'size_bytes': path.stat().st_size,
        }
        for path in sorted(TAILS_IMAGE_CACHE.glob('tails-amd64-*.img'))
    ]


def media_status() -> dict[str, dict[str, Any]]:
    return {
        role: {
            'path': str(path),
            'exists': path.exists(),
            'size_bytes': path.stat().st_size if path.exists() else None,
        }
        for role, path in MEDIA_FILES.items()
    }


def runbook_status() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for variant in RUNBOOK_VARIANTS:
        path = RUNBOOK_DIR / f'{variant}.sh'
        result[variant] = {
            'path': str(path),
            'exists': path.exists(),
            'executable': os.access(path, os.X_OK) if path.exists() else False,
        }
    return result


def display_status() -> dict[str, Any]:
    return {
        'DISPLAY': os.environ.get('DISPLAY'),
        'WAYLAND_DISPLAY': os.environ.get('WAYLAND_DISPLAY'),
        'graphical_hint_available': bool(os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')),
    }


def kvm_status() -> dict[str, Any]:
    kvm = Path('/dev/kvm')
    return {
        'path': str(kvm),
        'exists': kvm.exists(),
        'readable': os.access(kvm, os.R_OK) if kvm.exists() else False,
        'writable': os.access(kvm, os.W_OK) if kvm.exists() else False,
    }


def preflight() -> dict[str, Any]:
    tools = tool_status()
    images = cached_images()
    media = media_status()
    runbooks = runbook_status()
    readme_status = {
        'path': str(README),
        'exists': README.exists(),
    }
    checks = {
        'tools_ready': all(item['found'] for item in tools.values()),
        'cached_images_ready': len(images) >= 2,
        'media_ready': all(item['exists'] for item in media.values()),
        'runbooks_ready': all(item['exists'] and item['executable'] for item in runbooks.values()),
        'readme_ready': readme_status['exists'],
    }
    return {
        'ready_for_attempt': all(checks.values()),
        'checks': checks,
        'tools': tools,
        'cached_images': images,
        'media': media,
        'runbooks': runbooks,
        'operator_readme': readme_status,
        'display': display_status(),
        'kvm': kvm_status(),
        'notes': [
            'DISPLAY/WAYLAND and /dev/kvm are hints, not hard gates: QEMU may still run without acceleration or with a different display setup.',
            'Run real-boot-prepare-runbooks before this preflight when media/runbooks are missing.',
        ],
    }


def print_human(report: dict[str, Any]) -> None:
    print('real-boot preflight')
    print(f"ready_for_attempt: {report['ready_for_attempt']}")
    print('checks:')
    for name, value in report['checks'].items():
        print(f'  {name}: {value}')
    print('tools:')
    for name, status in report['tools'].items():
        print(f"  {name}: found={status['found']} path={status['path']}")
    print(f"cached_images: {len(report['cached_images'])}")
    print('media:')
    for role, status in report['media'].items():
        print(f"  {role}: exists={status['exists']} path={status['path']}")
    print('runbooks:')
    for variant, status in report['runbooks'].items():
        print(f"  {variant}: exists={status['exists']} executable={status['executable']}")
    print(f"operator_readme: exists={report['operator_readme']['exists']} path={report['operator_readme']['path']}")
    print(f"display: graphical_hint_available={report['display']['graphical_hint_available']}")
    print(f"kvm: exists={report['kvm']['exists']} readable={report['kvm']['readable']} writable={report['kvm']['writable']}")


def main() -> int:
    parser = argparse.ArgumentParser(description='Report host/runtime preflight status for real-boot capture attempts.')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--require-ready', action='store_true')
    args = parser.parse_args()
    report = preflight()
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_human(report)
    return 0 if not args.require_ready or report['ready_for_attempt'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
