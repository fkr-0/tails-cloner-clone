from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tails_cloner.models import BootLoaderOrderOptions, PostWriteOptions
from tails_cloner.post_write import PostWriteFailure, apply_post_write_options


def test_partition_selector_rejects_automount_and_non_vfat(monkeypatch) -> None:
    from tails_cloner.post_write import _find_tails_boot_partition
    from tails_cloner.upgrader import PartitionInfo

    def partition(fstype: str, mounts: tuple[str, ...] = ()) -> PartitionInfo:
        return PartitionInfo("/dev/sdb2", fstype, "tails", "", 1024, False, mounts)

    monkeypatch.setattr("tails_cloner.post_write.find_tails_system_partition",
                        lambda _device: partition("vfat", ("/media/user/Tails",)))
    with pytest.raises(RuntimeError, match="already mounted"):
        _find_tails_boot_partition("/dev/sdb")
    monkeypatch.setattr("tails_cloner.post_write.find_tails_system_partition",
                        lambda _device: partition("ext4"))
    with pytest.raises(RuntimeError, match="non-VFAT"):
        _find_tails_boot_partition("/dev/sdb")


def test_settle_unavailable_and_flush_after_unmount(monkeypatch) -> None:
    calls: list[str] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        name = next((part for part in command if part in {"mount", "umount", "udevadm"}), "other")
        calls.append(name)
        if name == "udevadm":
            raise FileNotFoundError("udevadm unavailable")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    class Result:
        files: list[object] = []

    monkeypatch.setattr("tails_cloner.post_write._find_tails_boot_partition", lambda _device: "/dev/sdb2")
    monkeypatch.setattr("tails_cloner.post_write.build_privileged_command", lambda c: c)
    monkeypatch.setattr("tails_cloner.post_write.apply_boot_loader_order_to_directory",
                        lambda _root, _entries: Result())
    options = PostWriteOptions(enabled=True, sync_device=True, settle_seconds=0,
                               boot_loader_order=BootLoaderOrderOptions(enabled=True, entries=["Tails"]))
    apply_post_write_options("/dev/sdb", options, sync_runner=lambda: calls.append("sync"),
                             command_runner=fake_run)
    assert calls == ["sync", "udevadm", "mount", "umount", "sync"]


@pytest.mark.parametrize("failure", ["mount", "rewrite", "umount"])
def test_failure_records_audit_and_write_succeeded(monkeypatch, tmp_path: Path, failure: str) -> None:
    from tails_cloner.post_write import PostWriteFailure

    commands: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if failure in command:
            raise subprocess.CalledProcessError(32, command, stderr="injected failure")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    class Result:
        files: list[object] = []

    def fake_rewrite(_root: Path, _entries: list[str]) -> Result:
        if failure == "rewrite":
            raise ValueError("injected rewrite")
        return Result()

    monkeypatch.setattr("tails_cloner.post_write._find_tails_boot_partition", lambda _device: "/dev/sdb2")
    monkeypatch.setattr("tails_cloner.post_write.build_privileged_command", lambda c: c)
    monkeypatch.setattr("tails_cloner.post_write.apply_boot_loader_order_to_directory", fake_rewrite)
    options = PostWriteOptions(enabled=True, sync_device=False, settle_seconds=0,
                               log_file_path=str(tmp_path / "audit.log"),
                               boot_loader_order=BootLoaderOrderOptions(enabled=True, entries=["Tails"]))
    with pytest.raises(PostWriteFailure) as captured:
        apply_post_write_options("/dev/sdb", options, command_runner=fake_run)
    assert captured.value.write_succeeded
    assert "post_write_failed" in (tmp_path / "audit.log").read_text()
    assert "unmount_succeeded=" in (tmp_path / "audit.log").read_text()
    assert any("umount" in c for c in commands) == (failure != "mount")


def test_keyboard_interrupt_always_unmounts(monkeypatch) -> None:
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    def interrupt(_root: Path, _entries: list[str]) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("tails_cloner.post_write._find_tails_boot_partition", lambda _device: "/dev/sdb2")
    monkeypatch.setattr("tails_cloner.post_write.build_privileged_command", lambda c: c)
    monkeypatch.setattr("tails_cloner.post_write.apply_boot_loader_order_to_directory", interrupt)
    options = PostWriteOptions(enabled=True, sync_device=False, settle_seconds=0,
                               boot_loader_order=BootLoaderOrderOptions(enabled=True, entries=["Tails"]))
    with pytest.raises(PostWriteFailure) as caught:
        apply_post_write_options("/dev/sdb", options, command_runner=fake_run)
    assert caught.value.write_succeeded is True
    assert caught.value.device_state.startswith("unknown")
    assert any("umount" in c for c in commands)


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

    mount_command = next(c for c in commands if "mount" in c)
    assert mount_command[:4] == ["pkexec", "mount", "-t", "vfat"]
    assert "uid=" in mount_command[5]
    assert "gid=" in mount_command[5]
    assert "nosuid,nodev,noexec" in mount_command[5]
    assert mount_command[-2] == "/dev/sdb7"
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

    assert next(c for c in commands if "mount" in c)[-2] == "/dev/mapper/custom-tails"


def test_failed_unmount_never_deletes_files_under_the_mount_point(monkeypatch) -> None:
    """If umount fails the directory is still the live target filesystem: do not rmtree it."""
    import pytest

    seen: dict[str, Path] = {}

    class _Result:
        files: list[object] = []

    def fake_apply(root: Path, _desired_order: list[str]):
        seen["root"] = root
        (root / "syslinux.cfg").write_text("boot config on the mounted partition")
        return _Result()

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if "umount" in command:
            raise subprocess.CalledProcessError(32, command, stderr="target is busy")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("tails_cloner.post_write._find_tails_boot_partition", lambda _device: "/dev/sdb2")
    monkeypatch.setattr("tails_cloner.post_write.build_privileged_command", lambda command: ["pkexec", *command])
    monkeypatch.setattr("tails_cloner.post_write.apply_boot_loader_order_to_directory", fake_apply)
    options = PostWriteOptions(
        enabled=True,
        sync_device=False,
        settle_seconds=0,
        boot_loader_order=BootLoaderOrderOptions(enabled=True, entries=["Tails"]),
    )

    try:
        with pytest.raises(RuntimeError, match="post-write step failed"):
            apply_post_write_options("/dev/sdb", options, command_runner=fake_run)
        assert (seen["root"] / "syslinux.cfg").read_text() == "boot config on the mounted partition"
    finally:
        # Test hygiene only: the fake "mount" is an ordinary directory.
        (seen["root"] / "syslinux.cfg").unlink(missing_ok=True)
        seen["root"].rmdir()


def test_unmount_failure_does_not_mask_the_original_error(monkeypatch) -> None:
    import pytest

    seen: dict[str, Path] = {}

    def failing_apply(root: Path, _desired_order: list[str]):
        seen["root"] = root
        raise ValueError("unsupported boot configuration")

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if "umount" in command:
            raise subprocess.CalledProcessError(32, command, stderr="target is busy")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("tails_cloner.post_write._find_tails_boot_partition", lambda _device: "/dev/sdb2")
    monkeypatch.setattr("tails_cloner.post_write.build_privileged_command", lambda command: ["pkexec", *command])
    monkeypatch.setattr("tails_cloner.post_write.apply_boot_loader_order_to_directory", failing_apply)
    options = PostWriteOptions(
        enabled=True,
        sync_device=False,
        settle_seconds=0,
        boot_loader_order=BootLoaderOrderOptions(enabled=True, entries=["Tails"]),
    )
    messages: list[str] = []

    try:
        with pytest.raises(RuntimeError, match="post-write step failed"):
            apply_post_write_options("/dev/sdb", options, messages.append, command_runner=fake_run)
        assert any("could not be unmounted" in message for message in messages)
    finally:
        seen["root"].rmdir()
