"""E2E-006: synthetic, read-only block ancestry collision fixtures."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from test_upgrader import FakeRunner, independent_identity_probe

from tails_cloner.upgrader import (
    _assert_independent_media,
    upgrade_tails_system_partition_from_device,
)


def _node(major_minor: str, *parents: dict[str, object]) -> dict[str, object]:
    return {"path": "/dev/fixture", "maj:min": major_minor, "type": "part", "children": list(parents)}


class Probe:
    def __init__(self, graph: dict[str, dict[str, object]], backing: str = "/dev/host1") -> None:
        self.graph = graph
        self.backing = backing
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(argv)
        if argv[0] == "findmnt":
            return subprocess.CompletedProcess(argv, 0, stdout=self.backing + "\n", stderr="")
        if argv[-1] not in self.graph:
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="unknown fixture")
        return subprocess.CompletedProcess(
            argv, 0, stdout=json.dumps({"blockdevices": [self.graph[argv[-1]]]}), stderr=""
        )


@pytest.mark.parametrize(
    "source,target",
    [
        ("/dev/disk/by-id/usb-A-part1", "/dev/sda2"),
        ("/dev/mapper/live", "/dev/sda2"),
        ("/dev/loop7p1", "/dev/sda2"),
    ],
)
def test_alias_dm_loop_share_underlying_disk(source: str, target: str) -> None:
    graph = {
        source: _node("7:1", _node("8:0")),
        target: _node("8:2", _node("8:0")),
    }
    probe = Probe(graph)
    with pytest.raises(RuntimeError, match="share underlying block media"):
        _assert_independent_media(source, target, probe)
    assert not any(call[0] == "dd" for call in probe.calls)


def test_image_stored_on_target_is_refused() -> None:
    probe = Probe(
        {
            "/dev/loop7p1": _node("7:1", _node("7:0")),
            "/dev/sda2": _node("8:2", _node("8:0")),
            "/dev/sda1": _node("8:1", _node("8:0")),
        },
        backing="/dev/sda1",
    )
    with pytest.raises(RuntimeError, match="Source image resides on target"):
        _assert_independent_media(
            "/dev/loop7p1", "/dev/sda2", probe, source_image=Path("/media/target/tails.img")
        )


def test_distinct_media_permitted_by_fixture() -> None:
    probe = Probe({"/dev/sdb1": _node("8:17", _node("8:16")), "/dev/sda2": _node("8:2", _node("8:0"))})
    _assert_independent_media("/dev/sdb1", "/dev/sda2", probe)


def test_unknown_or_incomplete_ancestry_fails_closed() -> None:
    probe = Probe({"/dev/sdb1": _node("", _node("8:16")), "/dev/sda2": _node("8:2")})
    with pytest.raises(RuntimeError, match="Incomplete block ancestry"):
        _assert_independent_media("/dev/sdb1", "/dev/sda2", probe)


def test_unknown_image_backing_fails_closed() -> None:
    probe = Probe(
        {"/dev/loop7p1": _node("7:1"), "/dev/sda2": _node("8:2")},
        backing="overlay",
    )
    with pytest.raises(RuntimeError, match="Cannot verify image backing block device"):
        _assert_independent_media(
            "/dev/loop7p1", "/dev/sda2", probe, source_image=Path("/images/tails.img")
        )


def test_injected_direct_writer_without_identity_probe_never_copies() -> None:
    runner = FakeRunner()
    with pytest.raises(RuntimeError, match="Cannot verify source-target identity"):
        upgrade_tails_system_partition_from_device("/dev/sdc", "/dev/sdb", runner=runner)
    assert not any(call[0] == "dd" for call in runner.commands)


def test_topology_remap_after_unmount_refuses_before_dd() -> None:
    runner = FakeRunner()
    checks = 0

    def remapping_probe(argv: list[str]) -> subprocess.CompletedProcess[str]:
        nonlocal checks
        if argv[0] == "lsblk" and argv[-1] == "/dev/sdc1":
            checks += 1
            if checks == 2:
                payload = {"blockdevices": [_node("8:33", _node("8:16"))]}
                return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")
        return independent_identity_probe(argv)

    with pytest.raises(RuntimeError, match="share underlying block media"):
        upgrade_tails_system_partition_from_device(
            "/dev/sdc", "/dev/sdb", runner=runner, identity_runner=remapping_probe
        )
    assert checks == 2
    assert not any(call[0] == "dd" for call in runner.commands)
