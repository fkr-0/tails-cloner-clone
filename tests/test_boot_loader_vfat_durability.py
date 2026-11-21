"""Loopback-free host fixtures for FAT/vfat filesystem API failure modes.

These are injected failures, not empirical vfat or power-cut tests.
"""

import errno
import os
from pathlib import Path

import pytest

from tails_cloner import boot_loader


def _original(tmp_path: Path) -> Path:
    path = tmp_path / "grub.cfg"
    path.write_bytes(b"original\n")
    return path


def test_vfat_hardlink_unsupported_does_not_block_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _original(tmp_path)

    def no_hardlinks(*_args: object, **_kwargs: object) -> None:
        raise OSError(errno.EOPNOTSUPP, "hardlinks unsupported")

    monkeypatch.setattr(os, "link", no_hardlinks)
    backup = boot_loader._create_backup(path, path.read_bytes())
    assert backup.read_bytes() == b"original\n"
    assert path.read_bytes() == b"original\n"
    assert list(tmp_path.glob("*.bak")) == [backup]


def test_backup_no_clobber_collision_keeps_existing_bytes(tmp_path: Path) -> None:
    path = _original(tmp_path)
    occupied = tmp_path / "grub.cfg.tails-cloner.bak"
    occupied.write_bytes(b"other backup\n")
    backup = boot_loader._create_backup(path, path.read_bytes())
    assert occupied.read_bytes() == b"other backup\n"
    assert backup.name == "grub.cfg.tails-cloner.1.bak"
    assert backup.read_bytes() == b"original\n"


def test_no_clobber_rename_unsupported_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _original(tmp_path)

    def no_rename(*_args: object) -> None:
        raise OSError(errno.ENOSYS, "renameat2 unavailable")

    monkeypatch.setattr(boot_loader, "_rename_no_replace", no_rename)
    with pytest.raises(OSError) as caught:
        boot_loader._create_backup(path, path.read_bytes())
    assert caught.value.errno == errno.ENOSYS
    assert not list(tmp_path.glob("*.bak"))
    assert not list(tmp_path.glob(".*.backup.*"))


def test_backup_directory_sync_error_preserves_published_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _original(tmp_path)
    original_fsync = os.fsync

    def fail_directory_sync(fd: int) -> None:
        if os.path.isdir(f"/proc/self/fd/{fd}"):
            raise OSError(errno.EINVAL, "unsupported directory sync")
        original_fsync(fd)

    monkeypatch.setattr(os, "fsync", fail_directory_sync)
    with pytest.raises(boot_loader.BackupDurabilityError):
        boot_loader._create_backup(path, path.read_bytes())
    assert (tmp_path / "grub.cfg.tails-cloner.bak").read_bytes() == b"original\n"


@pytest.mark.parametrize("operation", ["fchmod", "fchown"])
def test_vfat_metadata_operation_rejection_allows_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    path = _original(tmp_path)
    original_stat = path.stat()
    def fail_metadata(*_args: object, **_kwargs: object) -> None:
        raise OSError(errno.EPERM, f"{operation} refused by mount")

    monkeypatch.setattr(os, operation, fail_metadata)
    if operation == "fchown":
        # Force the ownership correction branch without touching the real file.
        real_fstat = os.fstat

        def altered_fstat(fd: int) -> os.stat_result:
            result = list(real_fstat(fd))
            result[4] = original_stat.st_uid + 1
            return os.stat_result(result)

        monkeypatch.setattr(os, "fstat", altered_fstat)
    boot_loader._atomic_replace_bytes(path, b"replacement\n", original_stat)
    assert path.read_bytes() == b"replacement\n"


def test_directory_fsync_unsupported_after_rename_reports_failure_but_file_is_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _original(tmp_path)
    original_fsync = os.fsync

    def no_directory_sync(fd: int) -> None:
        if os.path.isdir(f"/proc/self/fd/{fd}"):
            raise OSError(errno.EINVAL, "directory fsync unsupported")
        original_fsync(fd)

    monkeypatch.setattr(os, "fsync", no_directory_sync)
    with pytest.raises(boot_loader.ReplacementDurabilityError) as caught:
        boot_loader._atomic_replace_bytes(path, b"replacement\n", path.stat())
    assert caught.value.errno == errno.EINVAL
    assert caught.value.replacement_applied
    assert path.read_bytes() == b"replacement\n"


def test_rename_failure_preserves_original_after_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _original(tmp_path)

    def fail_rename(*_args: object, **_kwargs: object) -> None:
        raise OSError(errno.EIO, "rename I/O error")

    monkeypatch.setattr(os, "replace", fail_rename)
    with pytest.raises(OSError, match="rename I/O error"):
        boot_loader._atomic_replace_bytes(path, b"replacement\n", path.stat())
    assert path.read_bytes() == b"original\n"
