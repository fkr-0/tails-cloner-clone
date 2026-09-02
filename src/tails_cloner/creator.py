"""Whole-device Tails image installation primitives."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from tails_cloner.models import PostWriteOptions
from tails_cloner.post_write import apply_post_write_options
from tails_cloner.source import LocalImageSource

RunCloneCommand = Callable[[list[str], Callable[[str], None]], int]
InspectRun = Callable[..., subprocess.CompletedProcess[str]]
TargetPreparer = Callable[[str, int, RunCloneCommand, Callable[[str], None]], None]

TARGET_LSBLK_COLUMNS = "PATH,NAME,TYPE,FSTYPE,MOUNTPOINTS,RO,SIZE"
SYSTEM_MOUNTPOINTS = {"/", "/boot", "/boot/efi", "/home", "/usr", "/var"}
PROTECTED_LIVE_MOUNT_PREFIXES = ("/lib/live/mount", "/run/live")


def build_clone_command(image_path: str | Path, device_path: str, use_pkexec: bool = True) -> list[str]:
    command = [
        "dd",
        f"if={Path(image_path)}",
        f"of={device_path}",
        "bs=4M",
        "status=progress",
        "oflag=direct",
        "conv=fsync",
    ]
    if use_pkexec:
        return ["pkexec", *command]
    return command


def _stream_process_output(process: subprocess.Popen[str], progress_callback: Callable[[str], None]) -> int:
    assert process.stderr is not None
    for line in process.stderr:
        message = line.strip()
        if message:
            progress_callback(message)
    return process.wait()


def run_clone_command(command: list[str], progress_callback: Callable[[str], None]) -> int:
    process = subprocess.Popen(  # noqa: S603 - destructive system command is the tool's core job
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    return _stream_process_output(process, progress_callback)


def _walk_nodes(nodes: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    for node in nodes:
        yield node
        children = node.get("children") or []
        if isinstance(children, list):
            yield from _walk_nodes([child for child in children if isinstance(child, dict)])


def _mountpoints(value: object) -> list[str]:
    if isinstance(value, list):
        return [mountpoint for mountpoint in value if isinstance(mountpoint, str)]
    if isinstance(value, str):
        return [value]
    return []


def inspect_target_deactivation_commands(
    device_path: str,
    inspect_run: InspectRun = subprocess.run,
    *,
    required_size_bytes: int = 0,
) -> list[list[str]]:
    """Return privileged commands required to make a whole disk safe to overwrite."""
    if not device_path.startswith("/dev/"):
        raise ValueError(f"Whole-device target must be a /dev path: {device_path}")

    result = inspect_run(
        ["lsblk", "--json", "--output", TARGET_LSBLK_COLUMNS, device_path],
        check=True,
        text=True,
        capture_output=True,
    )
    payload = json.loads(result.stdout)
    roots = [node for node in payload.get("blockdevices", []) if isinstance(node, dict)]
    if len(roots) != 1:
        raise RuntimeError(f"Could not resolve exactly one target block device: {device_path}")

    root = roots[0]
    if root.get("type") != "disk":
        raise RuntimeError(f"Whole-device install target is not a disk: {device_path}")
    if bool(root.get("ro", False)):
        raise RuntimeError(f"Whole-device install target is read-only: {device_path}")
    target_size_bytes = int(root.get("size") or 0)
    if required_size_bytes > 0 and target_size_bytes > 0 and required_size_bytes > target_size_bytes:
        raise RuntimeError(
            "Image is larger than the whole-device target: "
            f"{required_size_bytes} > {target_size_bytes} bytes"
        )

    commands: list[list[str]] = []
    descendants = list(_walk_nodes([root]))[1:]
    active_system_mounts = sorted(
        {
            mountpoint
            for node in descendants
            for mountpoint in _mountpoints(node.get("mountpoints"))
            if mountpoint in SYSTEM_MOUNTPOINTS
            or any(
                mountpoint == prefix or mountpoint.startswith(f"{prefix}/")
                for prefix in PROTECTED_LIVE_MOUNT_PREFIXES
            )
        }
    )
    if active_system_mounts:
        raise RuntimeError(
            "Refusing to overwrite a disk used by the currently running operating system: "
            + ", ".join(active_system_mounts)
        )

    for node in reversed(descendants):
        path = str(node.get("path") or "")
        mountpoints = _mountpoints(node.get("mountpoints"))
        fstype = str(node.get("fstype") or "").casefold()

        if fstype == "swap" or "[SWAP]" in mountpoints:
            if path:
                commands.append(["pkexec", "swapoff", "--", path])
            continue

        for mountpoint in mountpoints:
            if mountpoint.startswith("/"):
                commands.append(["pkexec", "umount", "--", mountpoint])

        mapping_name = str(node.get("name") or "")
        if node.get("type") == "crypt" and mapping_name:
            commands.append(["pkexec", "cryptsetup", "close", mapping_name])

    return commands


def prepare_target_device(
    device_path: str,
    required_size_bytes: int,
    run_command: RunCloneCommand,
    progress_callback: Callable[[str], None],
    inspect_run: InspectRun = subprocess.run,
) -> None:
    for command in inspect_target_deactivation_commands(
        device_path,
        inspect_run,
        required_size_bytes=required_size_bytes,
    ):
        progress_callback(f"Preparing target: {' '.join(command[1:])}")
        exit_code = run_command(command, progress_callback)
        if exit_code != 0:
            raise RuntimeError(f"Target preparation command exited with status {exit_code}: {' '.join(command)}")


def clone_image_to_device(
    image_path: str | Path,
    device_path: str,
    run_command: RunCloneCommand = run_clone_command,
    progress_callback: Callable[[str], None] | None = None,
    post_write_options: PostWriteOptions | None = None,
    post_write_runner: Callable[[str, PostWriteOptions, Callable[[str], None] | None], None] = apply_post_write_options,
    target_preparer: TargetPreparer = prepare_target_device,
) -> None:
    image = LocalImageSource(Path(image_path))
    image.validate()
    callback = progress_callback or (lambda _message: None)

    target_preparer(device_path, image.path.stat().st_size, run_command, callback)
    exit_code = run_command(build_clone_command(image.path, device_path), callback)
    if exit_code != 0:
        raise RuntimeError(f"Clone process exited with status {exit_code}")

    for command in (["pkexec", "blockdev", "--rereadpt", device_path], ["udevadm", "settle"]):
        exit_code = run_command(command, callback)
        if exit_code != 0:
            raise RuntimeError(f"Post-write device refresh exited with status {exit_code}: {' '.join(command)}")

    options = post_write_options or PostWriteOptions()
    post_write_runner(device_path, options, callback)
