from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class SourceMode(Enum):
    """Source mode for cloning."""
    RUNNING = "running"  # Clone from running Tails system
    LOCAL = "local"      # Use local ISO/IMG file
    REMOTE = "remote"    # Download remote version


@dataclass(slots=True)
class VersionAssets:
    version: str
    directory_url: str
    iso_url: str
    img_url: str
    sig_url: str
    sha256_url: str


@dataclass(slots=True)
class BlockDevice:
    path: str
    size_bytes: int
    size_label: str
    model: str
    vendor: str
    transport: str
    removable: bool
    read_only: bool = False
    # Device properties for upgrade detection
    fstype: str = ""
    label: str = ""
    is_gpt: bool = False
    is_isohybrid: bool = False
    has_tails: bool = False
    is_big_enough_for_installation: bool = True
    is_big_enough_for_upgrade: bool = True

    @property
    def pretty_name(self) -> str:
        vendor = self.vendor.strip() or "Unknown vendor"
        model = self.model.strip() or "Unknown model"
        removable_indicator = " (removable)" if self.removable else ""
        read_only_indicator = " (read-only)" if self.read_only else ""
        tails_indicator = " [Tails installed]" if self.has_tails else ""
        return f"{self.path} · {self.size_label} · {vendor} {model}{removable_indicator}{read_only_indicator}{tails_indicator}".strip()


@dataclass(slots=True)
class AppState:
    available_versions: list[VersionAssets] = field(default_factory=list)
    devices: list[BlockDevice] = field(default_factory=list)
    status_message: str = "Ready."
    selected_version: str = ""
    selected_iso_url: str = ""
    selected_image_url: str = ""
    selected_signature_url: str = ""
    selected_checksum_url: str = ""
    versions_loading: bool = False
    devices_loading: bool = False
    last_clone_progress: str = ""
    # Source mode: running Tails, local file, or remote download
    source_mode: SourceMode = SourceMode.LOCAL
    # Info about running Tails (if applicable)
    running_tails_version: str = ""
    running_tails_device: str = ""
    running_tails_available: bool = False
