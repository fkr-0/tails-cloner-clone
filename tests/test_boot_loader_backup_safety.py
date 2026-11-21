"""Host-only backup publication/rollback fault-injection tests."""

from __future__ import annotations

import errno
import os
import stat
from pathlib import Path

import pytest

from tails_cloner import boot_loader

GRUB = b"menuentry 'A' {\n linux /a\n}\nmenuentry 'B' {\n linux /b\n}\n"


def config_at(root: Path) -> Path:
    path = root / "grub.cfg"
    path.write_bytes(GRUB)
    path.chmod(0o600)
    return path


def test_interrupted_backup_not_published(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = config_at(tmp_path)
    real_fsync = os.fsync
    visible: list[Path] = []

    def fail_backup_sync(fd: int) -> None:
        if stat.S_ISREG(os.fstat(fd).st_mode) and target.read_bytes() == GRUB:
            visible.extend(tmp_path.glob("grub.cfg.tails-cloner*.bak"))
            raise KeyboardInterrupt("backup interrupted")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fail_backup_sync)
    with pytest.raises(KeyboardInterrupt, match="backup interrupted"):
        boot_loader.apply_boot_loader_order_to_file(target, ["B", "A"])
    assert visible == [], "unfinished backup visible under final name"
    assert target.read_bytes() == GRUB


def test_backup_restrictive_at_all_sync_boundaries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = config_at(tmp_path)
    real_fsync = os.fsync
    inspected = []

    def inspect_sync(fd: int) -> None:
        for path in tmp_path.iterdir():
            if path.name.startswith("grub.cfg.tails-cloner"):
                inspected.append(path)
                assert stat.S_IMODE(path.stat().st_mode) & 0o077 == 0
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", inspect_sync)
    result = boot_loader.apply_boot_loader_order_to_file(target, ["B", "A"])
    assert result.backup_path is not None
    assert stat.S_IMODE(result.backup_path.stat().st_mode) == 0o600
    assert inspected


def test_poisoned_backup_rejected_before_restore(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = config_at(tmp_path)
    real_atomic = boot_loader._atomic_replace_bytes
    calls = 0

    def poisoned(path: Path, data: bytes, source: os.stat_result) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            real_atomic(path, b"bad replacement", source)
            backups = list(tmp_path.glob("grub.cfg.tails-cloner*.bak"))
            assert len(backups) == 1
            backups[0].write_bytes(b"poisoned")
        else:
            real_atomic(path, data, source)

    monkeypatch.setattr(boot_loader, "_atomic_replace_bytes", poisoned)
    with pytest.raises(ExceptionGroup, match="rollback"):
        boot_loader.apply_boot_loader_order_to_file(target, ["B", "A"])
    assert calls == 1
    assert target.read_bytes() != b"poisoned"


def test_rollback_does_not_overwrite_concurrent_update(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = config_at(tmp_path)
    real_atomic = boot_loader._atomic_replace_bytes
    calls = 0

    def racing(path: Path, data: bytes, source: os.stat_result) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            real_atomic(path, b"invalid menu", source)
            path.write_bytes(b"concurrent edit")
        else:
            real_atomic(path, data, source)

    monkeypatch.setattr(boot_loader, "_atomic_replace_bytes", racing)
    with pytest.raises(ExceptionGroup, match="rollback"):
        boot_loader.apply_boot_loader_order_to_file(target, ["B", "A"])
    assert target.read_bytes() == b"concurrent edit"
    assert calls == 1


def test_parent_fsync_eio_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = config_at(tmp_path)
    real_fsync = os.fsync

    def fail_dir(fd: int) -> None:
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError(errno.EIO, "directory fsync failed")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fail_dir)
    with pytest.raises(OSError, match="durability uncertain|directory fsync failed"):
        boot_loader.apply_boot_loader_order_to_file(target, ["B", "A"])
