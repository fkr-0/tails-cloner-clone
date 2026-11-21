"""Host-only mount side-effect and mount ownership fault injections.

No command runner here invokes a subprocess or interacts with a block device.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tails_cloner.models import BootLoaderOrderOptions, PostWriteOptions
from tails_cloner.post_write import PostWriteFailure, apply_post_write_options


def _options() -> PostWriteOptions:
    return PostWriteOptions(
        enabled=True, sync_device=False, settle_seconds=0,
        boot_loader_order=BootLoaderOrderOptions(enabled=True, entries=["Tails"]),
    )


@pytest.fixture
def host_mount(monkeypatch):
    commands: list[str] = []
    source: dict[str, str | None] = {"value": None}
    roots: list[Path] = []

    monkeypatch.setattr("tails_cloner.post_write._find_tails_boot_partition", lambda _: "/dev/fake2")
    monkeypatch.setattr("tails_cloner.post_write._mount_source_at", lambda _: source["value"])
    monkeypatch.setattr("tails_cloner.post_write.build_privileged_command", lambda cmd: cmd)

    class Empty:
        files: list[object] = []

    def apply(root: Path, _entries: list[str]):
        roots.append(root)
        return Empty()

    monkeypatch.setattr("tails_cloner.post_write.apply_boot_loader_order_to_directory", apply)

    def run(command: list[str], **_kwargs):
        name = command[0]
        commands.append(name)
        if name == "mount":
            source["value"] = "/dev/fake2"
        if name == "umount":
            source["value"] = None
        return subprocess.CompletedProcess(command, 0, "", "")

    return commands, source, roots, run


@pytest.mark.parametrize("failure", ["raise", "nonzero"])
def test_mount_side_effect_then_failure_is_reclaimed(host_mount, failure):
    commands, source, _roots, base = host_mount

    def run(command, **kwargs):
        result = base(command, **kwargs)
        if command[0] == "mount":
            if failure == "raise":
                raise OSError("wrapper failed after mount succeeded")
            return subprocess.CompletedProcess(command, 32, "", "injected error")
        return result

    with pytest.raises(PostWriteFailure) as caught:
        apply_post_write_options("/dev/fake", _options(), command_runner=run)
    assert commands == ["udevadm", "mount", "umount"]
    assert source["value"] is None
    assert caught.value.write_succeeded is True
    assert caught.value.unmount_succeeded is True


def test_preexisting_or_automounted_scratch_target_is_never_unmounted(host_mount):
    commands, source, _roots, run = host_mount
    source["value"] = "/dev/foreign"
    with pytest.raises(PostWriteFailure) as caught:
        apply_post_write_options("/dev/fake", _options(), command_runner=run)
    assert commands == ["udevadm"]
    assert source["value"] == "/dev/foreign"
    assert caught.value.unmount_succeeded is None


def test_foreign_mount_appearing_during_mount_failure_is_not_unmounted(host_mount):
    commands, source, _roots, base = host_mount

    def run(command, **kwargs):
        if command[0] == "mount":
            commands.append("mount")
            source["value"] = "/dev/foreign"
            raise OSError("automount race")
        return base(command, **kwargs)

    with pytest.raises(PostWriteFailure) as caught:
        apply_post_write_options("/dev/fake", _options(), command_runner=run)
    assert commands == ["udevadm", "mount"]
    assert source["value"] == "/dev/foreign"
    assert caught.value.device_state.startswith("unknown")


def test_failed_unmount_with_failing_warning_callback_preserves_recovery(host_mount):
    commands, source, roots, base = host_mount

    def run(command, **kwargs):
        if command[0] == "umount":
            commands.append("umount")
            return subprocess.CompletedProcess(command, 32, "", "busy")
        return base(command, **kwargs)

    def callback(message: str):
        if "could not be unmounted" in message:
            raise RuntimeError("UI callback unavailable")

    try:
        with pytest.raises(PostWriteFailure) as caught:
            apply_post_write_options("/dev/fake", _options(), callback, command_runner=run)
        assert caught.value.unmount_succeeded is False
        assert caught.value.device_state.startswith("unknown")
        assert source["value"] == "/dev/fake2"
        assert commands[-1] == "umount"
    finally:
        # Only a simulated mount; the actual host directory is empty.
        for root in roots:
            root.rmdir()
