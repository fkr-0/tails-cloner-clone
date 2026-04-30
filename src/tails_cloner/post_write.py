from __future__ import annotations

import os
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from tails_cloner.models import PostWriteOptions

ProgressCallback = Callable[[str], None] | None
SyncRunner = Callable[[], None]
SleepRunner = Callable[[float], None]
TimestampProvider = Callable[[], datetime]



def _emit(progress_callback: ProgressCallback, message: str) -> None:
    if progress_callback is not None:
        progress_callback(message)



def _utc_now() -> datetime:
    return datetime.now(timezone.utc)



def _append_audit_log(log_file_path: str, lines: list[str]) -> None:
    path = Path(log_file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")



def apply_post_write_options(
    device_path: str,
    options: PostWriteOptions,
    progress_callback: ProgressCallback = None,
    sync_runner: SyncRunner = os.sync,
    sleep_runner: SleepRunner = time.sleep,
    timestamp_provider: TimestampProvider = _utc_now,
) -> None:
    """Apply optional post-write customizations after a successful clone.

    This hook is intentionally generic and opt-in only. It provides a stable
    extension point for safe post-write features without changing the default
    clone behavior.
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

    _emit(progress_callback, f"Running optional post-write customizations for {device_path}...")

    if options.sync_device:
        _emit(progress_callback, "Flushing system buffers...")
        sync_runner()

    if options.settle_seconds > 0:
        _emit(progress_callback, f"Waiting {options.settle_seconds:.1f}s for device to settle...")
        sleep_runner(options.settle_seconds)

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
