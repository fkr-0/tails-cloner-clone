#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
LANE_DIR = Path(__file__).resolve().parent
OUT_DIR = LANE_DIR / 'out/fsuuid'
SERIAL_DIR = LANE_DIR / 'out/serial-logs'
EVIDENCE_JSON = OUT_DIR / 'fsuuid-direct-kernel-evidence.json'

KERNEL = OUT_DIR / 'vmlinuz-7.7.2'
INITRD = OUT_DIR / 'initrd-fsuuid-rfc-7.7.2.img'
BOOT_IMAGE = OUT_DIR / 'tails-amd64-7.7.2-boot-8g.img'
EXTRA_IMAGE = OUT_DIR / 'tails-amd64-7.7.1-extra.img'
LOG = SERIAL_DIR / 'fsuuid-direct-kernel-controlled.log'
FSUUID = 'A690-20D2'
EXTRA_UUID = 'BEEF-1234'

APPEND_ARGS = [
    'boot=live',
    'config',
    'live-media=removable',
    'nopersistence',
    'noprompt',
    'timezone=Etc/UTC',
    'module=Tails',
    'slab_nomerge',
    'slub_debug=FZ',
    'mce=0',
    'vsyscall=none',
    'init_on_free=1',
    'mds=full,nosmt',
    'page_alloc.shuffle=1',
    'randomize_kstack_offset=on',
    'efi_pstore.pstore_disable=1',
    'erst_disable',
    'spec_store_bypass_disable=on',
    'systemd.condition_needs_update=no',
    f'FSUUID={FSUUID}',
    'console=ttyS0,115200n8',
]

SUCCESS_PATTERNS = {
    'cmdline_has_fsuuid': re.compile(rf'FSUUID={re.escape(FSUUID)}'),
    'fsuuid_probe_checked': re.compile(rf'/dev/disk/by-uuid/{re.escape(FSUUID)}'),
    'fsuuid_selected_vda1': re.compile(r'SYSTEM_PARTITION=/dev/vda1|System partition is available at /dev/vda1'),
    'first_boot_repartition_started': re.compile(r'This is the first boot, so repartitioning'),
    'root_mount_started': re.compile(r'Begin: Mounting root file system'),
}

FAILURE_PATTERNS = {
    'selected_extra_uuid': re.compile(rf'/dev/disk/by-uuid/{re.escape(EXTRA_UUID)}|SYSTEM_PARTITION=/dev/vdb1|System partition is available at /dev/vdb1'),
    'panic': re.compile(r'panic|Kernel panic|Unable to find a medium containing a live file system', re.IGNORECASE),
    'too_small_guard': re.compile(r'too small to run Tails', re.IGNORECASE),
}


def require_artifacts() -> None:
    missing = [path for path in [KERNEL, INITRD, BOOT_IMAGE, EXTRA_IMAGE] if not path.exists()]
    if missing:
        raise SystemExit('missing FSUUID smoke artifacts: ' + ', '.join(str(path) for path in missing))


def append_args(*, noautologin: bool) -> list[str]:
    args = list(APPEND_ARGS)
    if noautologin:
        args.insert(args.index('module=Tails'), 'noautologin')
    return args


def qemu_command(memory_mb: int, cpus: int, noautologin: bool) -> list[str]:
    return [
        'qemu-system-x86_64',
        '-machine', 'q35,accel=kvm:tcg',
        '-cpu', 'max',
        '-m', str(memory_mb),
        '-smp', str(cpus),
        '-kernel', str(KERNEL),
        '-initrd', str(INITRD),
        '-append', ' '.join(append_args(noautologin=noautologin)),
        '-drive', f'file={BOOT_IMAGE},format=raw,if=virtio,snapshot=on',
        '-drive', f'file={EXTRA_IMAGE},format=raw,if=virtio,snapshot=on',
        '-display', 'none',
        '-serial', f'file:{LOG}',
        '-net', 'none',
    ]


def read_log() -> str:
    if not LOG.exists():
        return ''
    return LOG.read_text(errors='replace')


def analyze_log(log: str) -> dict[str, Any]:
    success = {name: bool(pattern.search(log)) for name, pattern in SUCCESS_PATTERNS.items()}
    failures = {name: bool(pattern.search(log)) for name, pattern in FAILURE_PATTERNS.items()}
    selected = ''
    if re.search(r'SYSTEM_PARTITION=/dev/vda1|System partition is available at /dev/vda1', log):
        selected = '/dev/vda1'
    elif re.search(r'SYSTEM_PARTITION=/dev/vdb1|System partition is available at /dev/vdb1', log):
        selected = '/dev/vdb1'
    enough = all(success[name] for name in ['cmdline_has_fsuuid', 'fsuuid_probe_checked', 'fsuuid_selected_vda1'])
    return {
        'success_checks': success,
        'failure_checks': failures,
        'selected_system_partition': selected,
        'fsuuid_device_selected': selected == '/dev/vda1',
        'extra_device_selected': selected == '/dev/vdb1',
        'early_fsuuid_evidence_complete': bool(enough and not any(failures.values())),
        'log_size_bytes': len(log.encode(errors='replace')),
        'log_tail': log[-8000:],
    }


def terminate_process(process: subprocess.Popen[str], grace_seconds: float = 5.0) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=grace_seconds)


def run_smoke(timeout_seconds: int, memory_mb: int, cpus: int, stop_after_early_evidence: bool, noautologin: bool) -> dict[str, Any]:
    require_artifacts()
    SERIAL_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if LOG.exists():
        LOG.unlink()

    cmd = qemu_command(memory_mb=memory_mb, cpus=cpus, noautologin=noautologin)
    start = time.time()
    process = subprocess.Popen(cmd, cwd=REPO_ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stop_reason = 'timeout'
    try:
        while time.time() - start < timeout_seconds:
            if process.poll() is not None:
                stop_reason = f'qemu-exited-{process.returncode}'
                break
            log = read_log()
            analysis = analyze_log(log)
            if stop_after_early_evidence and analysis['early_fsuuid_evidence_complete']:
                stop_reason = 'early-fsuuid-evidence-complete'
                break
            time.sleep(2)
    finally:
        terminate_process(process)

    stdout, stderr = process.communicate(timeout=5)
    elapsed = time.time() - start
    log = read_log()
    analysis = analyze_log(log)
    result: dict[str, Any] = {
        'variant': 'fsuuid-two-valid-media-direct-kernel-smoke',
        'status': 'passed' if analysis['early_fsuuid_evidence_complete'] else 'incomplete',
        'stop_reason': stop_reason,
        'elapsed_seconds': round(elapsed, 2),
        'qemu_returncode': process.returncode,
        'kernel': str(KERNEL),
        'initrd': str(INITRD),
        'boot_image': str(BOOT_IMAGE),
        'boot_image_expected_uuid': FSUUID,
        'extra_image': str(EXTRA_IMAGE),
        'extra_image_expected_uuid': EXTRA_UUID,
        'serial_log': str(LOG),
        'append': ' '.join(append_args(noautologin=noautologin)),
        'noautologin': noautologin,
        'command': cmd,
        'stdout_tail': stdout[-2000:],
        'stderr_tail': stderr[-4000:],
        'analysis': analysis,
        'scope_note': 'This smoke validates early initramfs FSUUID device selection. It does not replace the full in-guest probe/GUI boot contract.',
    }
    EVIDENCE_JSON.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description='Controlled direct-kernel QEMU smoke for the FSUUID live-boot RFC patch.')
    parser.add_argument('--timeout', type=int, default=180, help='Maximum seconds to let QEMU run. Default: 180')
    parser.add_argument('--memory-mb', type=int, default=2048, help='QEMU memory in MiB. Default: 2048')
    parser.add_argument('--cpus', type=int, default=1, help='QEMU vCPUs. Default: 1')
    parser.add_argument('--no-stop-after-early-evidence', action='store_true', help='Let QEMU run until timeout instead of stopping when early FSUUID evidence is complete.')
    parser.add_argument('--noautologin', action='store_true', help='Add noautologin to the kernel command line. By default the runner omits it so Tails can autologin for e2e probing.')
    parser.add_argument('--dry-run', action='store_true', help='Validate artifacts and print the QEMU command without launching it.')
    parser.add_argument('--json', action='store_true', help='Print full JSON evidence.')
    args = parser.parse_args()

    require_artifacts()
    if args.dry_run:
        result: dict[str, Any] = {
            'variant': 'fsuuid-two-valid-media-direct-kernel-smoke',
            'status': 'dry-run',
            'command': qemu_command(memory_mb=args.memory_mb, cpus=args.cpus, noautologin=args.noautologin),
            'kernel': str(KERNEL),
            'initrd': str(INITRD),
            'boot_image': str(BOOT_IMAGE),
            'extra_image': str(EXTRA_IMAGE),
            'append': ' '.join(append_args(noautologin=args.noautologin)),
            'noautologin': args.noautologin,
        }
    else:
        result = run_smoke(
            timeout_seconds=args.timeout,
            memory_mb=args.memory_mb,
            cpus=args.cpus,
            stop_after_early_evidence=not args.no_stop_after_early_evidence,
            noautologin=args.noautologin,
        )

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"FSUUID direct-kernel smoke {result['status']}: {result.get('stop_reason', 'dry-run')}")
        if result.get('analysis'):
            analysis = result['analysis']
            print(f"selected_system_partition={analysis['selected_system_partition']}")
            print(f"early_fsuuid_evidence_complete={analysis['early_fsuuid_evidence_complete']}")
            print(f"serial_log={result['serial_log']}")
            print(f"evidence_json={EVIDENCE_JSON}")
    return 0 if result['status'] in {'passed', 'dry-run'} else 1


if __name__ == '__main__':
    raise SystemExit(main())
