"""Automation/operator CLI for Tails Cloner Clone.

No destructive implementation lives here: installs and upgrades are routed
through :class:`ApplicationController`, which performs the same planner checks,
stable-identity revalidation, and creator/upgrader calls as the GUI.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import asdict
from pathlib import Path
from typing import cast

from tails_cloner import __version__
from tails_cloner.cli_events import (
    CliError,
    CommandResult,
    EventSink,
    ExitCode,
    OutputMode,
)
from tails_cloner.config import DEFAULT_REMOTE_INDEX_URL
from tails_cloner.controller import ApplicationController, SafetyRefusalError
from tails_cloner.download import (
    DownloadNetworkError,
    DownloadProgress,
    DownloadVerificationError,
    RemoteImageRequest,
    VerifiedImageDownloader,
    VerifiedImageResult,
    bundled_asset_path,
    default_destination_for_url,
)
from tails_cloner.drive_inspector import inspect_drive_tails_facts
from tails_cloner.models import (
    BlockDevice,
    BootLoaderOrderOptions,
    PostWriteOptions,
    SourceMode,
    VersionAssets,
)
from tails_cloner.planner import OperationKind, target_confirmation_token
from tails_cloner.runtime import build_application_controller
from tails_cloner.source import (
    AttachedLiveSystemSource,
    RunningLiveSystemSource,
    get_parent_disk_path,
)
from tails_cloner.verification import verify_openpgp_detached_signature, verify_sha256

_DD_BYTES_RE = re.compile(r"(?P<bytes>\d+)\s+bytes(?:\s+\([^)]*\))?\s+(?:copied|transferred)", re.IGNORECASE)

Handler = Callable[[argparse.Namespace, EventSink], CommandResult]


def _build_controller(remote_index_url: str, post_write_options: PostWriteOptions | None = None) -> ApplicationController:
    return build_application_controller(
        remote_index_url=remote_index_url,
        post_write_options=post_write_options,
    )


def _version_to_dict(version: VersionAssets, *, latest: bool, default: bool) -> dict[str, object]:
    payload: dict[str, object] = asdict(version)
    payload["latest"] = latest
    payload["default"] = default
    return payload


def _human_fields(payload: dict[str, object]) -> str:
    return "\n".join(f"{key}: {value}" for key, value in payload.items())


def _catalog(controller: ApplicationController, sink: EventSink) -> list[VersionAssets]:
    try:
        versions = controller.discover_versions()
    except Exception as error:  # noqa: BLE001 - normalized for operator exit taxonomy
        raise CliError(
            f"Could not retrieve the Tails version catalog: {error}",
            exit_code=ExitCode.DOWNLOAD_FAILED,
            category="catalog_network_failure",
        ) from error
    sink.discovery(resource="version_catalog", count=len(versions))
    if not versions:
        raise CliError(
            "The remote Tails version catalog is empty.",
            exit_code=ExitCode.INVALID_INPUT,
            category="empty_catalog",
        )
    return versions


def _select_version(
    controller: ApplicationController,
    requested: str,
    sink: EventSink,
) -> VersionAssets:
    versions = _catalog(controller, sink)
    selected = versions[0] if requested in {"latest", "default", ""} else next(
        (entry for entry in versions if entry.version == requested),
        None,
    )
    if selected is None:
        raise CliError(
            f"Tails version not found: {requested}",
            exit_code=ExitCode.INVALID_INPUT,
            category="version_not_found",
            details={"requested_version": requested},
        )
    controller.apply_version_selection(selected)
    sink.resolution(resource="version", requested=requested, resolved=selected.version)
    return selected


def _device_to_dict(device: BlockDevice, *, inspect_facts: bool = True) -> dict[str, object]:
    payload: dict[str, object] = {
        "path": device.path,
        "stable_path": device.stable_path,
        "identity_key": list(device.identity_key),
        "confirmation_token": target_confirmation_token(device),
        "size_bytes": device.size_bytes,
        "size_label": device.size_label,
        "model": device.model,
        "vendor": device.vendor,
        "transport": device.transport,
        "removable": device.removable,
        "serial": device.serial,
        "wwn": device.wwn,
        "major_minor": device.major_minor,
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
    if not inspect_facts:
        return payload
    try:
        facts = inspect_drive_tails_facts(device.path)
    except Exception as error:  # noqa: BLE001 - inspection remains best effort/read-only
        payload["inspection_error"] = str(error)
        return payload
    payload.update(
        {
            "installed_tails_version": facts.tails_version or "",
            "running_tails_on_this_drive": facts.running_tails_on_this_drive,
            "persistence_present": facts.persistence_configured,
            "persistence_size_bytes": facts.persistence_partition_size_bytes,
            "version_detection_error": facts.version_detection_error or "",
            "version_detection_requires_privilege": facts.version_detection_requires_privilege,
        }
    )
    return payload


def _scan_devices(controller: ApplicationController, sink: EventSink) -> list[BlockDevice]:
    try:
        devices = controller.discover_devices()
    except Exception as error:  # noqa: BLE001 - normalized for operator output
        raise CliError(
            f"Device discovery failed: {error}",
            exit_code=ExitCode.INVALID_INPUT,
            category="device_discovery_failed",
        ) from error
    sink.discovery(resource="block_devices", count=len(devices))
    return devices


def _find_device(controller: ApplicationController, path: str) -> BlockDevice:
    try:
        return controller.find_target_device(path)
    except RuntimeError as error:
        raise CliError(
            str(error),
            exit_code=ExitCode.INVALID_INPUT,
            category="device_not_found",
            details={"device": path},
        ) from error


def handle_versions(args: argparse.Namespace, sink: EventSink) -> CommandResult:
    controller = _build_controller(args.remote_index_url)
    try:
        versions = _catalog(controller, sink)
        latest = versions[0].version
        if args.versions_command == "list":
            payload: dict[str, object] = {
                "latest": latest,
                "default": latest,
                "versions": [
                    _version_to_dict(entry, latest=entry.version == latest, default=entry.version == latest)
                    for entry in versions
                ],
            }
            text = "\n".join(
                f"{entry.version}{' (latest, default)' if entry.version == latest else ''}"
                for entry in versions
            )
            return CommandResult(payload=payload, human_text=text)

        requested = "latest" if args.versions_command == "latest" else args.version
        selected = versions[0] if requested in {"latest", "default", ""} else next(
            (entry for entry in versions if entry.version == requested),
            None,
        )
        if selected is None:
            raise CliError(
                f"Tails version not found: {requested}",
                exit_code=ExitCode.INVALID_INPUT,
                category="version_not_found",
                details={"requested_version": requested},
            )
        controller.apply_version_selection(selected)
        sink.resolution(resource="version", requested=requested, resolved=selected.version)
        payload = _version_to_dict(
            selected,
            latest=selected.version == latest,
            default=selected.version == latest,
        )
        return CommandResult(payload={"version": payload}, human_text=_human_fields(payload))
    finally:
        controller.shutdown()


def _format_device_table(devices: list[dict[str, object]]) -> str:
    rows = [("PATH", "STABLE", "SIZE", "TAILS", "PERSIST", "SELECTABLE", "STATUS")]
    for device in devices:
        rows.append(
            (
                str(device["path"]),
                str(device["stable_path"] or "-"),
                str(device["size_label"]),
                str(device.get("installed_tails_version") or ("yes" if device["has_tails"] else "no")),
                "yes" if device.get("persistence_present") else "no",
                "yes" if device["selectable"] else "no",
                str(device["status_label"] or device["disabled_reason"] or "ok"),
            )
        )
    widths = [max(len(row[index]) for row in rows) for index in range(len(rows[0]))]
    return "\n".join(
        "  ".join(value.ljust(widths[index]) for index, value in enumerate(row))
        for row in rows
    )


def handle_devices(args: argparse.Namespace, sink: EventSink) -> CommandResult:
    controller = _build_controller(args.remote_index_url)
    try:
        controller.detect_running_tails()
        devices = _scan_devices(controller, sink)
        if args.devices_command == "list":
            payloads = [_device_to_dict(device) for device in devices]
            return CommandResult(
                payload={"devices": payloads},
                human_text=_format_device_table(payloads),
            )
        device = _find_device(controller, args.device)
        payload = _device_to_dict(device)
        return CommandResult(payload={"device": payload}, human_text=_human_fields(payload))
    finally:
        controller.shutdown()


def _attached_source_from_args(args: argparse.Namespace) -> AttachedLiveSystemSource | None:
    device = str(getattr(args, "attached_source", "") or "")
    mount = str(getattr(args, "attached_mount", "") or "")
    if not device and not mount:
        return None
    if not device or not mount:
        raise CliError(
            "Attached source requires both --attached-source DEVICE and --attached-mount PATH.",
            exit_code=ExitCode.INVALID_INPUT,
            category="invalid_attached_source",
        )
    source = AttachedLiveSystemSource(device_path=device, mount_point=Path(mount))
    try:
        source.validate()
    except (OSError, RuntimeError, ValueError) as error:
        raise CliError(
            f"Attached Tails source validation failed: {error}",
            exit_code=ExitCode.INVALID_INPUT,
            category="invalid_attached_source",
        ) from error
    return source


def _running_source_payload() -> dict[str, object]:
    source = RunningLiveSystemSource()
    device = source.device or ""
    return {
        "available": source.exists,
        "version": source.version or "",
        "device": device,
        "parent_device": get_parent_disk_path(device),
        "mount_point": str(source.mount_point),
        "iso_path": str(source.get_iso_path() or ""),
    }


def _attached_source_payload(source: AttachedLiveSystemSource) -> dict[str, object]:
    return {
        "valid": True,
        "device": source.device_path,
        "parent_device": source.parent_device,
        "mount_point": str(source.mount_point),
        "version": source.version or "",
        "live_path": str(source.get_liveos_path()),
        "iso_path": str(source.get_iso_path() or ""),
    }


def handle_status(args: argparse.Namespace, sink: EventSink) -> CommandResult:
    controller = _build_controller(args.remote_index_url)
    try:
        controller.detect_running_tails()
        attached = _attached_source_from_args(args)
        if attached is not None:
            controller.set_attached_live_source(attached.device_path, attached.mount_point)
        devices = _scan_devices(controller, sink)
        current_backing = [
            _device_to_dict(device, inspect_facts=False)
            for device in devices
            if device.is_current_system_device
        ]
        payload: dict[str, object] = {
            "application": {"name": "tails-cloner-clone", "version": __version__},
            "running_tails": _running_source_payload(),
            "current_system_backing_devices": current_backing,
            "device_count": len(devices),
        }
        if attached is not None:
            payload["attached_live_source"] = _attached_source_payload(attached)
        human = [f"application: tails-cloner-clone {__version__}"]
        running = cast(dict[str, object], payload["running_tails"])
        human.append(
            f"running Tails: {running['version'] if running['available'] else 'not detected'}"
        )
        human.append(
            "current system backing: "
            + (", ".join(str(item["path"]) for item in current_backing) or "not resolved")
        )
        human.append(f"devices: {len(devices)}")
        return CommandResult(payload=payload, human_text="\n".join(human))
    finally:
        controller.shutdown()


def handle_source(args: argparse.Namespace, sink: EventSink) -> CommandResult:
    if args.source_command == "running":
        payload = _running_source_payload()
        code = ExitCode.SUCCESS if payload["available"] else ExitCode.INVALID_INPUT
        return CommandResult(payload={"running_tails": payload}, human_text=_human_fields(payload), exit_code=code)

    source = AttachedLiveSystemSource(
        device_path=args.device,
        mount_point=Path(args.mount_point),
    )
    try:
        source.validate()
    except (OSError, RuntimeError, ValueError) as error:
        payload = {
            "valid": False,
            "error": str(error),
            "device": source.device_path,
            "parent_device": source.parent_device,
            "mount_point": str(source.mount_point),
            "version": source.version or "",
        }
        return CommandResult(payload=payload, human_text=_human_fields(payload), exit_code=ExitCode.INVALID_INPUT)
    payload = _attached_source_payload(source)
    sink.resolution(resource="attached_live_source", device=source.device_path, version=source.version or "")
    return CommandResult(payload=payload, human_text=_human_fields(payload))


def _download_progress_to_events(sink: EventSink, progress: DownloadProgress) -> None:
    fields: dict[str, object] = {"phase": progress.phase}
    if progress.bytes_received is not None:
        fields["bytes_received"] = progress.bytes_received
    if progress.bytes_total is not None:
        fields["bytes_total"] = progress.bytes_total
    if progress.phase == "verification":
        sink.verification(progress.message, **fields)
    else:
        sink.progress(progress.message, **fields)


def _download_selected_version(
    controller: ApplicationController,
    version: VersionAssets,
    sink: EventSink,
    *,
    destination: Path | None = None,
) -> VerifiedImageResult:
    destination = destination or default_destination_for_url(version.img_url, version=version.version)
    sink.resolution(
        resource="download_destination",
        version=version.version,
        destination=str(destination.expanduser()),
    )
    try:
        result = VerifiedImageDownloader().download(
            RemoteImageRequest(
                version=version.version,
                image_url=version.img_url,
                checksum_url=version.sha256_url,
                signature_url=version.sig_url,
                destination=destination,
            ),
            progress_callback=lambda progress: _download_progress_to_events(sink, progress),
        )
    except DownloadVerificationError as error:
        raise CliError(
            f"Tails image verification failed: {error}",
            exit_code=ExitCode.VERIFICATION_FAILED,
            category="verification_failed",
        ) from error
    except DownloadNetworkError as error:
        raise CliError(
            f"Tails image download failed: {error}",
            exit_code=ExitCode.DOWNLOAD_FAILED,
            category="download_failed",
        ) from error
    controller.record_verified_image(
        path=str(result.path),
        sha256=result.sha256,
        version=result.version,
        source_url=result.source_url,
        signing_fingerprint=result.signing_fingerprint,
    )
    controller.set_source_mode(SourceMode.LOCAL)
    sink.verification(
        "SHA-256 and pinned OpenPGP verification succeeded",
        sha256=result.sha256,
        signing_fingerprint=result.signing_fingerprint,
        reused_existing=result.reused_existing,
    )
    return result


def handle_download(args: argparse.Namespace, sink: EventSink) -> CommandResult:
    controller = _build_controller(args.remote_index_url)
    try:
        version = _select_version(controller, args.version, sink)
        destination: Path | None = None
        if args.destination:
            destination = Path(args.destination)
        elif args.directory:
            filename = Path(version.img_url).name
            destination = Path(args.directory) / filename
        result = _download_selected_version(controller, version, sink, destination=destination)
        payload: dict[str, object] = {"download": result.to_dict()}
        status = "reused verified file" if result.reused_existing else "downloaded and verified"
        return CommandResult(
            payload=payload,
            human_text=f"{status}: {result.path}\nsha256: {result.sha256}\nsigning fingerprint: {result.signing_fingerprint}",
        )
    finally:
        controller.shutdown()


def _verify_local_source(
    controller: ApplicationController,
    args: argparse.Namespace,
    sink: EventSink,
    image: Path,
    *,
    for_execution: bool,
) -> None:
    checksum = str(getattr(args, "sha256", "") or "").strip()
    signature = str(getattr(args, "signature", "") or "").strip()
    allow_unverified = bool(getattr(args, "allow_unverified_local_image", False))
    if bool(checksum) != bool(signature):
        raise CliError(
            "Local cryptographic verification requires both --sha256 DIGEST and --signature FILE.",
            exit_code=ExitCode.INVALID_INPUT,
            category="incomplete_verification_input",
        )
    if not checksum:
        if for_execution and not allow_unverified:
            raise CliError(
                "Refusing a noninteractive write from an unverified local image. Supply --sha256 and --signature, or explicitly pass --allow-unverified-local-image.",
                exit_code=ExitCode.VERIFICATION_FAILED,
                category="unverified_local_image",
            )
        sink.warning("Local image has not been cryptographically verified by this application")
        return
    try:
        digest = verify_sha256(image, checksum)
        fingerprint = verify_openpgp_detached_signature(
            image,
            Path(signature),
            bundled_asset_path("tails-signing-minimal.key"),
        )
    except (OSError, RuntimeError, ValueError) as error:
        raise CliError(
            f"Local image verification failed: {error}",
            exit_code=ExitCode.VERIFICATION_FAILED,
            category="verification_failed",
        ) from error
    controller.record_verified_image(
        path=str(image),
        sha256=digest,
        version=str(getattr(args, "source_version", "") or ""),
        source_url="",
        signing_fingerprint=fingerprint,
    )
    sink.verification(
        "Local image SHA-256 and pinned OpenPGP verification succeeded",
        sha256=digest,
        signing_fingerprint=fingerprint,
    )


def _catalog_download_destination(args: argparse.Namespace, version: VersionAssets) -> Path | None:
    raw = str(getattr(args, "download_destination", "") or "")
    if raw:
        return Path(raw)
    directory = str(getattr(args, "download_directory", "") or "")
    if directory:
        return Path(directory) / Path(version.img_url).name
    return None


def _prepare_source(
    controller: ApplicationController,
    args: argparse.Namespace,
    sink: EventSink,
    *,
    for_execution: bool,
) -> str | None:
    controller.detect_running_tails()
    image_value = str(getattr(args, "image", "") or "")
    catalog_version = str(getattr(args, "catalog_version", "") or "")
    running_source = bool(getattr(args, "running_source", False))
    attached_source = str(getattr(args, "attached_source", "") or "")

    if image_value:
        image = Path(image_value).expanduser().resolve()
        if not image.is_file():
            raise CliError(
                f"Local image does not exist or is not a regular file: {image}",
                exit_code=ExitCode.INVALID_INPUT,
                category="invalid_local_image",
            )
        controller.set_source_mode(SourceMode.LOCAL)
        _verify_local_source(controller, args, sink, image, for_execution=for_execution)
        sink.resolution(resource="operation_source", type="local_image", path=str(image))
        return str(image)

    if catalog_version:
        version = _select_version(controller, catalog_version, sink)
        if not for_execution:
            controller.set_source_mode(SourceMode.REMOTE)
            sink.resolution(
                resource="operation_source",
                type="catalog_image",
                version=version.version,
                image_url=version.img_url,
                download_required=True,
            )
            return None
        result = _download_selected_version(
            controller,
            version,
            sink,
            destination=_catalog_download_destination(args, version),
        )
        sink.resolution(
            resource="operation_source",
            type="verified_catalog_image",
            version=version.version,
            path=str(result.path),
        )
        return str(result.path)

    if running_source:
        if not controller.state.running_tails_available or not controller.state.running_tails_device:
            raise CliError(
                "The running Tails source device could not be determined.",
                exit_code=ExitCode.INVALID_INPUT,
                category="running_tails_unavailable",
            )
        controller.set_source_mode(SourceMode.RUNNING)
        sink.resolution(
            resource="operation_source",
            type="running_tails",
            version=controller.state.running_tails_version,
            device=controller.state.running_tails_device,
        )
        return None

    if attached_source:
        source = _attached_source_from_args(args)
        assert source is not None
        controller.set_attached_live_source(source.device_path, source.mount_point)
        sink.resolution(
            resource="operation_source",
            type="attached_tails",
            version=source.version or "",
            device=source.device_path,
            mount_point=str(source.mount_point),
        )
        return None

    raise CliError(
        "Choose exactly one source: --image, --version, --running-source, or --attached-source.",
        exit_code=ExitCode.INVALID_INPUT,
        category="missing_source",
    )


def _blocked_plan_exit(plan: dict[str, object]) -> ExitCode:
    target = cast(dict[str, object], plan["target"])
    if target.get("disabled_reason") or target.get("read_only"):
        return ExitCode.SAFETY_REFUSAL
    return ExitCode.INVALID_INPUT


def _plan_operation(
    args: argparse.Namespace,
    sink: EventSink,
    *,
    operation: OperationKind,
) -> CommandResult:
    controller = _build_controller(args.remote_index_url)
    try:
        image_path = _prepare_source(controller, args, sink, for_execution=False)
        _scan_devices(controller, sink)
        try:
            plan = controller.plan_target_operation(operation, args.target, image_path)
        except RuntimeError as error:
            raise CliError(
                str(error),
                exit_code=ExitCode.INVALID_INPUT,
                category="target_resolution_failed",
            ) from error
        payload = plan.to_dict()
        for warning in plan.warnings:
            sink.warning(warning)
        sink.resolution(
            resource="operation_plan",
            operation=operation.value,
            target=plan.target.path,
            stable_target=plan.target.stable_path,
            destructive_scope=plan.destructive_scope,
            persistence_behavior=plan.persistence_behavior,
            would_write=plan.would_write,
        )
        exit_code = ExitCode.SUCCESS if not plan.blocking_errors else _blocked_plan_exit(payload)
        human = [
            f"operation: {operation.value}",
            f"source: {plan.source_label}",
            f"target: {plan.target_label}",
            f"destructive scope: {plan.destructive_scope}",
            f"persistence: {plan.persistence_behavior}",
            f"confirmation token: {target_confirmation_token(plan.target) or 'unavailable'}",
            f"would write: {'yes' if plan.would_write else 'no'}",
        ]
        human.extend(f"warning: {warning}" for warning in plan.warnings)
        human.extend(f"blocking error: {error}" for error in plan.blocking_errors)
        return CommandResult(payload={"plan": payload}, human_text="\n".join(human), exit_code=exit_code)
    finally:
        controller.shutdown()


def handle_plan(args: argparse.Namespace, sink: EventSink) -> CommandResult:
    return _plan_operation(args, sink, operation=OperationKind(args.plan_command))


def _post_write_options(args: argparse.Namespace) -> PostWriteOptions:
    enabled = bool(getattr(args, "enable_post_write_customizations", False))
    entries = list(getattr(args, "boot_order_entry", []) or [])
    if entries and not enabled:
        raise CliError(
            "--boot-order-entry requires --enable-post-write-customizations.",
            exit_code=ExitCode.INVALID_INPUT,
            category="post_write_option_requires_enable",
        )
    return PostWriteOptions(
        enabled=enabled,
        sync_device=not bool(getattr(args, "disable_post_write_sync", False)),
        settle_seconds=max(float(getattr(args, "post_write_settle_seconds", 2.0)), 0.0),
        log_file_path=str(getattr(args, "post_write_log_file", "") or ""),
        boot_loader_order=BootLoaderOrderOptions(enabled=bool(entries), entries=entries),
    )


def _progress_callback(sink: EventSink) -> Callable[[str], None]:
    def emit(message: str) -> None:
        fields: dict[str, object] = {}
        match = _DD_BYTES_RE.search(message)
        if match is not None:
            with suppress(ValueError):
                fields["bytes_written"] = int(match.group("bytes"))
        sink.progress(message, **fields)

    return emit


def _require_destructive_acknowledgement(
    args: argparse.Namespace,
    target: BlockDevice,
) -> str:
    token = target_confirmation_token(target)
    if not token:
        raise CliError(
            "Noninteractive writes require a target with a stable WWN, serial, or /dev/disk/by-id identity; this target has none.",
            exit_code=ExitCode.SAFETY_REFUSAL,
            category="unstable_target_identity",
            details={"target": target.path},
        )
    if not args.yes:
        raise CliError(
            "Destructive execution requires --yes in addition to the target identity token.",
            exit_code=ExitCode.SAFETY_REFUSAL,
            category="confirmation_required",
            details={"confirmation_token": token},
        )
    supplied = str(args.target_id or "")
    if supplied != token:
        raise CliError(
            "Target identity acknowledgement does not match the currently selected hardware.",
            exit_code=ExitCode.SAFETY_REFUSAL,
            category="target_identity_mismatch",
            details={"expected_confirmation_token": token, "supplied_target_id": supplied},
        )
    return token


def _execute_operation(
    args: argparse.Namespace,
    sink: EventSink,
    *,
    operation: OperationKind,
) -> CommandResult:
    post_write = _post_write_options(args) if operation == OperationKind.INSTALL else PostWriteOptions()
    controller = _build_controller(args.remote_index_url, post_write)
    try:
        image_path = _prepare_source(controller, args, sink, for_execution=True)
        _scan_devices(controller, sink)
        try:
            plan = controller.plan_target_operation(operation, args.target, image_path)
        except RuntimeError as error:
            raise CliError(
                str(error),
                exit_code=ExitCode.INVALID_INPUT,
                category="target_resolution_failed",
            ) from error
        if plan.blocking_errors:
            raise CliError(
                "Operation plan is blocked: " + "; ".join(plan.blocking_errors),
                exit_code=_blocked_plan_exit(plan.to_dict()),
                category="safety_refusal" if _blocked_plan_exit(plan.to_dict()) == ExitCode.SAFETY_REFUSAL else "invalid_operation_plan",
                details={"plan": plan.to_dict()},
            )
        for warning in plan.warnings:
            sink.warning(warning)
        token = _require_destructive_acknowledgement(args, plan.target)
        sink.resolution(
            resource="destructive_operation",
            operation=operation.value,
            source=plan.source_label,
            target=plan.target.path,
            stable_target=plan.target.stable_path,
            confirmation_token=token,
            destructive_scope=plan.destructive_scope,
            persistence_behavior=plan.persistence_behavior,
        )
        sink.write_boundary(
            "Explicit acknowledgement accepted; controller will revalidate source and target identity immediately before mutation",
            operation=operation.value,
            target=plan.target.path,
            stable_target=plan.target.stable_path,
        )
        try:
            if operation == OperationKind.INSTALL:
                controller.clone_selected_image(
                    image_path,
                    args.target,
                    progress_callback=_progress_callback(sink),
                )
            else:
                controller.upgrade_selected_image(
                    image_path,
                    args.target,
                    progress_callback=_progress_callback(sink),
                )
        except SafetyRefusalError as error:
            raise CliError(
                str(error),
                exit_code=ExitCode.SAFETY_REFUSAL,
                category="safety_refusal",
            ) from error
        except RuntimeError as error:
            if "previously verified downloaded image" in str(error).casefold() or "integrity verification failed" in str(error).casefold():
                raise CliError(
                    str(error),
                    exit_code=ExitCode.VERIFICATION_FAILED,
                    category="verification_failed",
                ) from error
            code = ExitCode.INSTALL_FAILED if operation == OperationKind.INSTALL else ExitCode.UPGRADE_FAILED
            raise CliError(
                str(error),
                exit_code=code,
                category=f"{operation.value}_failed",
            ) from error
        result = {
            "operation": operation.value,
            "executed": True,
            "target": _device_to_dict(plan.target, inspect_facts=False),
            "source": asdict(plan.source),
            "destructive_scope": plan.destructive_scope,
            "persistence_behavior": plan.persistence_behavior,
            "confirmation_token": token,
            "post_write_customizations": post_write.enabled if operation == OperationKind.INSTALL else False,
        }
        return CommandResult(
            payload={"operation_result": result},
            human_text=(
                f"{operation.value} completed successfully\n"
                f"target: {plan.target.stable_path or plan.target.path}\n"
                f"scope: {plan.destructive_scope}\n"
                f"persistence: {plan.persistence_behavior}"
            ),
        )
    finally:
        controller.shutdown()


def handle_install(args: argparse.Namespace, sink: EventSink) -> CommandResult:
    return _execute_operation(args, sink, operation=OperationKind.INSTALL)


def handle_upgrade(args: argparse.Namespace, sink: EventSink) -> CommandResult:
    return _execute_operation(args, sink, operation=OperationKind.UPGRADE)


def _add_source_arguments(parser: argparse.ArgumentParser, *, destructive: bool) -> None:
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--image", help="Local Tails IMG/ISO path.")
    source.add_argument(
        "--version",
        dest="catalog_version",
        metavar="VERSION",
        help="Official catalog version (or 'latest'); execution downloads and verifies its IMG automatically.",
    )
    source.add_argument("--running-source", action="store_true", help="Use the Tails medium currently running this session.")
    source.add_argument("--attached-source", metavar="DEVICE", help="Use a separately attached Tails live source device.")
    parser.add_argument("--attached-mount", help="Mount point for --attached-source containing live/Tails.version.")
    parser.add_argument("--source-version", default="", help="Optional version label for a local image.")
    parser.add_argument("--sha256", default="", help="Expected SHA-256 digest for a local --image.")
    parser.add_argument("--signature", default="", help="Detached OpenPGP signature file for a local --image.")
    if destructive:
        parser.add_argument(
            "--allow-unverified-local-image",
            action="store_true",
            help="Explicitly allow an unverified local image. Official catalog downloads are always verified.",
        )
        parser.add_argument("--download-destination", help="Destination file for an automatically downloaded catalog IMG.")
        parser.add_argument("--download-directory", help="Destination directory for an automatically downloaded catalog IMG.")


def _add_target_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--target",
        required=True,
        help="Target disk path or stable /dev/disk/by-id path.",
    )


def _add_confirmation_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--yes", action="store_true", help="Acknowledge that the requested operation is destructive.")
    parser.add_argument(
        "--target-id",
        default="",
        help="Exact confirmation_token reported by `devices inspect` or `plan` for the selected target.",
    )


def _add_install_post_write_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--enable-post-write-customizations",
        action="store_true",
        help="Enable the same optional post-write customization hook exposed by the GUI.",
    )
    parser.add_argument(
        "--disable-post-write-sync",
        action="store_true",
        help="Skip the optional customization hook's explicit buffer sync.",
    )
    parser.add_argument(
        "--post-write-settle-seconds",
        type=float,
        default=2.0,
        help="Settle delay used by optional post-write customizations (default: 2.0).",
    )
    parser.add_argument("--post-write-log-file", default="", help="Legacy post-write hook audit-log file.")
    parser.add_argument(
        "--boot-order-entry",
        action="append",
        default=[],
        metavar="ENTRY",
        help="Experimental desired boot-menu entry order; repeat for each entry and enable customizations.",
    )


def build_cli_parser() -> argparse.ArgumentParser:
    examples = """examples:
  tails-cloner-clone versions latest --output json
  tails-cloner-clone status --output json
  tails-cloner-clone devices inspect /dev/disk/by-id/usb-... --output json
  tails-cloner-clone download latest --destination ~/Downloads/tails.img --output jsonl
  tails-cloner-clone plan upgrade --running-source --target /dev/disk/by-id/usb-...
  tails-cloner-clone install --version latest --target /dev/disk/by-id/usb-... --yes --target-id 'serial:...'
"""
    parser = argparse.ArgumentParser(
        description="Tails Cloner Clone automation/operator CLI. Omit all arguments to launch the GUI; any nonempty argv enters this CLI.",
        epilog=examples,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--output",
        choices=[mode.value for mode in OutputMode],
        default=OutputMode.HUMAN.value,
        help="Output mode. json emits one final envelope; jsonl streams schema-v1 events.",
    )
    parser.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--log-file",
        default="",
        help="Append the stable tails-cloner-cli/v1 JSONL event stream to this file.",
    )
    parser.add_argument(
        "--remote-index-url",
        default=os.environ.get("TAILS_REMOTE_INDEX_URL", DEFAULT_REMOTE_INDEX_URL),
        help="Remote Tails directory-listing base URL. Asset and metadata fetches remain HTTPS-only.",
    )
    parser.add_argument("--version", action="version", version=f"tails-cloner-clone {__version__}")
    subcommands = parser.add_subparsers(dest="command", required=True)

    status = subcommands.add_parser("status", aliases=["inspect"], help="Inspect application, running-source, current-system, and device state.")
    status.add_argument("--attached-source", metavar="DEVICE", help="Optionally include a mounted attached Tails source.")
    status.add_argument("--attached-mount", help="Mount point paired with --attached-source.")
    status.set_defaults(func=handle_status)

    versions = subcommands.add_parser("versions", help="Retrieve official Tails version and asset metadata.")
    versions_sub = versions.add_subparsers(dest="versions_command", required=True)
    versions_sub.add_parser("list", help="List stable versions and mark latest/default.").set_defaults(func=handle_versions)
    versions_sub.add_parser("latest", help="Show latest/default version metadata.").set_defaults(func=handle_versions)
    versions_show = versions_sub.add_parser("show", help="Show one exact version's ISO/IMG/signature/checksum metadata.")
    versions_show.add_argument("version")
    versions_show.set_defaults(func=handle_versions)

    devices = subcommands.add_parser("devices", help="Discover targets with stable identity and Tails/persistence evidence.")
    devices_sub = devices.add_subparsers(dest="devices_command", required=True)
    devices_sub.add_parser("list", help="List discovered block devices.").set_defaults(func=handle_devices)
    devices_inspect = devices_sub.add_parser("inspect", help="Inspect one device by kernel or stable by-id path.")
    devices_inspect.add_argument("device")
    devices_inspect.set_defaults(func=handle_devices)

    source = subcommands.add_parser("source", help="Inspect or validate live Tails sources.")
    source_sub = source.add_subparsers(dest="source_command", required=True)
    source_sub.add_parser("running", help="Inspect the currently running Tails live source.").set_defaults(func=handle_source)
    attached = source_sub.add_parser("validate-attached", help="Validate an explicitly mounted attached Tails live source.")
    attached.add_argument("--device", required=True)
    attached.add_argument("--mount-point", required=True)
    attached.set_defaults(func=handle_source)

    download = subcommands.add_parser("download", help="Download and cryptographically verify an official Tails IMG.")
    download.add_argument("version", nargs="?", default="latest", help="Version or 'latest' (default).")
    destination = download.add_mutually_exclusive_group()
    destination.add_argument("--destination", help="Explicit output file path.")
    destination.add_argument("--directory", help="Output directory using the official IMG filename.")
    download.set_defaults(func=handle_download)

    plan = subcommands.add_parser("plan", help="Resolve an install/upgrade without crossing the write boundary.")
    plan_sub = plan.add_subparsers(dest="plan_command", required=True)
    for name in ("install", "upgrade"):
        operation = plan_sub.add_parser(name, help=f"Plan a {name} operation.")
        _add_target_argument(operation)
        _add_source_arguments(operation, destructive=False)
        operation.set_defaults(func=handle_plan)

    install = subcommands.add_parser("install", help="Whole-device install/reinstall using controller safety revalidation.")
    _add_target_argument(install)
    _add_source_arguments(install, destructive=True)
    _add_confirmation_arguments(install)
    _add_install_post_write_arguments(install)
    install.set_defaults(func=handle_install)

    upgrade = subcommands.add_parser("upgrade", help="Persistence-preserving Tails system-partition upgrade.")
    _add_target_argument(upgrade)
    _add_source_arguments(upgrade, destructive=True)
    _add_confirmation_arguments(upgrade)
    upgrade.set_defaults(func=handle_upgrade)
    return parser


def _normalize_global_options(argv: Sequence[str] | None) -> list[str] | None:
    """Allow output/log/catalog globals before or after nested subcommands."""
    if argv is None:
        return None
    values = list(argv)
    globals_: list[str] = []
    remaining: list[str] = []
    index = 0
    value_options = {"--output", "--log-file", "--remote-index-url"}
    while index < len(values):
        value = values[index]
        if value == "--json":
            globals_.append(value)
            index += 1
            continue
        if value in value_options and index + 1 < len(values):
            globals_.extend(values[index : index + 2])
            index += 2
            continue
        if any(value.startswith(f"{option}=") for option in value_options):
            globals_.append(value)
            index += 1
            continue
        remaining.append(value)
        index += 1
    return globals_ + remaining


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_cli_parser()
    args = parser.parse_args(_normalize_global_options(argv))
    mode = OutputMode.JSON if args.json else OutputMode(args.output)
    sink = EventSink(
        command=str(args.command),
        output_mode=mode,
        log_file=Path(args.log_file).expanduser() if args.log_file else None,
    )
    sink.start(application_version=__version__)
    handler = cast(Handler, args.func)
    try:
        result = handler(args, sink)
    except CliError as error:
        sink.fail(error)
        return int(error.exit_code)
    except KeyboardInterrupt:
        interrupt_error = CliError(
            "Operation interrupted by user.",
            exit_code=ExitCode.INTERNAL_ERROR,
            category="interrupted",
        )
        sink.fail(interrupt_error)
        return int(interrupt_error.exit_code)
    except Exception as error:  # noqa: BLE001 - stable final failure object is part of CLI contract
        wrapped = CliError(
            f"Unexpected CLI failure: {error}",
            exit_code=ExitCode.INTERNAL_ERROR,
            category="internal_error",
        )
        sink.fail(wrapped)
        return int(wrapped.exit_code)
    sink.complete(result)
    return int(result.exit_code)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
