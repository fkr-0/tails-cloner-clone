from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from tails_cloner.devices import MIN_INSTALLATION_SIZE_GB
from tails_cloner.models import BlockDevice


class OperationKind(Enum):
    INSTALL = "install"
    UPGRADE = "upgrade"


@dataclass(frozen=True, slots=True)
class OperationSource:
    type: str
    path: str = ""
    device: str = ""
    version: str = ""
    size_bytes: int = 0
    verified: bool = False
    sha256: str = ""
    origin_url: str = ""
    signing_fingerprint: str = ""


@dataclass(frozen=True, slots=True)
class OperationPlan:
    operation: OperationKind
    source: OperationSource
    target: BlockDevice
    warnings: list[str] = field(default_factory=list)
    blocking_errors: list[str] = field(default_factory=list)
    requires_confirmation: bool = True
    dry_run_only: bool = True

    @property
    def would_write(self) -> bool:
        return not self.blocking_errors

    @property
    def action_label(self) -> str:
        if self.blocking_errors:
            return "Not selectable"
        if self.operation == OperationKind.UPGRADE:
            return "Upgrade"
        if self.target.has_tails:
            return "Reinstall (delete all data)"
        return "Install"

    @property
    def status_message(self) -> str:
        if self.blocking_errors:
            return "Device is visible for context but cannot be selected as a target."
        if self.operation == OperationKind.UPGRADE:
            return "Existing Tails installation detected. Upgrade preserves existing Persistent Storage if present."
        if self.target.has_tails:
            return "Device has Tails installed. Install/Reinstall will delete Persistent Storage."
        return "Device is eligible for installation."


    @property
    def confirmation_title(self) -> str:
        if self.operation == OperationKind.UPGRADE:
            return "Confirm upgrade"
        if self.target.has_tails:
            return "Confirm reinstallation"
        return "Confirm installation"

    @property
    def source_label(self) -> str:
        if self.source.type == "running_source":
            version = f" {self.source.version}" if self.source.version else ""
            return f"running Tails{version}".strip()
        if self.source.type == "attached_source":
            version = self.source.version or "unknown"
            device = self.source.device or "not selected"
            return f"attached Tails live source {version} from {device}"
        if self.source.type == "remote_image":
            version = f" {self.source.version}" if self.source.version else ""
            return f"downloaded Tails image{version}".strip()
        if self.source.type == "image" and self.source.verified:
            filename = self.source.path.rsplit("/", 1)[-1] if self.source.path else "cached image"
            version = f" {self.source.version}" if self.source.version else ""
            return f"verified downloaded Tails{version} ({filename})"
        return self.source.path.rsplit("/", 1)[-1] if self.source.path else "selected image"

    @property
    def target_label(self) -> str:
        details = [self.target.path]
        if self.target.size_label:
            details.append(self.target.size_label)
        vendor_model = " ".join(part for part in [self.target.vendor, self.target.model] if part).strip()
        if vendor_model:
            details.append(vendor_model)
        if self.target.wwn:
            details.append(f"WWN {self.target.wwn}")
        elif self.target.serial:
            details.append(f"serial {self.target.serial}")
        elif self.target.stable_path:
            details.append(f"by-id {self.target.stable_path}")
        else:
            details.append("no stable hardware ID reported")
        if not self.target.removable:
            details.append("internal/non-removable")
        return " · ".join(details)

    @property
    def data_impact_summary(self) -> str:
        if self.operation == OperationKind.UPGRADE:
            return "This will upgrade the existing Tails installation while preserving existing Persistent Storage if present."
        if self.target.has_tails:
            return "All data on the selected device will be lost, including any Persistent Storage."
        return "All data on the selected device will be lost."

    @property
    def confirmation_message(self) -> str:
        if self.operation == OperationKind.UPGRADE:
            action = "Upgrade while preserving existing Persistent Storage if present"
            lead = f"Upgrade {self.target.path} from {self.source_label}?"
        elif self.target.has_tails:
            action = "Reinstall and delete all data"
            lead = f"Reinstall Tails on {self.target.path} from {self.source_label}?"
        else:
            action = "Install and delete all data on selected device"
            lead = f"Install Tails to {self.target.path} from {self.source_label}?"
        parts = [
            lead,
            "",
            f"Source: {self.source_label}",
            f"Target: {self.target_label}",
            f"Action: {action}",
        ]
        if self.warnings:
            parts.extend(["", "Warnings:", *[f"- {warning}" for warning in self.warnings]])
        parts.extend(["", self.data_impact_summary])
        return "\n".join(parts)

    @property
    def status_foreground(self) -> str:
        if self.blocking_errors or self.target.has_tails:
            return "#a63636"
        if self.operation == OperationKind.UPGRADE:
            return "#2e7d32"
        return ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation.value,
            "source": asdict(self.source),
            "target": _device_to_dict(self.target),
            "blocking_errors": list(self.blocking_errors),
            "warnings": list(self.warnings),
            "requires_confirmation": self.requires_confirmation,
            "would_write": self.would_write,
            "dry_run_only": self.dry_run_only,
            "action_label": self.action_label,
            "status_message": self.status_message,
            "source_label": self.source_label,
            "target_label": self.target_label,
            "data_impact_summary": self.data_impact_summary,
            "confirmation_title": self.confirmation_title,
            "confirmation_message": self.confirmation_message,
        }


def _device_to_dict(device: BlockDevice) -> dict[str, Any]:
    return {
        "path": device.path,
        "size_bytes": device.size_bytes,
        "size_label": device.size_label,
        "model": device.model,
        "vendor": device.vendor,
        "transport": device.transport,
        "removable": device.removable,
        "serial": device.serial,
        "wwn": device.wwn,
        "major_minor": device.major_minor,
        "stable_path": device.stable_path,
        "read_only": device.read_only,
        "fstype": device.fstype,
        "label": device.label,
        "is_gpt": device.is_gpt,
        "is_isohybrid": device.is_isohybrid,
        "has_tails": device.has_tails,
        "is_big_enough_for_installation": device.is_big_enough_for_installation,
        "is_big_enough_for_upgrade": device.is_big_enough_for_upgrade,
        "is_running_system_device": device.is_running_system_device,
        "is_current_system_device": device.is_current_system_device,
        "is_host_system_device": device.is_host_system_device,
        "is_attached_source_device": device.is_attached_source_device,
        "selectable": device.selectable,
        "disabled_reason": device.disabled_reason,
        "status_label": device.status_label,
        "pretty_name": device.pretty_name,
    }


def plan_operation(operation: OperationKind, source: OperationSource, target: BlockDevice) -> OperationPlan:
    warnings: list[str] = []
    errors: list[str] = []

    if source.type == "remote_image":
        errors.append("Download the selected remote IMG before starting a write operation.")
    elif source.type == "image" and not source.path:
        errors.append("Choose a local ISO or IMG file before starting a write operation.")
    elif source.type == "image" and not source.verified:
        warnings.append(
            "local image has not been cryptographically verified by this application; "
            "verify its official Tails signature before writing"
        )
    elif source.type == "running_source":
        if not source.device:
            errors.append("The running Tails source device could not be determined.")
        if operation != OperationKind.UPGRADE:
            errors.append(
                "The running Tails medium is supported only as an upgrade source. "
                "Choose a verified Tails IMG for install or reinstall."
            )
    elif source.type == "attached_source":
        if not source.device:
            errors.append("Validate an attached Tails live source before starting an upgrade.")
        if operation != OperationKind.UPGRADE:
            errors.append("Attached live sources are only supported for persistence-preserving upgrades.")

    if (
        operation == OperationKind.INSTALL
        and source.size_bytes > 0
        and target.size_bytes > 0
        and source.size_bytes > target.size_bytes
    ):
        errors.append(
            "The selected image is larger than the target device: "
            f"{source.size_bytes} > {target.size_bytes} bytes."
        )

    if target.disabled_reason:
        errors.append(target.disabled_reason)
    if target.read_only:
        errors.append("This device is read-only and cannot be written to.")

    if operation == OperationKind.UPGRADE:
        if not target.has_tails:
            errors.append("Upgrade requires an existing Tails installation on the selected target.")
        if target.has_tails and not target.is_big_enough_for_upgrade:
            errors.append(
                "This Tails device is too small for a safe upgrade from this version. "
                "Use Install/Reinstall only if you accept deleting all data."
            )
    else:
        if not target.is_big_enough_for_installation:
            errors.append(
                f"This device is too small to install Tails (at least {MIN_INSTALLATION_SIZE_GB} GB is required)."
            )
        if target.has_tails:
            warnings.append(
                "target already contains Tails; install would reinstall and may remove Persistent Storage"
            )

    if not target.removable:
        warnings.append("target is not reported as removable; verify that this is intentional")

    if not (target.wwn or target.serial or target.stable_path):
        warnings.append(
            "target exposes no stable hardware identifier; hot-swap revalidation is limited to device characteristics"
        )

    return OperationPlan(
        operation=operation,
        source=source,
        target=target,
        warnings=warnings,
        blocking_errors=errors,
    )
