from __future__ import annotations

import subprocess
from pathlib import Path

from tails_cloner.models import BootLoaderOrderOptions, PostWriteOptions
from tails_cloner.post_write import apply_post_write_options


def test_apply_post_write_skips_boot_order_when_disabled(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    options = PostWriteOptions(enabled=True, sync_device=False, settle_seconds=0)
    apply_post_write_options(str(tmp_path / "dev"), options, command_runner=fake_run)

    assert calls == []


def test_apply_post_write_mounts_detected_tails_partition_through_polkit(
    monkeypatch,
) -> None:
    mounted_at: dict[str, str] = {}
    commands: list[list[str]] = []

    def fake_apply(root: Path, desired_order: list[str]):
        mounted_at["root"] = str(root)
        mounted_at["desired"] = ",".join(desired_order)

        class _Result:
            files: list[object] = []

        return _Result()

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("tails_cloner.post_write._find_tails_boot_partition", lambda _device: "/dev/sdb7")
    monkeypatch.setattr("tails_cloner.post_write.build_privileged_command", lambda command: ["pkexec", *command])
    monkeypatch.setattr("tails_cloner.post_write.apply_boot_loader_order_to_directory", fake_apply)
    options = PostWriteOptions(
        enabled=True,
        sync_device=False,
        settle_seconds=0,
        boot_loader_order=BootLoaderOrderOptions(enabled=True, entries=["B", "A"]),
    )

    apply_post_write_options("/dev/sdb", options, command_runner=fake_run)

    assert commands[0][:3] == ["pkexec", "mount", "-o"]
    assert "uid=" in commands[0][3]
    assert "gid=" in commands[0][3]
    assert commands[0][-2] == "/dev/sdb7"
    assert commands[-1][0:3] == ["pkexec", "umount", "--"]
    assert mounted_at["desired"] == "B,A"


def test_apply_post_write_uses_partition_resolver_not_name_guessing(monkeypatch) -> None:
    commands: list[list[str]] = []

    class _Result:
        files: list[object] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(
        "tails_cloner.post_write._find_tails_boot_partition",
        lambda device: "/dev/mapper/custom-tails" if device == "/dev/nvme0n1" else "",
    )
    monkeypatch.setattr("tails_cloner.post_write.build_privileged_command", lambda command: ["pkexec", *command])
    monkeypatch.setattr(
        "tails_cloner.post_write.apply_boot_loader_order_to_directory",
        lambda _root, _desired_order: _Result(),
    )
    options = PostWriteOptions(
        enabled=True,
        sync_device=False,
        settle_seconds=0,
        boot_loader_order=BootLoaderOrderOptions(enabled=True, entries=["Tails"]),
    )

    apply_post_write_options("/dev/nvme0n1", options, command_runner=fake_run)

    assert commands[0][-2] == "/dev/mapper/custom-tails"
