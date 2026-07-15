from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from tails_cloner.creator import (
    build_clone_command,
    clone_image_to_device,
    inspect_target_deactivation_commands,
    prepare_target_device,
)
from tails_cloner.models import PostWriteOptions

ProgressCallback = Callable[[str], None]


def _no_prepare(_device: str, _required_size: int, _runner, _callback: ProgressCallback) -> None:
    return None


def test_build_clone_command_prefers_pkexec_and_progress() -> None:
    command = build_clone_command("/tmp/tails.iso", "/dev/sdb")

    assert command == [
        "pkexec",
        "dd",
        "if=/tmp/tails.iso",
        "of=/dev/sdb",
        "bs=4M",
        "status=progress",
        "oflag=direct",
        "conv=fsync",
    ]


def test_target_deactivation_unmounts_swap_and_closes_encryption() -> None:
    payload = {
        "blockdevices": [
            {
                "path": "/dev/sdb",
                "name": "sdb",
                "type": "disk",
                "ro": False,
                "size": 32 * 1024**3,
                "children": [
                    {
                        "path": "/dev/sdb1",
                        "name": "sdb1",
                        "type": "part",
                        "fstype": "vfat",
                        "mountpoints": ["/media/tails"],
                    },
                    {
                        "path": "/dev/sdb2",
                        "name": "sdb2",
                        "type": "part",
                        "fstype": "swap",
                        "mountpoints": ["[SWAP]"],
                    },
                    {
                        "path": "/dev/sdb3",
                        "name": "sdb3",
                        "type": "part",
                        "fstype": "crypto_LUKS",
                        "mountpoints": [None],
                        "children": [
                            {
                                "path": "/dev/mapper/TailsData_unlocked",
                                "name": "TailsData_unlocked",
                                "type": "crypt",
                                "fstype": "ext4",
                                "mountpoints": ["/media/persistence"],
                            }
                        ],
                    },
                ],
            }
        ]
    }

    def fake_inspect(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

    assert inspect_target_deactivation_commands("/dev/sdb", fake_inspect) == [
        ["pkexec", "umount", "--", "/media/persistence"],
        ["pkexec", "cryptsetup", "close", "TailsData_unlocked"],
        ["pkexec", "swapoff", "--", "/dev/sdb2"],
        ["pkexec", "umount", "--", "/media/tails"],
    ]


def test_target_inspection_rejects_current_os_disk() -> None:
    payload = {
        "blockdevices": [
            {
                "path": "/dev/sda",
                "type": "disk",
                "ro": False,
                "size": 32 * 1024**3,
                "children": [
                    {
                        "path": "/dev/sda1",
                        "type": "part",
                        "fstype": "ext4",
                        "mountpoints": ["/"],
                    }
                ],
            }
        ]
    }

    def fake_inspect(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

    with pytest.raises(RuntimeError, match="currently running operating system"):
        inspect_target_deactivation_commands("/dev/sda", fake_inspect)


def test_target_inspection_rejects_oversized_image_before_deactivation() -> None:
    payload = {
        "blockdevices": [
            {
                "path": "/dev/sdb",
                "type": "disk",
                "ro": False,
                "size": 1024,
                "children": [],
            }
        ]
    }

    def fake_inspect(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

    with pytest.raises(RuntimeError, match="Image is larger"):
        inspect_target_deactivation_commands(
            "/dev/sdb",
            fake_inspect,
            required_size_bytes=2048,
        )


def test_prepare_target_stops_on_failed_deactivation() -> None:
    payload = {
        "blockdevices": [
            {
                "path": "/dev/sdb",
                "type": "disk",
                "ro": False,
                "size": 32 * 1024**3,
                "children": [
                    {
                        "path": "/dev/sdb1",
                        "type": "part",
                        "fstype": "vfat",
                        "mountpoints": ["/media/tails"],
                    }
                ],
            }
        ]
    }

    def fake_inspect(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

    def failed_run(_command: list[str], _callback: ProgressCallback) -> int:
        return 1

    with pytest.raises(RuntimeError, match="Target preparation command exited"):
        prepare_target_device("/dev/sdb", 1024, failed_run, lambda _message: None, fake_inspect)


def test_clone_image_to_device_prepares_target_before_dd(tmp_path: Path) -> None:
    image_path = tmp_path / "tails.iso"
    image_path.write_bytes(b"test")
    calls: list[str] = []

    def fake_prepare(_device: str, required_size: int, _runner, _callback: ProgressCallback) -> None:
        assert required_size == 4
        calls.append("prepare")

    def fake_run(command: list[str], progress_callback: ProgressCallback) -> int:
        if "dd" in command:
            calls.append("dd")
            progress_callback("copied")
        elif command[:3] == ["pkexec", "blockdev", "--rereadpt"]:
            calls.append("reread")
        elif command == ["udevadm", "settle"]:
            calls.append("settle")
        return 0

    progress: list[str] = []
    clone_image_to_device(
        image_path=image_path,
        device_path="/dev/sdb",
        run_command=fake_run,
        progress_callback=progress.append,
        target_preparer=fake_prepare,
    )

    assert calls == ["prepare", "dd", "reread", "settle"]
    assert progress == ["copied"]


def test_clone_image_to_device_runs_post_write_hook_when_enabled(tmp_path: Path) -> None:
    image_path = tmp_path / "tails.iso"
    image_path.write_bytes(b"test")
    seen: dict[str, object] = {}

    def fake_run(command: list[str], progress_callback: ProgressCallback) -> int:
        if "dd" in command:
            progress_callback("copied")
        return 0

    def fake_post_write(
        device_path: str,
        options: PostWriteOptions,
        progress_callback: ProgressCallback | None,
    ) -> None:
        seen["post_write_device"] = device_path
        seen["post_write_enabled"] = options.enabled
        if progress_callback is not None:
            progress_callback("post-write done")

    progress: list[str] = []
    clone_image_to_device(
        image_path=image_path,
        device_path="/dev/sdb",
        run_command=fake_run,
        progress_callback=progress.append,
        post_write_options=PostWriteOptions(enabled=True),
        post_write_runner=fake_post_write,
        target_preparer=_no_prepare,
    )

    assert seen == {"post_write_device": "/dev/sdb", "post_write_enabled": True}
    assert progress == ["copied", "post-write done"]


def test_clone_failure_does_not_run_post_write(tmp_path: Path) -> None:
    image_path = tmp_path / "tails.iso"
    image_path.write_bytes(b"test")
    post_write_called = False

    def fake_run(_command: list[str], _progress_callback: ProgressCallback) -> int:
        return 2

    def fake_post_write(
        _device_path: str,
        _options: PostWriteOptions,
        _progress_callback: ProgressCallback | None,
    ) -> None:
        nonlocal post_write_called
        post_write_called = True

    with pytest.raises(RuntimeError, match="status 2"):
        clone_image_to_device(
            image_path=image_path,
            device_path="/dev/sdb",
            run_command=fake_run,
            post_write_runner=fake_post_write,
            target_preparer=_no_prepare,
        )

    assert post_write_called is False
