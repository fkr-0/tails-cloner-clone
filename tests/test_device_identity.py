"""Synthetic lsblk responses only; never access a real block device."""

import json
import subprocess

import pytest

from tails_cloner.device_identity import DeviceIdentityError, snapshot_disk, verify_partition


def fake_lsblk(*, swapped=False, wrong_parent=False, missing_id=False):
    def run(command, **_kwargs):
        path = command[-1]
        if path == '/dev/sdb':
            node = {'path': path, 'type': 'disk', 'maj:min': '8:16', 'size': 32000000000,
                    'serial': '' if missing_id else ('replacement' if swapped else 'serial-A'), 'wwn': ''}
        elif path == '/dev/sdb2':
            node = {'path': path, 'type': 'part', 'maj:min': '8:18',
                    'pkname': 'sdc' if wrong_parent else 'sdb'}
        elif path == '/dev/sdc':
            node = {'path': path, 'type': 'disk', 'maj:min': '8:32', 'size': 32000000000,
                    'serial': 'serial-A', 'wwn': ''}
        else:
            raise AssertionError(path)
        return subprocess.CompletedProcess(command, 0, json.dumps({'blockdevices': [node]}), '')
    return run


def test_matching_disk_and_partition():
    run = fake_lsblk()
    identity = snapshot_disk('/dev/sdb', run)
    verify_partition(identity, '/dev/sdb2', run)


@pytest.mark.parametrize('variant', ['swapped', 'wrong_parent', 'missing_id'])
def test_refuses_replacement_wrong_ancestry_or_unidentified_disk(variant):
    original = snapshot_disk('/dev/sdb', fake_lsblk())
    with pytest.raises(DeviceIdentityError):
        verify_partition(original, '/dev/sdb2', fake_lsblk(**{variant: True}))


def test_rejects_partition_as_disk_and_ambiguous_path():
    with pytest.raises(DeviceIdentityError):
        snapshot_disk('/dev/sdb2', fake_lsblk())
    with pytest.raises(DeviceIdentityError):
        snapshot_disk('/dev/../tmp', fake_lsblk())


def test_refuses_parent_reenumeration_even_with_matching_serial():
    original = snapshot_disk('/dev/sdb', fake_lsblk())
    with pytest.raises(DeviceIdentityError, match='parent identity'):
        verify_partition(original, '/dev/sdb2', fake_lsblk(wrong_parent=True))


def test_refuses_reconnect_during_partition_inspection():
    original = snapshot_disk('/dev/sdb', fake_lsblk())
    calls = 0

    def swapped_after_partition(command, **kwargs):
        nonlocal calls
        calls += 1
        return fake_lsblk(swapped=calls >= 4)(command, **kwargs)

    with pytest.raises(DeviceIdentityError):
        verify_partition(original, '/dev/sdb2', swapped_after_partition)


def test_refuses_injected_nonzero_lsblk_response():
    def failing(command, **_kwargs):
        return subprocess.CompletedProcess(command, 1, '{}', 'failed')

    with pytest.raises(DeviceIdentityError):
        snapshot_disk('/dev/sdb', failing)


def test_clone_refuses_replaced_disk_before_write(tmp_path, monkeypatch):
    from tails_cloner.creator import clone_image_to_device
    from tails_cloner.models import BootLoaderOrderOptions, PostWriteOptions
    from tails_cloner.post_write import PostWriteFailure

    image = tmp_path / 'image.iso'
    image.write_bytes(b'img')
    initial = snapshot_disk('/dev/sdb', fake_lsblk())
    inspected = []

    def probe(_path):
        inspected.append(True)
        return initial if len(inspected) == 1 else snapshot_disk('/dev/sdb', fake_lsblk(swapped=True))

    monkeypatch.setattr('tails_cloner.creator.snapshot_disk', probe)
    calls = []
    with pytest.raises(PostWriteFailure, match='changed after') as failure:
        clone_image_to_device(
            image, '/dev/sdb', run_command=lambda command, _callback: calls.append(command) or 0,
            target_preparer=lambda *_args: None,
            post_write_options=PostWriteOptions(enabled=True, sync_device=False,
                boot_loader_order=BootLoaderOrderOptions(enabled=True, entries=['Tails'])),
        )
    assert len(calls) == 3  # dd, partition reread, udev settle; no mount
    assert failure.value.write_succeeded
    assert not failure.value.customization_succeeded
    assert all('mount' not in command for command in calls)
