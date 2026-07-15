from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

from tails_cloner.config import DEFAULT_REMOTE_INDEX_URL
from tails_cloner.controller import ApplicationController
from tails_cloner.devices import DeviceService
from tails_cloner.models import AppState, BlockDevice, VersionAssets
from tails_cloner.planner import OperationKind, OperationSource, plan_operation
from tails_cloner.remote_index import RemoteVersionIndex
from tails_cloner.source import (
    AttachedLiveSystemSource,
    RunningLiveSystemSource,
    get_parent_disk_path,
)


class _NoWriteCloneService:
    def clone_image(self, *_args: object, **_kwargs: object) -> None:
        raise RuntimeError("write operations are not implemented by the read-only CLI")

    def upgrade_image(self, *_args: object, **_kwargs: object) -> None:
        raise RuntimeError("write operations are not implemented by the read-only CLI")

    def upgrade_from_device(self, *_args: object, **_kwargs: object) -> None:
        raise RuntimeError("write operations are not implemented by the read-only CLI")


class _VersionService:
    def __init__(self, index: RemoteVersionIndex) -> None:
        self._index = index

    def fetch_versions(self) -> list[VersionAssets]:
        return self._index.fetch_versions()


def _print_json(payload: object) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def _device_to_dict(device: BlockDevice) -> dict[str, Any]:
    return {
        "path": device.path,
        "size_bytes": device.size_bytes,
        "size_label": device.size_label,
        "model": device.model,
        "vendor": device.vendor,
        "transport": device.transport,
        "removable": device.removable,
        "read_only": device.read_only,
        "fstype": device.fstype,
        "label": device.label,
        "is_gpt": device.is_gpt,
        "is_isohybrid": device.is_isohybrid,
        "has_tails": device.has_tails,
        "is_big_enough_for_installation": device.is_big_enough_for_installation,
        "is_big_enough_for_upgrade": device.is_big_enough_for_upgrade,
        "is_running_system_device": device.is_running_system_device,
        "is_attached_source_device": device.is_attached_source_device,
        "selectable": device.selectable,
        "disabled_reason": device.disabled_reason,
        "pretty_name": device.pretty_name,
    }


def _version_to_dict(version: VersionAssets) -> dict[str, str]:
    return asdict(version)


def _build_controller(remote_index_url: str) -> ApplicationController:
    return ApplicationController(
        state=AppState(),
        version_service=_VersionService(RemoteVersionIndex(base_url=remote_index_url)),
        device_service=DeviceService(),
        clone_service=_NoWriteCloneService(),
    )


def _format_device_table(devices: list[BlockDevice]) -> str:
    rows = [("PATH", "SIZE", "TYPE", "TAILS", "SELECTABLE", "STATUS")]
    for device in devices:
        kind = "removable" if device.removable else "internal/other"
        status = device.disabled_reason or "ok"
        rows.append(
            (
                device.path,
                device.size_label,
                kind,
                "yes" if device.has_tails else "no",
                "yes" if device.selectable else "no",
                status,
            )
        )
    widths = [max(len(row[column]) for row in rows) for column in range(len(rows[0]))]
    return "\n".join(
        "  ".join(value.ljust(widths[index]) for index, value in enumerate(row))
        for row in rows
    )


def _find_device(devices: list[BlockDevice], path: str) -> BlockDevice | None:
    normalized = get_parent_disk_path(path)
    for device in devices:
        if device.path == path or device.path == normalized:
            return device
    return None


def handle_versions(args: argparse.Namespace) -> int:
    index = RemoteVersionIndex(base_url=args.remote_index_url)
    versions = index.fetch_versions()
    if args.versions_command == "list":
        if args.json:
            _print_json({"versions": [_version_to_dict(version) for version in versions]})
        else:
            for version in versions:
                print(version.version)
        return 0

    selected = next((version for version in versions if version.version == args.version), None)
    if selected is None:
        raise SystemExit(f"version not found: {args.version}")
    if args.json:
        _print_json({"version": _version_to_dict(selected)})
    else:
        print(f"version: {selected.version}")
        print(f"directory: {selected.directory_url}")
        print(f"iso: {selected.iso_url}")
        print(f"img: {selected.img_url}")
        print(f"sig: {selected.sig_url}")
        print(f"sha256: {selected.sha256_url}")
    return 0


def handle_devices(args: argparse.Namespace) -> int:
    controller = _build_controller(args.remote_index_url)
    try:
        controller._detect_running_tails()
        controller.refresh_devices()
        devices = controller.state.devices
        if args.devices_command == "list":
            if args.json:
                _print_json({"devices": [_device_to_dict(device) for device in devices]})
            else:
                print(_format_device_table(devices))
            return 0

        device = _find_device(devices, args.device)
        if device is None:
            raise SystemExit(f"device not found: {args.device}")
        if args.json:
            _print_json({"device": _device_to_dict(device)})
        else:
            for key, value in _device_to_dict(device).items():
                print(f"{key}: {value}")
        return 0
    finally:
        controller.shutdown()


def handle_source(args: argparse.Namespace) -> int:
    payload: dict[str, object]
    if args.source_command == "running":
        running_source = RunningLiveSystemSource()
        payload = {
            "running_tails_available": running_source.exists,
            "version": running_source.version or "",
            "device": running_source.device or "",
            "parent_device": get_parent_disk_path(running_source.device or ""),
            "mount_point": str(running_source.mount_point),
            "iso_path": str(running_source.get_iso_path() or ""),
        }
    elif args.source_command == "validate-attached":
        attached_source = AttachedLiveSystemSource(device_path=args.device, mount_point=Path(args.mount_point))
        try:
            attached_source.validate()
            valid = True
            error = ""
        except Exception as exc:  # noqa: BLE001 - surfaced as CLI validation error
            valid = False
            error = str(exc)
        payload = {
            "valid": valid,
            "error": error,
            "device": attached_source.device_path,
            "parent_device": attached_source.parent_device,
            "mount_point": str(attached_source.mount_point),
            "version": attached_source.version or "",
            "live_path": str(attached_source.get_liveos_path()),
            "iso_path": str(attached_source.get_iso_path() or ""),
        }
    else:
        raise SystemExit(f"unknown source command: {args.source_command}")

    if args.json:
        _print_json(payload)
    else:
        for key, value in payload.items():
            print(f"{key}: {value}")
    if args.source_command == "validate-attached" and not payload["valid"]:
        return 1
    return 0


def handle_plan(args: argparse.Namespace) -> int:
    controller = _build_controller(args.remote_index_url)
    try:
        controller._detect_running_tails()
        controller.refresh_devices()
        device = _find_device(controller.state.devices, args.target)
        if device is None:
            raise SystemExit(f"target device not found: {args.target}")
        if args.image:
            image = Path(args.image)
            try:
                image_size = image.stat().st_size if image.is_file() else 0
            except OSError:
                image_size = 0
            source = OperationSource(type="image", path=str(image), size_bytes=image_size)
        elif args.running_source:
            running = RunningLiveSystemSource()
            source = OperationSource(
                type="running_source",
                device=running.device or "",
                version=running.version or "",
            )
        else:
            raise SystemExit("plan requires --image or --running-source")

        plan = plan_operation(
            operation=OperationKind(args.plan_command),
            source=source,
            target=device,
        )
        payload = plan.to_dict()
        if args.json:
            _print_json(payload)
        else:
            print(f"operation: {payload['operation']}")
            print(f"target: {device.path}")
            print(f"would_write: {payload['would_write']}")
            for warning in payload["warnings"]:
                print(f"warning: {warning}")
            for error in payload["blocking_errors"]:
                print(f"error: {error}")
        return 1 if payload["blocking_errors"] else 0
    finally:
        controller.shutdown()


def handle_download(_args: argparse.Namespace) -> int:
    raise SystemExit(
        "download commands are specified but not implemented yet; use versions show to retrieve URLs"
    )


def handle_write_placeholder(_args: argparse.Namespace) -> int:
    raise SystemExit("write commands are intentionally not implemented in this CLI phase")


def build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CLI helpers for Tails Cloner Clone.")
    parser.add_argument("--json", action="store_true", help="Emit JSON output where supported.")
    parser.add_argument(
        "--remote-index-url",
        default=DEFAULT_REMOTE_INDEX_URL,
        help="Remote Tails directory listing base URL.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    versions = subcommands.add_parser("versions", help="Retrieve Tails version metadata.")
    versions_subcommands = versions.add_subparsers(dest="versions_command", required=True)
    versions_subcommands.add_parser("list", help="List available versions.").set_defaults(func=handle_versions)
    versions_show = versions_subcommands.add_parser("show", help="Show one version's assets.")
    versions_show.add_argument("version")
    versions_show.set_defaults(func=handle_versions)

    devices = subcommands.add_parser("devices", help="Enumerate target devices.")
    devices_subcommands = devices.add_subparsers(dest="devices_command", required=True)
    devices_subcommands.add_parser("list", help="List devices.").set_defaults(func=handle_devices)
    devices_inspect = devices_subcommands.add_parser("inspect", help="Inspect one device.")
    devices_inspect.add_argument("device")
    devices_inspect.set_defaults(func=handle_devices)

    source = subcommands.add_parser("source", help="Inspect source media.")
    source_subcommands = source.add_subparsers(dest="source_command", required=True)
    source_subcommands.add_parser("running", help="Show currently running Tails source.").set_defaults(func=handle_source)
    validate_attached = source_subcommands.add_parser("validate-attached", help="Validate a mounted attached Tails live source.")
    validate_attached.add_argument("--device", required=True, help="Attached source partition path, for example /dev/sdb1.")
    validate_attached.add_argument("--mount-point", required=True, help="Mount point containing live/Tails.version.")
    validate_attached.set_defaults(func=handle_source)

    plan = subcommands.add_parser("plan", help="Create a dry-run operation plan.")
    plan_subcommands = plan.add_subparsers(dest="plan_command", required=True)
    for command in ["install", "upgrade"]:
        plan_command = plan_subcommands.add_parser(command, help=f"Plan a {command} operation.")
        plan_command.add_argument("--target", required=True)
        source_group = plan_command.add_mutually_exclusive_group(required=True)
        source_group.add_argument("--image")
        source_group.add_argument("--running-source", action="store_true")
        plan_command.set_defaults(func=handle_plan)

    download = subcommands.add_parser("download", help="Download Tails assets. Placeholder for next phase.")
    download.set_defaults(func=handle_download)
    install = subcommands.add_parser("install", help="Install to a target device. Placeholder for next phase.")
    install.set_defaults(func=handle_write_placeholder)
    upgrade = subcommands.add_parser("upgrade", help="Upgrade a target device. Placeholder for next phase.")
    upgrade.set_defaults(func=handle_write_placeholder)
    return parser


def _normalize_global_options(argv: Sequence[str] | None) -> list[str] | None:
    """Accept global flags before or after subcommands for CLI ergonomics."""
    if argv is None:
        return None
    args = list(argv)
    normalized: list[str] = []
    remaining: list[str] = []
    index = 0
    while index < len(args):
        value = args[index]
        if value == "--json":
            normalized.append(value)
            index += 1
            continue
        if value == "--remote-index-url":
            normalized.extend(args[index:index + 2])
            index += 2
            continue
        if value.startswith("--remote-index-url="):
            normalized.append(value)
            index += 1
            continue
        remaining.append(value)
        index += 1
    return normalized + remaining


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_cli_parser()
    args = parser.parse_args(_normalize_global_options(argv))
    handler = cast(Callable[[argparse.Namespace], int], args.func)
    return handler(args)


def looks_like_cli_invocation(argv: Sequence[str]) -> bool:
    if not argv:
        return False
    return argv[0] in {"versions", "devices", "source", "plan", "download", "install", "upgrade"}


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
