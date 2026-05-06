from __future__ import annotations

import subprocess

from tails_cloner.models import BootLoaderOrderOptions, PostWriteOptions
from tails_cloner.post_write import apply_post_write_options


def test_apply_post_write_skips_boot_order_when_disabled(tmp_path, monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    options = PostWriteOptions(enabled=True, sync_device=False, settle_seconds=0)
    apply_post_write_options(str(tmp_path / "dev"), options, command_runner=fake_run)

    assert calls == []


def test_apply_post_write_mounts_and_unmounts_for_boot_order(tmp_path, monkeypatch) -> None:
    mounted_at: dict[str, str] = {}
    commands: list[list[str]] = []

    def fake_apply(root, desired_order):
        mounted_at["root"] = str(root)
        mounted_at["desired"] = ",".join(desired_order)

        class _Result:
            files = []
            changed = False

        return _Result()

    def fake_run(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("tails_cloner.post_write.apply_boot_loader_order_to_directory", fake_apply)
    options = PostWriteOptions(
        enabled=True,
        sync_device=False,
        settle_seconds=0,
        boot_loader_order=BootLoaderOrderOptions(enabled=True, entries=["B", "A"]),
    )

    apply_post_write_options("/dev/sdb", options, command_runner=fake_run)

    assert commands[0][:3] == ["mount", "-o", "rw"]
    assert commands[0][3] == "/dev/sdb1"
    assert commands[-1][0] == "umount"
    assert mounted_at["desired"] == "B,A"


def test_apply_post_write_uses_nvme_partition_suffix(monkeypatch) -> None:
    commands: list[list[str]] = []

    def fake_apply(root, desired_order):
        class _Result:
            files = []
            changed = False

        return _Result()

    def fake_run(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("tails_cloner.post_write.apply_boot_loader_order_to_directory", fake_apply)
    options = PostWriteOptions(
        enabled=True,
        sync_device=False,
        settle_seconds=0,
        boot_loader_order=BootLoaderOrderOptions(enabled=True, entries=["Tails"]),
    )

    apply_post_write_options("/dev/nvme0n1", options, command_runner=fake_run)

    assert commands[0][3] == "/dev/nvme0n1p1"
