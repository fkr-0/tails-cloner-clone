from __future__ import annotations

from dataclasses import dataclass, field


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

    @property
    def pretty_name(self) -> str:
        vendor = self.vendor.strip() or "Unknown vendor"
        model = self.model.strip() or "Unknown model"
        return f"{self.path} · {self.size_label} · {vendor} {model}".strip()


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
