from __future__ import annotations

import json
import subprocess
from pathlib import Path

from tails_cloner.upgrader import (
    build_partition_upgrade_command,
    find_partition,
    has_persistence_partition,
    upgrade_tails_system_partition,
    upgrade_tails_system_partition_from_device,
)


class FakeRunner:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        if command[:3] == ['lsblk', '-J', '-o']:
            device = command[-1]
            if device == '/dev/sdb':
                payload = {
                    'blockdevices': [
                        {
                            'children': [
                                {'path': '/dev/sdb1', 'fstype': 'vfat', 'label': 'TAILS'},
                                {'path': '/dev/sdb2', 'fstype': 'ext4', 'label': 'persistence'},
                            ]
                        }
                    ]
                }
            elif device == '/dev/loop7':
                payload = {
                    'blockdevices': [
                        {'children': [{'path': '/dev/loop7p1', 'fstype': 'vfat', 'label': 'TAILS'}]}
                    ]
                }
            elif device == '/dev/sdc':
                payload = {
                    'blockdevices': [
                        {'children': [{'path': '/dev/sdc1', 'fstype': 'vfat', 'label': 'TAILS'}]}
                    ]
                }
            else:
                payload = {'blockdevices': []}
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr='')
        if command[:3] == ['losetup', '--find', '--partscan']:
            return subprocess.CompletedProcess(command, 0, stdout='/dev/loop7\n', stderr='')
        return subprocess.CompletedProcess(command, 0, stdout='', stderr='')


def test_find_partition_returns_matching_partition() -> None:
    runner = FakeRunner()
    assert find_partition('/dev/sdb', fstype='vfat', runner=runner) == '/dev/sdb1'


def test_has_persistence_partition_detects_ext4_persistence() -> None:
    runner = FakeRunner()
    assert has_persistence_partition('/dev/sdb', runner=runner) is True


def test_build_partition_upgrade_command_is_partition_scoped() -> None:
    assert build_partition_upgrade_command('/dev/loop7p1', '/dev/sdb1') == [
        'dd',
        'if=/dev/loop7p1',
        'of=/dev/sdb1',
        'bs=4M',
        'status=progress',
        'conv=fsync',
    ]


def test_upgrade_tails_system_partition_uses_real_upgrader_path(tmp_path: Path) -> None:
    image = tmp_path / 'tails-amd64-7.7.2.img'
    image.write_bytes(b'image')
    runner = FakeRunner()
    progress: list[str] = []

    upgrade_tails_system_partition(image, '/dev/sdb', runner=runner, progress_callback=progress.append)

    assert ['dd', 'if=/dev/loop7p1', 'of=/dev/sdb1', 'bs=4M', 'status=progress', 'conv=fsync'] in runner.commands
    assert ['sync'] in runner.commands
    assert ['blockdev', '--flushbufs', '/dev/sdb'] in runner.commands
    assert ['losetup', '-d', '/dev/loop7'] in runner.commands
    assert any('persistence partition still present' in message for message in progress)


def test_upgrade_tails_system_partition_from_device_is_partition_scoped() -> None:
    runner = FakeRunner()
    progress: list[str] = []

    upgrade_tails_system_partition_from_device('/dev/sdc', '/dev/sdb', runner=runner, progress_callback=progress.append)

    assert ['dd', 'if=/dev/sdc1', 'of=/dev/sdb1', 'bs=4M', 'status=progress', 'conv=fsync'] in runner.commands
    assert ['sync'] in runner.commands
    assert ['blockdev', '--flushbufs', '/dev/sdb'] in runner.commands
    assert not any(command[:1] == ['losetup'] for command in runner.commands)
    assert any('Using source Tails system partition /dev/sdc1' in message for message in progress)
    assert any('persistence partition still present' in message for message in progress)


def test_upgrade_tails_system_partition_from_device_rejects_same_source_and_target() -> None:
    runner = FakeRunner()

    try:
        upgrade_tails_system_partition_from_device('/dev/sdb', '/dev/sdb', runner=runner)
    except RuntimeError as error:
        assert 'Source and target devices must be different' in str(error)
    else:
        raise AssertionError('expected same source/target upgrade to fail')
