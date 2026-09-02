from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class SourceMode(Enum):
    """Source mode for cloning."""

    RUNNING = "running"  # Use the running Tails medium as an upgrade source
    ATTACHED = "attached"  # Use an attached/mounted Tails live source
    LOCAL = "local"  # Use local ISO/IMG file
    REMOTE = "remote"  # Download remote version


@dataclass(slots=True)
class VersionAssets:
    version: str
    directory_url: str
    iso_url: str
    img_url: str
    sig_url: str
    sha256_url: str


@dataclass(slots=True)
class BootLoaderOrderOptions:
    enabled: bool = False
    entries: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PostWriteOptions:
    enabled: bool = False
    sync_device: bool = True
    settle_seconds: float = 2.0
    log_file_path: str = ""
    boot_loader_order: BootLoaderOrderOptions = field(default_factory=BootLoaderOrderOptions)


@dataclass(slots=True)
class BlockDevice:
    path: str
    size_bytes: int
    size_label: str
    model: str
    vendor: str
    transport: str
    removable: bool
    serial: str = ""
    wwn: str = ""
    major_minor: str = ""
    stable_path: str = ""
    read_only: bool = False
    # Device properties for upgrade detection
    fstype: str = ""
    label: str = ""
    is_gpt: bool = False
    is_isohybrid: bool = False
    has_tails: bool = False
    is_big_enough_for_installation: bool = True
    is_big_enough_for_upgrade: bool = True
    is_running_system_device: bool = False
    is_current_system_device: bool = False
    is_host_system_device: bool = False
    is_attached_source_device: bool = False
    disabled_reason: str = ""

    @property
    def status_label(self) -> str:
        if self.is_running_system_device:
            return "Currently running Tails"
        if self.is_current_system_device:
            return "Running system"
        if self.is_attached_source_device:
            return "Attached Tails source"
        if self.is_host_system_device:
            return "Current OS storage"
        return ""

    @property
    def pretty_name(self) -> str:
        vendor = self.vendor.strip() or "Unknown vendor"
        model = self.model.strip() or "Unknown model"
        identity = self.serial.strip() or self.wwn.strip()
        identity_indicator = f" · ID {identity}" if identity else ""
        removable_indicator = " (removable)" if self.removable else ""
        read_only_indicator = " (read-only)" if self.read_only else ""
        tails_indicator = " [Tails installed]" if self.has_tails else ""
        status_indicator = f" [{self.status_label}]" if self.status_label else ""
        disabled_indicator = " [not selectable]" if self.disabled_reason else ""
        return (
            f"{self.path} · {self.size_label} · {vendor} {model}{identity_indicator}"
            f"{removable_indicator}{read_only_indicator}{tails_indicator}{status_indicator}{disabled_indicator}"
        ).strip()

    @property
    def identity_key(self) -> tuple[str, ...]:
        """Return the strongest available hardware identity for hot-swap checks."""
        wwn = self.wwn.strip().casefold()
        if wwn:
            return ("wwn", wwn)

        serial = self.serial.strip().casefold()
        if serial:
            return (
                "serial",
                serial,
                self.vendor.strip().casefold(),
                self.model.strip().casefold(),
            )

        stable_path = self.stable_path.strip()
        if stable_path:
            return ("by-id", stable_path)

        # Some inexpensive flash drives expose neither WWN nor serial. This
        # fallback cannot distinguish two truly identical devices, but still
        # detects the common case where a different drive reuses the same path.
        return (
            "fallback",
            str(self.size_bytes),
            self.vendor.strip().casefold(),
            self.model.strip().casefold(),
            self.transport.strip().casefold(),
        )

    @property
    def selectable(self) -> bool:
        return not self.disabled_reason


@dataclass(slots=True)
class AppState:
    available_versions: list[VersionAssets] = field(default_factory=list)
    devices: list[BlockDevice] = field(default_factory=list)
    post_write_options: PostWriteOptions = field(default_factory=PostWriteOptions)
    status_message: str = "Ready."
    selected_version: str = ""
    selected_iso_url: str = ""
    selected_image_url: str = ""
    selected_signature_url: str = ""
    selected_checksum_url: str = ""
    verified_image_path: str = ""
    verified_image_sha256: str = ""
    verified_image_version: str = ""
    verified_image_source_url: str = ""
    verified_image_signing_fingerprint: str = ""
    versions_loading: bool = False
    devices_loading: bool = False
    last_clone_progress: str = ""
    # Source mode: running Tails, local file, or remote download
    source_mode: SourceMode = SourceMode.LOCAL
    # Info about running Tails (if applicable)
    running_tails_version: str = ""
    running_tails_device: str = ""
    running_tails_available: bool = False
    # Info about an attached/mounted Tails live source separate from this launcher OS
    attached_live_source_device: str = ""
    attached_live_source_mount: str = ""
    attached_live_source_version: str = ""
