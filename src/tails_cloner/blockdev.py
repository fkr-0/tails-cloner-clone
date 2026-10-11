"""Shared block-device helpers used by discovery, install, and upgrade paths.

The "which mounts mean this disk is in use by the running system" policy lives
here exactly once so device discovery, whole-disk installs, and partition
upgrades can never drift apart on what they refuse to overwrite.
"""

from __future__ import annotations

import re
import subprocess
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from typing import Any

SYSTEM_MOUNTPOINTS = frozenset({"/", "/boot", "/boot/efi", "/home", "/usr", "/var"})
PROTECTED_LIVE_MOUNT_PREFIXES = ("/lib/live/mount", "/run/live")
STDERR_TAIL_LINES = 5
_DD_BYTES_RE = re.compile(r"(?P<bytes>\d+)\s+bytes(?:\s+\([^)]*\))?\s+(?:copied|transferred)", re.IGNORECASE)


def parse_dd_bytes(message: str) -> int | None:
    """Extract the byte count from a ``dd status=progress`` line, if it is one."""
    match = _DD_BYTES_RE.search(message)
    return int(match.group("bytes")) if match else None


def walk_nodes(nodes: Iterable[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    """Yield lsblk nodes depth-first (parents before children)."""
    for node in nodes:
        yield node
        children = node.get("children") or []
        if isinstance(children, list):
            yield from walk_nodes(child for child in children if isinstance(child, dict))


def mountpoints(value: object) -> list[str]:
    """Normalize lsblk ``mountpoints`` (list, string, or null-filled list) to strings."""
    if isinstance(value, list):
        return [mountpoint for mountpoint in value if isinstance(mountpoint, str)]
    if isinstance(value, str):
        return [value]
    return []


def is_live_medium_mountpoint(mountpoint: str) -> bool:
    """True for mounts that belong to a running live (Tails) medium."""
    return any(
        mountpoint == prefix or mountpoint.startswith(f"{prefix}/")
        for prefix in PROTECTED_LIVE_MOUNT_PREFIXES
    )


def is_protected_mountpoint(mountpoint: str) -> bool:
    """True when a mount means the disk backs the running operating system."""
    return mountpoint in SYSTEM_MOUNTPOINTS or is_live_medium_mountpoint(mountpoint)


def protected_mountpoints(candidates: Iterable[str]) -> list[str]:
    """Return the sorted, de-duplicated protected subset of ``candidates``."""
    return sorted({mountpoint for mountpoint in candidates if is_protected_mountpoint(mountpoint)})


def stream_process_output(
    process: subprocess.Popen[str],
    progress_callback: Callable[[str], None],
) -> int:
    """Forward a process's stderr lines (e.g. ``dd status=progress``) and return its status."""
    assert process.stderr is not None
    for line in process.stderr:
        message = line.strip()
        if message:
            progress_callback(message)
    return process.wait()


def run_streaming_command(command: list[str], progress_callback: Callable[[str], None]) -> int:
    """Run a command, streaming its stderr to ``progress_callback``; return the exit status."""
    process = subprocess.Popen(  # noqa: S603 - privileged write commands are the tool's core job
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    return stream_process_output(process, progress_callback)


def describe_command_failure(error: BaseException) -> str:
    """Render a subprocess/OS failure with the command's stderr tail, not just its exit code."""
    if isinstance(error, subprocess.CalledProcessError):
        command = " ".join(str(part) for part in error.cmd) if isinstance(error.cmd, list) else str(error.cmd)
        stderr = error.stderr if isinstance(error.stderr, str) else ""
        tail = " | ".join(deque((line.strip() for line in stderr.splitlines() if line.strip()), maxlen=STDERR_TAIL_LINES))
        detail = f": {tail}" if tail else ""
        return f"`{command}` failed with exit status {error.returncode}{detail}"
    if isinstance(error, FileNotFoundError):
        missing = error.filename or "a required program"
        return f"Required program not found: {missing}"
    return f"{type(error).__name__}: {error}"
