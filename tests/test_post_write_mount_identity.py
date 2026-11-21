"""Host-only mount-time identity regression fixtures; no block-device operations."""

import subprocess

import pytest

from tails_cloner.device_identity import DiskIdentity
from tails_cloner.models import BootLoaderOrderOptions, PostWriteOptions
from tails_cloner.post_write import PostWriteFailure, apply_post_write_options


@pytest.mark.parametrize("scenario", ["valid", "wrong_mount", "changed_parent", "changed_node", "source_swap"])
def test_mount_identity_is_checked_before_mutating(monkeypatch, scenario):
    import tails_cloner.post_write as module

    disk = DiskIdentity("/dev/fakedisk", "/dev/fakedisk", "8:0", "stable-serial", "", 4096)
    options = PostWriteOptions(enabled=True, sync_device=False, settle_seconds=0,
        boot_loader_order=BootLoaderOrderOptions(enabled=True, entries=["Tails"]))
    commands = []
    mutations = []
    mount = {"source": None, "number": "8:1", "checks": 0}
    monkeypatch.setattr(module, "_find_tails_boot_partition", lambda _: "/dev/fakepart")
    monkeypatch.setattr(module, "build_privileged_command", lambda cmd: cmd)
    monkeypatch.setattr(module, "_mount_source_at", lambda _: mount["source"])

    def mount_number(_):
        mount["checks"] += 1
        if scenario == "source_swap" and mount["checks"] > 1:
            return "8:3"
        return "8:2" if scenario == "wrong_mount" else mount["number"]

    monkeypatch.setattr(module, "_mount_major_minor_at", mount_number)
    monkeypatch.setattr(module, "_partition_major_minor", lambda _: "8:9" if scenario == "changed_node" else "8:1")

    def verify(*_args):
        if scenario == "changed_parent" and mount["source"] is not None:
            raise RuntimeError("parent changed after mount")

    monkeypatch.setattr(module, "verify_partition", verify)
    monkeypatch.setattr("tails_cloner.device_identity.snapshot_disk", lambda *_args: disk)

    class Empty:
        files = []

    def apply(*_args):
        mutations.append(True)
        return Empty()

    monkeypatch.setattr(module, "apply_boot_loader_order_to_directory", apply)

    def run(cmd, **_kwargs):
        commands.append(cmd[0])
        if cmd[0] == "mount":
            mount["source"] = "/dev/fakepart"
        elif cmd[0] == "umount":
            mount["source"] = None
        return subprocess.CompletedProcess(cmd, 0, "", "")

    if scenario == "valid":
        apply_post_write_options("/dev/fakedisk", options, command_runner=run, expected_disk=disk)
        assert mutations == [True]
    else:
        with pytest.raises(PostWriteFailure):
            apply_post_write_options("/dev/fakedisk", options, command_runner=run, expected_disk=disk)
        assert not mutations
    if scenario in {"wrong_mount", "changed_node"}:
        assert commands[-1] == "mount"  # Foreign mount ownership is uncertain.
    else:
        assert commands[-1] == "umount"
