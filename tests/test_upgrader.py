from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tails_cloner.upgrader import (
    attach_image,
    build_partition_upgrade_command,
    build_privileged_command,
    find_partition,
    find_tails_system_partition,
    has_persistence_partition,
    upgrade_tails_system_partition,
    upgrade_tails_system_partition_from_device,
)

GIB = 1024**3


def _part(
    path: str,
    *,
    fstype: str,
    label: str = "",
    partlabel: str = "",
    size: int = 8 * GIB,
    read_only: bool = False,
    mountpoints: list[str | None] | None = None,
    node_type: str = "part",
) -> dict[str, object]:
    return {
        "path": path,
        "type": node_type,
        "fstype": fstype,
        "label": label,
        "partlabel": partlabel,
        "size": size,
        "ro": read_only,
        "mountpoints": mountpoints or [None],
    }


class FakeRunner:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    def _parts_for(self, device: str) -> list[dict[str, object]]:
        if device == "/dev/sdb":
            return [
                _part("/dev/sdb1", fstype="vfat", label="EFI", size=64 * 1024**2),
                _part(
                    "/dev/sdb2",
                    fstype="vfat",
                    label="Tails",
                    partlabel="Tails",
                    mountpoints=["/media/target-tails"],
                ),
                _part(
                    "/dev/sdb3",
                    fstype="crypto_LUKS",
                    partlabel="TailsData",
                    size=24 * GIB,
                ),
            ]
        if device == "/dev/loop7":
            return [_part("/dev/loop7p1", fstype="vfat", label="TAILS", size=7 * GIB)]
        if device == "/dev/sdc":
            return [_part("/dev/sdc1", fstype="vfat", label="Tails", size=7 * GIB)]
        if device == "/dev/sdd":
            return [_part("/dev/sdd1", fstype="vfat", label="Tails", size=8 * GIB)]
        if device == "/dev/sde":
            return [_part("/dev/sde1", fstype="vfat", label="Tails", size=9 * GIB)]
        if device == "/dev/sdf":
            return [_part("/dev/sdf1", fstype="vfat", label="Tails", size=8 * GIB)]
        if device == "/dev/sdg":
            return [_part("/dev/sdg1", fstype="vfat", label="Tails", read_only=True)]
        if device == "/dev/sdh":
            return [
                _part(
                    "/dev/sdh1",
                    fstype="vfat",
                    label="Tails",
                    mountpoints=["/lib/live/mount/medium"],
                )
            ]
        return []

    def __call__(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        if command[:4] == ["lsblk", "--json", "--bytes", "--output"]:
            device = command[-1]
            payload = {"blockdevices": [{"path": device, "type": "disk", "children": self._parts_for(device)}]}
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")
        if command[:5] == ["losetup", "--read-only", "--find", "--partscan", "--show"]:
            return subprocess.CompletedProcess(command, 0, stdout="/dev/loop7\n", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")


class MutationRunner(FakeRunner):
    pass


class SettleFailRunner(FakeRunner):
    def __call__(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        if command == ["udevadm", "settle"]:
            self.commands.append(command)
            raise subprocess.CalledProcessError(1, command, stderr="udev queue busy")
        return super().__call__(command)


class SettleNonzeroRunner(FakeRunner):
    def __call__(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        if command == ["udevadm", "settle"]:
            self.commands.append(command)
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="udev queue busy")
        return super().__call__(command)


class DelayedLoopPartitionRunner(FakeRunner):
    def __init__(self, empty_probes: int) -> None:
        super().__init__()
        self.empty_probes = empty_probes
        self.loop_probes = 0

    def _parts_for(self, device: str) -> list[dict[str, object]]:
        if device == "/dev/loop7":
            self.loop_probes += 1
            if self.loop_probes <= self.empty_probes:
                return []
        return super()._parts_for(device)


def test_find_partition_matches_label_case_insensitively() -> None:
    runner = FakeRunner()

    assert find_partition("/dev/sdb", fstype="vfat", label="TAILS", runner=runner) == "/dev/sdb2"


def test_find_tails_partition_skips_unrelated_vfat_partition() -> None:
    runner = FakeRunner()

    assert find_tails_system_partition("/dev/sdb", runner).path == "/dev/sdb2"


def test_has_persistence_partition_detects_tailsdata_partlabel() -> None:
    runner = FakeRunner()

    assert has_persistence_partition("/dev/sdb", runner=runner) is True
    assert has_persistence_partition("/dev/sdd", runner=runner) is False


def test_build_privileged_command_uses_pkexec_only_when_needed() -> None:
    command = ["dd", "if=source", "of=target"]

    assert build_privileged_command(command, effective_uid=1000) == ["pkexec", *command]
    assert build_privileged_command(command, effective_uid=0) == command


def test_build_partition_upgrade_command_is_partition_scoped() -> None:
    assert build_partition_upgrade_command("/dev/loop7p1", "/dev/sdb2") == [
        "dd",
        "if=/dev/loop7p1",
        "of=/dev/sdb2",
        "bs=4M",
        "status=progress",
        "conv=fsync",
    ]


def test_attach_image_tolerates_immediate_udevadm_settle_failure_when_partition_is_visible() -> None:
    reader = SettleFailRunner()
    mutator = MutationRunner()

    loopdev = attach_image(
        "/tmp/tails.img",
        reader,
        mutator,
        partition_wait_timeout_seconds=0.1,
        partition_poll_interval_seconds=0.01,
        sleep=lambda _seconds: None,
    )

    assert loopdev == "/dev/loop7"
    assert ["udevadm", "settle"] in reader.commands
    assert any(command[-1] == "/dev/loop7" for command in reader.commands if command[:1] == ["lsblk"])
    assert ["losetup", "--detach", "/dev/loop7"] not in mutator.commands


def test_attach_image_tolerates_nonzero_settle_result_when_runner_does_not_raise() -> None:
    reader = SettleNonzeroRunner()
    mutator = MutationRunner()

    assert attach_image("/tmp/tails.img", reader, mutator, sleep=lambda _seconds: None) == "/dev/loop7"
    assert ["udevadm", "settle"] in reader.commands


def test_attach_image_retries_until_loop_partition_becomes_visible() -> None:
    reader = DelayedLoopPartitionRunner(empty_probes=2)
    mutator = MutationRunner()
    sleeps: list[float] = []

    loopdev = attach_image(
        "/tmp/tails.img",
        reader,
        mutator,
        partition_wait_timeout_seconds=0.1,
        partition_poll_interval_seconds=0.01,
        sleep=sleeps.append,
    )

    assert loopdev == "/dev/loop7"
    assert reader.loop_probes == 3
    assert sleeps == [0.01, 0.01]


def test_attach_image_detaches_loop_after_bounded_partition_visibility_failure() -> None:
    reader = DelayedLoopPartitionRunner(empty_probes=999)
    mutator = MutationRunner()

    with pytest.raises(RuntimeError, match="did not become visible within 0.02s"):
        attach_image(
            "/tmp/tails.img",
            reader,
            mutator,
            partition_wait_timeout_seconds=0.02,
            partition_poll_interval_seconds=0.01,
            sleep=lambda _seconds: None,
        )

    assert reader.loop_probes == 3
    assert ["losetup", "--detach", "/dev/loop7"] in mutator.commands


def test_image_upgrade_uses_read_only_loop_and_privileged_mutation_boundary(tmp_path: Path) -> None:
    image = tmp_path / "tails-amd64-7.7.2.img"
    image.write_bytes(b"image")
    reader = FakeRunner()
    mutator = MutationRunner()
    progress: list[str] = []

    upgrade_tails_system_partition(
        image,
        "/dev/sdb",
        runner=reader,
        privileged_runner=mutator,
        progress_callback=progress.append,
    )

    assert ["losetup", "--read-only", "--find", "--partscan", "--show", str(image)] in mutator.commands
    assert ["umount", "--", "/media/target-tails"] in mutator.commands
    assert ["dd", "if=/dev/loop7p1", "of=/dev/sdb2", "bs=4M", "status=progress", "conv=fsync"] in mutator.commands
    assert ["blockdev", "--flushbufs", "/dev/sdb"] in mutator.commands
    assert ["losetup", "--detach", "/dev/loop7"] in mutator.commands
    assert ["sync"] in reader.commands
    assert ["udevadm", "settle"] in reader.commands
    assert not any(command[0] in {"dd", "losetup", "umount", "blockdev"} for command in reader.commands)
    assert any("Persistent Storage is still present" in message for message in progress)


def test_image_upgrade_does_not_fail_after_successful_write_when_udev_settle_returns_one(tmp_path: Path) -> None:
    image = tmp_path / "tails-amd64-7.7.2.img"
    image.write_bytes(b"image")
    reader = SettleFailRunner()
    mutator = MutationRunner()

    upgrade_tails_system_partition(image, "/dev/sdb", runner=reader, privileged_runner=mutator)

    assert ["dd", "if=/dev/loop7p1", "of=/dev/sdb2", "bs=4M", "status=progress", "conv=fsync"] in mutator.commands
    assert reader.commands.count(["udevadm", "settle"]) == 2


def test_upgrade_from_device_is_partition_scoped_without_loop_setup() -> None:
    runner = FakeRunner()
    progress: list[str] = []

    upgrade_tails_system_partition_from_device(
        "/dev/sdc",
        "/dev/sdb",
        runner=runner,
        progress_callback=progress.append,
    )

    assert ["dd", "if=/dev/sdc1", "of=/dev/sdb2", "bs=4M", "status=progress", "conv=fsync"] in runner.commands
    assert not any(command[:1] == ["losetup"] for command in runner.commands)
    assert any("Using source Tails system partition /dev/sdc1" in message for message in progress)


def test_upgrade_allows_target_without_persistence() -> None:
    runner = FakeRunner()
    progress: list[str] = []

    upgrade_tails_system_partition_from_device(
        "/dev/sdc",
        "/dev/sdd",
        runner=runner,
        progress_callback=progress.append,
    )

    assert ["dd", "if=/dev/sdc1", "of=/dev/sdd1", "bs=4M", "status=progress", "conv=fsync"] in runner.commands
    assert any("had no Persistent Storage" in message for message in progress)


def test_upgrade_rejects_source_partition_larger_than_target() -> None:
    runner = FakeRunner()

    with pytest.raises(RuntimeError, match="larger than the target"):
        upgrade_tails_system_partition_from_device("/dev/sde", "/dev/sdf", runner=runner)

    assert not any(command[:1] == ["dd"] for command in runner.commands)


def test_upgrade_rejects_read_only_target_partition() -> None:
    runner = FakeRunner()

    with pytest.raises(RuntimeError, match="read-only"):
        upgrade_tails_system_partition_from_device("/dev/sdc", "/dev/sdg", runner=runner)


def test_upgrade_rejects_target_partition_used_by_running_live_system() -> None:
    runner = FakeRunner()

    with pytest.raises(RuntimeError, match="currently running operating system"):
        upgrade_tails_system_partition_from_device("/dev/sdc", "/dev/sdh", runner=runner)

    assert not any(command[:1] in (["umount"], ["dd"]) for command in runner.commands)


def test_upgrade_from_device_rejects_same_source_and_target() -> None:
    runner = FakeRunner()

    with pytest.raises(RuntimeError, match="Source and target devices must be different"):
        upgrade_tails_system_partition_from_device("/dev/sdb", "/dev/sdb", runner=runner)
