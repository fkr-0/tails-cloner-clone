"""Whole-device Tails image installation primitives."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path

from tails_cloner.blockdev import (
    mountpoints,
    protected_mountpoints,
    run_streaming_command,
    walk_nodes,
)
from tails_cloner.models import PostWriteOptions
from tails_cloner.post_write import apply_post_write_options
from tails_cloner.source import LocalImageSource

RunCloneCommand = Callable[[list[str], Callable[[str], None]], int]
InspectRun = Callable[..., subprocess.CompletedProcess[str]]
TargetPreparer = Callable[[str, int, RunCloneCommand, Callable[[str], None]], None]

TARGET_LSBLK_COLUMNS = "PATH,NAME,TYPE,FSTYPE,MOUNTPOINTS,RO,SIZE"


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


def run_clone_command(command: list[str], progress_callback: Callable[[str], None]) -> int:
    return run_streaming_command(command, progress_callback)


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
        ["lsblk", "--json", "--bytes", "--output", TARGET_LSBLK_COLUMNS, device_path],
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
    try:
        target_size_bytes = int(root.get("size") or 0)
    except (TypeError, ValueError) as error:
        raise RuntimeError(f"Could not read the target size from lsblk: {root.get('size')!r}") from error
    if required_size_bytes > 0 and target_size_bytes > 0 and required_size_bytes > target_size_bytes:
        raise RuntimeError(
            "Image is larger than the whole-device target: "
            f"{required_size_bytes} > {target_size_bytes} bytes"
        )

    commands: list[list[str]] = []
    # Include the disk node itself: a whole-disk filesystem (no partition table)
    # reports its mount on the root node and must be refused/unmounted too.
    nodes = list(walk_nodes([root]))
    active_system_mounts = protected_mountpoints(
        mountpoint for node in nodes for mountpoint in mountpoints(node.get("mountpoints"))
    )
    if active_system_mounts:
        raise RuntimeError(
            "Refusing to overwrite a disk used by the currently running operating system: "
            + ", ".join(active_system_mounts)
        )

    for node in reversed(nodes):
        path = str(node.get("path") or "")
        node_mounts = mountpoints(node.get("mountpoints"))
        fstype = str(node.get("fstype") or "").casefold()

        if fstype == "swap" or "[SWAP]" in node_mounts:
            if path:
                commands.append(["pkexec", "swapoff", "--", path])
            continue

        for mountpoint in node_mounts:
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
