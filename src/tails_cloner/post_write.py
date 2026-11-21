from __future__ import annotations

import os
import stat
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
from tails_cloner.device_identity import DiskIdentity, verify_partition
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
    """Image write succeeded, but subsequent device or customization steps failed."""

    def __init__(self, reason: str, state: MountState):
        super().__init__(f"Image write succeeded; post-write step failed: {reason}; "
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


def _mount_source_at(mount_path: Path) -> str | None:
    """Inspect exact mount points, not the containing parent filesystem."""
    with Path('/proc/self/mountinfo').open(encoding='utf-8') as mounts:
        for line in mounts:
            before, sep, after = line.partition(' - ')
            if not sep:
                continue
            fields, detail = before.split(), after.split()
            if len(fields) > 4 and len(detail) > 1:
                decoded = (fields[4].replace('\\040', ' ').replace('\\011', '\t')
                           .replace('\\012', '\n').replace('\\134', '\\'))
                if decoded == str(mount_path):
                    return detail[1]
    return None


def _same_mount_source(observed: str, expected: str) -> bool:
    return observed == expected or os.path.realpath(observed) == os.path.realpath(expected)


def _mount_major_minor_at(mount_path: Path) -> str | None:
    """Kernel mount identity, independent of a potentially reused /dev path."""
    with Path('/proc/self/mountinfo').open(encoding='utf-8') as mounts:
        for line in mounts:
            fields = line.split(' - ', 1)[0].split()
            if len(fields) > 4:
                decoded = (fields[4].replace('\\\\040', ' ').replace('\\\\011', '\t')
                           .replace('\\\\012', '\n').replace('\\\\134', '\\'))
                if decoded == str(mount_path):
                    return fields[2]
    return None


def _partition_major_minor(path: str) -> str:
    info = os.stat(path)
    if not stat.S_ISBLK(info.st_mode):
        raise RuntimeError('Mount source is no longer a block device')
    return f'{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}'


def _verify_mounted_partition(mount_path: Path, partition: str,
                              expected_disk: DiskIdentity, run: CommandRunner) -> None:
    # The pre-mount lsblk result is not authority for the mounted filesystem.
    # Check the kernel's mounted dev_t against the current partition node and
    # independently revalidate ancestry after mount(2), before any file edits.
    observed = _mount_source_at(mount_path)
    mounted_number = _mount_major_minor_at(mount_path)
    partition_number = _partition_major_minor(partition)
    if (observed is None or not _same_mount_source(observed, partition)
            or mounted_number != partition_number):
        raise RuntimeError('Mounted filesystem identity differs from verified partition')
    verify_partition(expected_disk, partition, run)
    if (_mount_source_at(mount_path) != observed
            or _mount_major_minor_at(mount_path) != mounted_number
            or _partition_major_minor(partition) != partition_number):
        raise RuntimeError('Mounted filesystem changed during identity verification')


def _apply_boot_loader_order(
    device_path: str,
    options: PostWriteOptions,
    progress_callback: ProgressCallback,
    run: CommandRunner = subprocess.run,
    state: MountState | None = None,
    expected_disk: DiskIdentity | None = None,
) -> list[str]:
    if not options.boot_loader_order.enabled or not options.boot_loader_order.entries:
        return []

    boot_partition = _find_tails_boot_partition(device_path)
    if expected_disk is not None:
        verify_partition(expected_disk, boot_partition, run)
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
        if expected_disk is not None:
            verify_partition(expected_disk, boot_partition, run)
        if _mount_source_at(mount_path) is not None:
            raise RuntimeError('Refusing an existing or automounted scratch mount')
        mount_options = f"rw,nosuid,nodev,noexec,uid={os.getuid()},gid={os.getgid()},umask=077"
        try:
            _run_checked(
                build_privileged_command(["mount", "-t", "vfat", "-o", mount_options,
                                          "--", boot_partition, str(mount_path)]), run)
        except (OSError, subprocess.SubprocessError):
            # A subprocess wrapper may raise after mount(2) has succeeded.
            try:
                observed = _mount_source_at(mount_path)
            except OSError:
                observed = 'unverified'
            mounted = bool(observed and _same_mount_source(observed, boot_partition))
            if mounted and expected_disk is not None:
                try:
                    mounted = (_mount_major_minor_at(mount_path)
                               == _partition_major_minor(boot_partition))
                except OSError:
                    mounted = False
            if state is not None and observed:
                state.device_state = ('mounted; customization may be partial' if mounted else
                                      'unknown; foreign or unverified scratch mount')
            raise
        mounted = expected_disk is None
        if expected_disk is not None:
            # A successful wrapper exit does not prove mount ownership. Never
            # unmount a foreign filesystem discovered at the scratch path.
            if state is not None:
                state.device_state = 'unknown; mount ownership not yet verified'
            observed = _mount_source_at(mount_path)
            mounted = bool(observed and _same_mount_source(observed, boot_partition)
                           and _mount_major_minor_at(mount_path)
                           == _partition_major_minor(boot_partition))
            if not mounted:
                if state is not None:
                    state.device_state = 'unknown; foreign or unverified scratch mount'
                raise RuntimeError('Refusing to modify or unmount unverified scratch filesystem')
        if state is not None:
            state.device_state = "mounted; customization may be partial"
        if expected_disk is not None:
            _verify_mounted_partition(mount_path, boot_partition, expected_disk, run)
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
            # Preserve the original failure and the manual recovery path.
            with suppress(BaseException):
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
    expected_disk: DiskIdentity | None = None,
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

    try:
        _emit(progress_callback, f"Running optional post-write customizations for {device_path}...")
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

        if expected_disk is not None:
            from tails_cloner.device_identity import snapshot_disk
            if snapshot_disk(expected_disk.path, command_runner) != expected_disk:
                raise RuntimeError("Target disk identity changed before post-write")
        changed_boot_files = _apply_boot_loader_order(
            device_path, options, progress_callback, command_runner, state,
            expected_disk=expected_disk,
        )
        log_lines.extend(f"boot_loader_order_changed_file={path}" for path in changed_boot_files)
        if changed_boot_files and options.sync_device:
            _emit(progress_callback, "Flushing boot-loader changes...")
            sync_runner()
        if state.unmount_succeeded and options.sync_device:
            sync_runner()
        log_lines.extend([f"finished_at={timestamp_provider().isoformat()}",
                          f"mount_point={state.mount_point}",
                          f"unmount_succeeded={state.unmount_succeeded}",
                          "post_write_complete"])
        if options.log_file_path:
            _append_audit_log(options.log_file_path, log_lines)
        _emit(progress_callback, "Optional post-write customizations completed.")
    except BaseException as error:
        # After dd, interruption and audit persistence errors cannot prove durability.
        if isinstance(error, (KeyboardInterrupt, SystemExit, OSError)):
            state.device_state = "unknown; post-write completion unverified"
        log_lines.extend([f"finished_at={timestamp_provider().isoformat()}",
                          f"mount_point={state.mount_point}",
                          f"unmount_succeeded={state.unmount_succeeded}",
                          f"error_type={type(error).__name__}", "post_write_failed"])
        if options.log_file_path:
            with suppress(OSError):
                _append_audit_log(options.log_file_path, log_lines)
        raise PostWriteFailure(type(error).__name__, state) from error
