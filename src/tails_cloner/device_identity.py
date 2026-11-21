"""Strict post-write identity checks; never infer identity from a /dev name alone."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


class DeviceIdentityError(RuntimeError):
    pass


Runner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class DiskIdentity:
    path: str
    kernel_path: str
    major_minor: str
    serial: str
    wwn: str
    size: int


def _node(path: str, run: Runner = subprocess.run) -> dict[str, Any]:
    if not path.startswith('/dev/') or '..' in path.split('/'):
        raise DeviceIdentityError('Invalid device path')
    try:
        result = run(['lsblk', '-J', '-b', '-o', 'PATH,TYPE,PKNAME,MAJ:MIN,SERIAL,WWN,SIZE', path], check=True, text=True, capture_output=True)
        nodes = json.loads(result.stdout)['blockdevices']
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as exc:
        raise DeviceIdentityError('Unable to inspect block device') from exc
    if (not isinstance(nodes, list) or len(nodes) != 1
            or not isinstance(nodes[0], dict)):
        raise DeviceIdentityError('Ambiguous block device')
    if result.returncode != 0:
        raise DeviceIdentityError('Block device inspection failed')
    return nodes[0]


def snapshot_disk(path: str, run: Runner = subprocess.run) -> DiskIdentity:
    node = _node(path, run)
    mm = str(node.get('maj:min') or '')
    if node.get('type') != 'disk' or mm.count(':') != 1 or not mm.replace(':', '').isdigit():
        raise DeviceIdentityError('Expected disk with major:minor')
    try:
        size = int(node['size'])
    except (KeyError, TypeError, ValueError) as exc:
        raise DeviceIdentityError('Invalid size') from exc
    if size <= 0:
        raise DeviceIdentityError('Invalid size')
    kernel_path = str(node.get('path') or '')
    if not kernel_path.startswith('/dev/') or '..' in kernel_path.split('/'):
        raise DeviceIdentityError('Invalid resolved disk path')
    return DiskIdentity(path, kernel_path, mm, str(node.get('serial') or '').strip(), str(node.get('wwn') or '').strip(), size)


def verify_partition(disk: DiskIdentity, partition: str, run: Runner = subprocess.run) -> None:
    if not (disk.serial or disk.wwn):
        raise DeviceIdentityError('Missing stable hardware identity')
    if snapshot_disk(disk.path, run) != disk:
        raise DeviceIdentityError('Disk identity changed')
    node = _node(partition, run)
    mm = str(node.get('maj:min') or '')
    parent_name = node.get('pkname')
    if (node.get('type') != 'part' or not isinstance(parent_name, str)
            or not parent_name or '/' in parent_name or parent_name in {'.', '..'}
            or mm == disk.major_minor or mm.count(':') != 1
            or not mm.replace(':', '').isdigit()):
        raise DeviceIdentityError('Partition ancestry mismatch')
    # PKNAME is a name, not an identity; independently inspect its parent disk.
    parent = snapshot_disk('/dev/' + parent_name, run)
    if (parent.major_minor != disk.major_minor or parent.kernel_path != disk.kernel_path
            or parent.serial != disk.serial or parent.wwn != disk.wwn
            or parent.size != disk.size):
        raise DeviceIdentityError('Partition parent identity mismatch')
    if snapshot_disk(disk.path, run) != disk:
        raise DeviceIdentityError('Disk identity changed during verification')
