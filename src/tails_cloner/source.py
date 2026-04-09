from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class SourceType(Enum):
    RUNNING_TAILS = "running_tails"
    LOCAL_IMAGE = "local_image"
    REMOTE_IMAGE = "remote_image"


def is_running_tails() -> bool:
    """Check if we're running from a live Tails system."""
    # Check for the Tails live mount point
    tails_mount = "/lib/live/mount/medium"
    if os.path.exists(tails_mount) and os.path.isdir(tails_mount):
        # Additional check: verify it's actually Tails
        version_file = os.path.join(tails_mount, "live", "Tails.version")
        if os.path.exists(version_file):
            return True
    return False


def get_running_tails_device() -> str | None:
    """Get the underlying device of the running Tails system."""
    tails_mount = "/lib/live/mount/medium"
    if not os.path.exists(tails_mount):
        return None

    try:
        # Use findmnt to get the device of the mount point
        result = subprocess.run(
            ["findmnt", "-n", "-o", "SOURCE", "--target", tails_mount],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.SubprocessError):
        pass

    return None


def get_running_tails_version() -> str | None:
    """Get the version of the running Tails system."""
    version_file = "/lib/live/mount/medium/live/Tails.version"
    if os.path.exists(version_file):
        try:
            with open(version_file, "r") as f:
                return f.read().strip()
        except (OSError, IOError):
            pass
    return None


def get_running_tails_size_bytes() -> int:
    """Calculate the size of the running Tails system."""
    tails_mount = "/lib/live/mount/medium"
    if not os.path.exists(tails_mount):
        return 0

    total_size = 0
    try:
        for dirpath, dirnames, filenames in os.walk(tails_mount):
            for filename in filenames:
                filepath = os.path.join(dirpath, filename)
                if os.path.exists(filepath) and not os.path.islink(filepath):
                    try:
                        total_size += os.path.getsize(filepath)
                    except (OSError, IOError):
                        # Skip files we can't read
                        pass
    except (OSError, IOError):
        pass

    return total_size


@dataclass(frozen=True, slots=True)
class LocalImageSource:
    path: Path

    @property
    def exists(self) -> bool:
        return self.path.exists()

    @property
    def suffix(self) -> str:
        return self.path.suffix.lower()

    def validate(self) -> None:
        if not self.path.exists():
            raise FileNotFoundError(str(self.path))
        if not self.path.is_file():
            raise IsADirectoryError(str(self.path))


@dataclass(frozen=True, slots=True)
class RunningLiveSystemSource:
    """Represents the currently running Tails system as a source."""

    mount_point: Path = Path("/lib/live/mount/medium")

    @property
    def exists(self) -> bool:
        return is_running_tails()

    @property
    def version(self) -> str | None:
        return get_running_tails_version()

    @property
    def device(self) -> str | None:
        return get_running_tails_device()

    @property
    def size_bytes(self) -> int:
        return get_running_tails_size_bytes()

    def validate(self) -> None:
        if not self.exists:
            raise RuntimeError("Not running from Tails. Cannot clone from running system.")
        if not self.mount_point.exists():
            raise RuntimeError(f"Tails mount point {self.mount_point} does not exist.")

    def get_liveos_path(self) -> Path:
        """Get path to the live OS files."""
        return self.mount_point / "live"

    def get_iso_path(self) -> Path | None:
        """Get path to the ISO if present."""
        iso_path = self.mount_point / "live" / "Tails.iso"
        if iso_path.exists():
            return iso_path
        return None
