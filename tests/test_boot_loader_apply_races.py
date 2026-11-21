"""Host-only adversarial file-apply fixtures (no mounts or block devices)."""

import os
from pathlib import Path

import pytest

from tails_cloner import boot_loader

GRUB = b"menuentry 'A' {\n linux /a\n}\nmenuentry 'B' {\n linux /b\n}\n"


def test_symlink_ancestor_refused(tmp_path: Path) -> None:
    actual = tmp_path / "actual"
    actual.mkdir()
    target = actual / "grub.cfg"
    target.write_bytes(GRUB)
    (tmp_path / "alias").symlink_to(actual, target_is_directory=True)
    with pytest.raises(OSError):
        boot_loader.apply_boot_loader_order_to_file(tmp_path / "alias" / "grub.cfg", ["B", "A"])
    assert target.read_bytes() == GRUB


@pytest.mark.parametrize("swap", ["bytes", "inode", "symlink"])
def test_intervening_source_change_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, swap: str) -> None:
    target = tmp_path / "grub.cfg"
    target.write_bytes(GRUB)
    outside = tmp_path / "outside"
    outside.write_bytes(b"outside")
    original = boot_loader._create_backup

    def race(path: Path, raw: bytes) -> Path:
        backup = original(path, raw)
        if swap == "bytes":
            path.write_bytes(b"new writer")
        else:
            path.unlink()
            if swap == "inode":
                path.write_bytes(GRUB)
            else:
                path.symlink_to(outside)
        return backup

    monkeypatch.setattr(boot_loader, "_create_backup", race)
    with pytest.raises((RuntimeError, OSError), match="conflict|symlink|link"):
        boot_loader.apply_boot_loader_order_to_file(target, ["B", "A"])
    assert outside.read_bytes() == b"outside"
    if swap == "bytes":
        assert target.read_bytes() == b"new writer"
    elif swap == "inode":
        assert target.read_bytes() == GRUB
    else:
        assert target.is_symlink()


def test_parent_swap_before_replace_does_not_write_outside(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    parent = tmp_path / "safe"
    parent.mkdir()
    target = parent / "grub.cfg"
    target.write_bytes(GRUB)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "grub.cfg").write_bytes(b"outside")
    original = boot_loader._create_backup

    def race(path: Path, raw: bytes) -> Path:
        backup = original(path, raw)
        parent.rename(tmp_path / "moved")
        parent.symlink_to(outside, target_is_directory=True)
        return backup

    monkeypatch.setattr(boot_loader, "_create_backup", race)
    with pytest.raises((OSError, RuntimeError)):
        boot_loader.apply_boot_loader_order_to_file(target, ["B", "A"])
    assert (outside / "grub.cfg").read_bytes() == b"outside"


def test_source_changed_while_replacement_is_staged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The last guard must recheck after staging, not just before backup."""
    target = tmp_path / "grub.cfg"
    target.write_bytes(GRUB)
    original_metadata = boot_loader._copy_file_metadata

    def intervening_writer(fd: int, source: object) -> None:
        original_metadata(fd, source)
        if os.readlink(f"/proc/self/fd/{fd}").endswith(".tmp"):
            target.write_bytes(b"concurrent edit during staging")

    monkeypatch.setattr(boot_loader, "_copy_file_metadata", intervening_writer)
    with pytest.raises(RuntimeError, match="conflict"):
        boot_loader.apply_boot_loader_order_to_file(target, ["B", "A"])
    assert target.read_bytes() == b"concurrent edit during staging"


def test_direct_source_snapshot_rejects_fifo_without_blocking(tmp_path: Path) -> None:
    """O_NONBLOCK and fstat must reject unusual leaves without opening a writer."""
    fifo = tmp_path / "grub.cfg"
    os.mkfifo(fifo)
    with pytest.raises(ValueError, match="non-regular"):
        boot_loader.apply_boot_loader_order_to_file(fifo, ["B", "A"])
