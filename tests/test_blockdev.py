from __future__ import annotations

import subprocess

from tails_cloner.blockdev import (
    describe_command_failure,
    is_protected_mountpoint,
    mountpoints,
    protected_mountpoints,
    walk_nodes,
)


def test_walk_nodes_is_depth_first_and_skips_non_dict_children() -> None:
    tree = [{"name": "sda", "children": [{"name": "sda1", "children": [{"name": "crypt"}]}, "junk", {"name": "sda2"}]}]
    assert [node["name"] for node in walk_nodes(tree)] == ["sda", "sda1", "crypt", "sda2"]


def test_mountpoints_normalizes_lsblk_shapes() -> None:
    assert mountpoints(["/", None, "/home"]) == ["/", "/home"]
    assert mountpoints("/mnt") == ["/mnt"]
    assert mountpoints(None) == []


def test_protected_mountpoints_cover_system_and_live_medium() -> None:
    assert is_protected_mountpoint("/")
    assert is_protected_mountpoint("/lib/live/mount/medium")
    assert is_protected_mountpoint("/run/live/medium")
    assert not is_protected_mountpoint("/media/user/usb")
    assert not is_protected_mountpoint("/lib/live/mountain")
    assert protected_mountpoints(["/media/x", "/home", "/home", "/"]) == ["/", "/home"]


def test_describe_command_failure_includes_stderr_tail() -> None:
    error = subprocess.CalledProcessError(
        32, ["pkexec", "umount", "--", "/media/usb"], stderr="umount: /media/usb: target is busy.\n"
    )
    message = describe_command_failure(error)
    assert "pkexec umount -- /media/usb" in message
    assert "exit status 32" in message
    assert "target is busy" in message


def test_describe_command_failure_names_missing_program() -> None:
    error = FileNotFoundError(2, "No such file or directory", "pkexec")
    assert "pkexec" in describe_command_failure(error)


def test_parse_dd_bytes_reads_progress_lines_only() -> None:
    from tails_cloner.blockdev import parse_dd_bytes

    assert parse_dd_bytes("1048576 bytes (1.0 MB, 1.0 MiB) copied, 1 s, 1.0 MB/s") == 1048576
    assert parse_dd_bytes("4096 bytes transferred in 0.1 secs") == 4096
    assert parse_dd_bytes("Preparing target: umount -- /media/x") is None
