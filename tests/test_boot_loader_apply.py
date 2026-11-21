"""File-backed boot menu safety tests; never touch real devices."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from tails_cloner import boot_loader

GRUB = b"set timeout=5\nmenuentry 'A' {\n linux /a\n}\n# keep slot\nmenuentry 'B' {\n linux /b\n}\n"


def test_crlf_round_trip_keeps_exact_line_endings(tmp_path: Path) -> None:
    config = tmp_path / "grub.cfg"
    original = GRUB.replace(b"\n", b"\r\n")
    config.write_bytes(original)
    result = boot_loader.apply_boot_loader_order_to_file(config, ["B", "A"])
    assert result.changed
    assert config.read_bytes().replace(b"\r\n", b"\n").count(b"\n") == GRUB.count(b"\n")
    assert b"\n" not in config.read_bytes().replace(b"\r\n", b"")
    assert result.backup_path is not None
    assert result.backup_path.read_bytes() == original


def test_invalid_utf8_is_refused_without_loss(tmp_path: Path) -> None:
    config = tmp_path / "grub.cfg"
    original = GRUB + b"# opaque \xff\n"
    config.write_bytes(original)
    result = boot_loader.apply_boot_loader_order_to_file(config, ["B", "A"])
    assert not result.changed and result.unsupported_reason
    assert config.read_bytes() == original
    assert len(list(tmp_path.iterdir())) == 1


def test_mixed_line_endings_fail_closed(tmp_path: Path) -> None:
    config = tmp_path / "grub.cfg"
    original = GRUB.replace(b"\n", b"\r\n", 1)
    config.write_bytes(original)
    result = boot_loader.apply_boot_loader_order_to_file(config, ["B", "A"])
    assert not result.changed and "newline" in result.unsupported_reason
    assert config.read_bytes() == original


def test_rewrite_shape_rejects_mutated_non_menu_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "grub.cfg"
    config.write_bytes(GRUB)
    real_rewrite = boot_loader.rewrite_boot_loader_config_text

    def corrupt_rewrite(text: str, order: list[str]) -> tuple[str, boot_loader.BootLoaderRewriteResult]:
        changed, result = real_rewrite(text, order)
        return changed.replace("timeout=5", "timeout=0"), result

    monkeypatch.setattr(boot_loader, "rewrite_boot_loader_config_text", corrupt_rewrite)
    with pytest.raises(RuntimeError, match="verification failed before writing"):
        boot_loader.apply_boot_loader_order_to_file(config, ["B", "A"])
    assert config.read_bytes() == GRUB
    assert len(list(tmp_path.iterdir())) == 1


def test_atomic_replace_failure_preserves_original(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "grub.cfg"
    config.write_bytes(GRUB)
    original_replace = os.replace

    def fail_replace(source: str | os.PathLike[str], dest: str | os.PathLike[str]) -> None:
        if Path(dest) == config:
            raise OSError("simulated replace failure")
        original_replace(source, dest)

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated replace failure"):
        boot_loader.apply_boot_loader_order_to_file(config, ["B", "A"])
    assert config.read_bytes() == GRUB
    assert not any(p.name.startswith(".grub.cfg.") for p in tmp_path.iterdir())


def test_verify_failure_restores_original(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "grub.cfg"
    config.write_bytes(GRUB)
    original = boot_loader._atomic_replace_bytes
    calls = 0

    def corrupt_once(path: Path, data: bytes, source_stat: os.stat_result) -> None:
        nonlocal calls
        calls += 1
        original(path, b"broken" if calls == 1 else data, source_stat)

    monkeypatch.setattr(boot_loader, "_atomic_replace_bytes", corrupt_once)
    with pytest.raises(RuntimeError, match="verification"):
        boot_loader.apply_boot_loader_order_to_file(config, ["B", "A"])
    assert config.read_bytes() == GRUB
    assert calls == 2


def test_restore_failure_surfaces_both_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "grub.cfg"
    config.write_bytes(GRUB)
    original = boot_loader._atomic_replace_bytes
    calls = 0

    def corrupt_then_fail(path: Path, _data: bytes, source_stat: os.stat_result) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("restore denied")
        original(path, b"broken", source_stat)

    monkeypatch.setattr(boot_loader, "_atomic_replace_bytes", corrupt_then_fail)
    with pytest.raises(ExceptionGroup, match="rollback") as caught:
        boot_loader.apply_boot_loader_order_to_file(config, ["B", "A"])
    assert "restore denied" in str(caught.value.exceptions[-1])
    assert any(p.name.endswith("tails-cloner.bak") for p in tmp_path.iterdir())


def test_directory_failure_rolls_back_earlier_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first, second = tmp_path / "a" / "grub.cfg", tmp_path / "b" / "grub.cfg"
    for path in (first, second):
        path.parent.mkdir()
        path.write_bytes(GRUB)
    original = boot_loader._atomic_replace_bytes

    def fail_second(path: Path, data: bytes, source_stat: os.stat_result) -> None:
        if path == second:
            raise OSError("second write denied")
        original(path, data, source_stat)

    monkeypatch.setattr(boot_loader, "_atomic_replace_bytes", fail_second)
    with pytest.raises(OSError, match="second write denied"):
        boot_loader.apply_boot_loader_order_to_directory(tmp_path, ["B", "A"])
    assert first.read_bytes() == second.read_bytes() == GRUB


def test_directory_refuses_outside_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "boot"
    root.mkdir()
    outside = tmp_path / "grub.cfg"
    outside.write_bytes(GRUB)
    (root / "grub.cfg").symlink_to(outside)
    result = boot_loader.apply_boot_loader_order_to_directory(root, ["B", "A"])
    assert not result.changed
    assert outside.read_bytes() == GRUB


def test_modes_and_backup_collisions(tmp_path: Path) -> None:
    config = tmp_path / "grub.cfg"
    config.write_bytes(GRUB)
    config.chmod(0o640)
    collision = tmp_path / "grub.cfg.tails-cloner.bak"
    collision.write_bytes(b"existing backup")
    result = boot_loader.apply_boot_loader_order_to_file(config, ["B", "A"])
    assert result.backup_path == tmp_path / "grub.cfg.tails-cloner.1.bak"
    assert collision.read_bytes() == b"existing backup"
    assert stat.S_IMODE(config.stat().st_mode) == 0o640
    assert config.stat().st_uid == result.backup_path.stat().st_uid
