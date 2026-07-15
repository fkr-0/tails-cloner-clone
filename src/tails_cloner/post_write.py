from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from tails_cloner.boot_loader import apply_boot_loader_order_to_directory
from tails_cloner.models import PostWriteOptions
from tails_cloner.upgrader import build_privileged_command, find_tails_system_partition

ProgressCallback = Callable[[str], None] | None
SyncRunner = Callable[[], None]
SleepRunner = Callable[[float], None]
TimestampProvider = Callable[[], datetime]
CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def _emit(progress_callback: ProgressCallback, message: str) -> None:
    if progress_callback is not None:
        progress_callback(message)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _append_audit_log(log_file_path: str, lines: list[str]) -> None:
    path = Path(log_file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def _find_tails_boot_partition(device_path: str) -> str:
    return find_tails_system_partition(device_path).path


def _run_checked(command: list[str], run: CommandRunner) -> subprocess.CompletedProcess[str]:
    return run(command, check=True, text=True, capture_output=True)


def _apply_boot_loader_order(
    device_path: str,
    options: PostWriteOptions,
    progress_callback: ProgressCallback,
    run: CommandRunner = subprocess.run,
) -> list[str]:
    if not options.boot_loader_order.enabled or not options.boot_loader_order.entries:
        return []

    boot_partition = _find_tails_boot_partition(device_path)
    changed_paths: list[str] = []
    _emit(progress_callback, f"Applying experimental boot-loader order on {boot_partition}...")

    with TemporaryDirectory(prefix="tails-cloner-boot-") as mount_dir:
        mount_path = Path(mount_dir)
        mounted = False
        try:
            mount_options = f"rw,uid={os.getuid()},gid={os.getgid()},umask=077"
            _run_checked(
                build_privileged_command(
                    ["mount", "-o", mount_options, "--", boot_partition, str(mount_path)]
                ),
                run,
            )
            mounted = True
            result = apply_boot_loader_order_to_directory(mount_path, options.boot_loader_order.entries)
            if not result.files:
                _emit(progress_callback, "No supported boot-loader config files found on boot partition.")
                return []
            for file_result in result.files:
                rel_path = file_result.path.relative_to(mount_path)
                if file_result.changed:
                    backup_rel = file_result.backup_path.relative_to(mount_path) if file_result.backup_path else None
                    changed_paths.append(str(rel_path))
                    _emit(
                        progress_callback,
                        f"Reordered {rel_path}; backup: {backup_rel or 'none'}",
                    )
                elif file_result.unsupported_reason:
                    _emit(progress_callback, f"Skipped {rel_path}: {file_result.unsupported_reason}")
                else:
                    _emit(progress_callback, f"No boot-loader order change needed for {rel_path}.")
            return changed_paths
        finally:
            if mounted:
                _run_checked(
                    build_privileged_command(["umount", "--", str(mount_path)]),
                    run,
                )


def apply_post_write_options(
    device_path: str,
    options: PostWriteOptions,
    progress_callback: ProgressCallback = None,
    sync_runner: SyncRunner = os.sync,
    sleep_runner: SleepRunner = time.sleep,
    timestamp_provider: TimestampProvider = _utc_now,
    command_runner: CommandRunner = subprocess.run,
) -> None:
    """Apply optional post-write customizations after a successful clone.

    Experimental boot-loader ordering is intentionally behind
    PostWriteOptions.enabled + PostWriteOptions.boot_loader_order.enabled.
    """
    if not options.enabled:
        return

    started_at = timestamp_provider()
    log_lines = [
        "post_write_start",
        f"started_at={started_at.isoformat()}",
        f"device_path={device_path}",
        f"sync_device={options.sync_device}",
        f"settle_seconds={options.settle_seconds}",
    ]
    if options.boot_loader_order.enabled:
        log_lines.append("boot_loader_order_enabled=true")
        log_lines.extend(f"boot_loader_order_entry={entry}" for entry in options.boot_loader_order.entries)

    _emit(progress_callback, f"Running optional post-write customizations for {device_path}...")

    if options.sync_device:
        _emit(progress_callback, "Flushing system buffers...")
        sync_runner()

    if options.settle_seconds > 0:
        _emit(progress_callback, f"Waiting {options.settle_seconds:.1f}s for device to settle...")
        sleep_runner(options.settle_seconds)

    changed_boot_files = _apply_boot_loader_order(device_path, options, progress_callback, command_runner)
    log_lines.extend(f"boot_loader_order_changed_file={path}" for path in changed_boot_files)

    if changed_boot_files and options.sync_device:
        _emit(progress_callback, "Flushing boot-loader changes...")
        sync_runner()

    finished_at = timestamp_provider()
    log_lines.extend(
        [
            f"finished_at={finished_at.isoformat()}",
            "post_write_complete",
        ]
    )

    if options.log_file_path:
        _emit(progress_callback, f"Writing post-write audit log to {options.log_file_path}...")
        _append_audit_log(options.log_file_path, log_lines)

    _emit(progress_callback, "Optional post-write customizations completed.")
