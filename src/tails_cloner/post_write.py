from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import mkdtemp

from tails_cloner.blockdev import describe_command_failure
from tails_cloner.boot_loader import apply_boot_loader_order_to_directory
from tails_cloner.models import PostWriteOptions
from tails_cloner.upgrader import build_privileged_command, find_tails_system_partition

ProgressCallback = Callable[[str], None] | None
SyncRunner = Callable[[], None]
SleepRunner = Callable[[float], None]
TimestampProvider = Callable[[], datetime]
CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(slots=True)
class MountState:
    mount_point: str = "none"
    unmount_succeeded: bool | None = None
    device_state: str = "unchanged"


class PostWriteFailure(RuntimeError):
    """Clone succeeded, but optional customizations did not finish safely."""

    def __init__(self, reason: str, state: MountState):
        super().__init__(f"Image write succeeded; post-write customization failed: {reason}; "
                         f"device state: {state.device_state}")
        self.write_succeeded = True
        self.customization_succeeded = False
        self.device_state = state.device_state
        self.mount_point = state.mount_point
        self.unmount_succeeded = state.unmount_succeeded


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
    partition = find_tails_system_partition(device_path)
    if partition.fstype not in {"vfat", "fat", "msdos"}:
        raise RuntimeError("Refusing to mount a non-VFAT Tails boot partition")
    if partition.mountpoints:
        raise RuntimeError("Refusing to modify an already mounted Tails boot partition: "
                           + ", ".join(partition.mountpoints))
    if partition.read_only:
        raise RuntimeError("Tails boot partition is read-only")
    return partition.path


def _run_checked(command: list[str], run: CommandRunner) -> subprocess.CompletedProcess[str]:
    result = run(command, check=True, text=True, capture_output=True)
    # Injected runners may not implement subprocess.run(check=True) semantics.
    if result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, command, result.stdout, result.stderr)
    return result


def _apply_boot_loader_order(
    device_path: str,
    options: PostWriteOptions,
    progress_callback: ProgressCallback,
    run: CommandRunner = subprocess.run,
    state: MountState | None = None,
) -> list[str]:
    if not options.boot_loader_order.enabled or not options.boot_loader_order.entries:
        return []

    boot_partition = _find_tails_boot_partition(device_path)
    changed_paths: list[str] = []
    _emit(progress_callback, f"Applying experimental boot-loader order on {boot_partition}...")

    # Not a TemporaryDirectory: if the unmount fails this directory is still the live
    # target filesystem, and recursive cleanup would delete the files on the device.
    mount_path = Path(mkdtemp(prefix="tails-cloner-boot-"))
    mount_path.chmod(0o700)
    if state is not None:
        state.mount_point = str(mount_path)
    mounted = False
    try:
        mount_options = f"rw,nosuid,nodev,noexec,uid={os.getuid()},gid={os.getgid()},umask=077"
        _run_checked(
            build_privileged_command(["mount", "-t", "vfat", "-o", mount_options,
                                      "--", boot_partition, str(mount_path)]),
            run,
        )
        mounted = True
        if state is not None:
            state.device_state = "mounted; customization may be partial"
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
        _release_mount(mount_path, mounted, progress_callback, run, state)


def _release_mount(
    mount_path: Path,
    mounted: bool,
    progress_callback: ProgressCallback,
    run: CommandRunner,
    state: MountState | None = None,
) -> None:
    """Unmount and remove the scratch mount point without masking an in-flight error."""
    error_in_flight = sys.exc_info()[0] is not None  # sampled before our own handler runs
    if mounted:
        try:
            _run_checked(build_privileged_command(["umount", "--", str(mount_path)]), run)
        except (OSError, subprocess.SubprocessError) as error:
            if state is not None:
                state.unmount_succeeded = False
                state.device_state = "unknown; mount may remain active"
            reason = describe_command_failure(error)
            _emit(
                progress_callback,
                f"Warning: boot partition mounted at {mount_path} could not be unmounted ({reason}). "
                "Unmount it manually before removing the device.",
            )
            if not error_in_flight:
                raise RuntimeError(f"Boot partition could not be unmounted: {reason}") from error
            return  # keep the directory: it is still the live filesystem
        if state is not None:
            state.unmount_succeeded = True
            state.device_state = "unmounted; customization may be partial"
    with suppress(OSError):
        mount_path.rmdir()


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
    state = MountState()
    log_lines = [
        "post_write_start",
        f"started_at={started_at.isoformat()}",
        f"device_path={device_path}",
        f"sync_device={options.sync_device}",
        f"settle_seconds={options.settle_seconds}",
    ]
    if options.boot_loader_order.enabled:
        log_lines.append("boot_loader_order_enabled=true")

    _emit(progress_callback, f"Running optional post-write customizations for {device_path}...")

    try:
        if options.sync_device:
            _emit(progress_callback, "Flushing system buffers...")
            sync_runner()

        if options.boot_loader_order.enabled and options.boot_loader_order.entries:
            try:
                _run_checked(["udevadm", "settle"], command_runner)
            except (OSError, subprocess.SubprocessError) as error:
                _emit(progress_callback, f"udevadm settle unavailable: {type(error).__name__}; proceeding cautiously")

        if options.settle_seconds > 0:
            _emit(progress_callback, f"Waiting {options.settle_seconds:.1f}s for device to settle...")
            sleep_runner(options.settle_seconds)

        changed_boot_files = _apply_boot_loader_order(
            device_path, options, progress_callback, command_runner, state
        )
        log_lines.extend(f"boot_loader_order_changed_file={path}" for path in changed_boot_files)
        if changed_boot_files and options.sync_device:
            _emit(progress_callback, "Flushing boot-loader changes...")
            sync_runner()
        if state.unmount_succeeded and options.sync_device:
            sync_runner()
    except BaseException as error:
        log_lines.extend([f"finished_at={timestamp_provider().isoformat()}",
                          f"mount_point={state.mount_point}",
                          f"unmount_succeeded={state.unmount_succeeded}",
                          f"error_type={type(error).__name__}", "post_write_failed"])
        if options.log_file_path:
            with suppress(OSError):
                _append_audit_log(options.log_file_path, log_lines)
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        raise PostWriteFailure(type(error).__name__, state) from error

    log_lines.extend([f"finished_at={timestamp_provider().isoformat()}",
                      f"mount_point={state.mount_point}",
                      f"unmount_succeeded={state.unmount_succeeded}",
                      "post_write_complete"])
    if options.log_file_path:
        _append_audit_log(options.log_file_path, log_lines)

    _emit(progress_callback, "Optional post-write customizations completed.")
