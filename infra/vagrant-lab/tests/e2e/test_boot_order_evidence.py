from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

LANE = Path(__file__).resolve().parents[2] / "real-boot-lane"
SPEC = importlib.util.spec_from_file_location("boot_order_evidence", LANE / "boot_order_evidence.py")
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def test_dry_run_does_not_modify_source(tmp_path: Path) -> None:
    boot = tmp_path / "source"
    boot.mkdir()
    config = boot / "grub.cfg"
    config.write_text("menuentry 'A' {\n linux /a\n}\nmenuentry 'B' {\n linux /b\n}\n")
    before = config.read_bytes()
    result = module.validate(boot, ["B", "A"], True)
    assert result["success"] is True
    assert result["layers"]["directory_copy"] == "dry_run"
    assert result["layers"]["qemu_boot"] == "not_verified"
    assert config.read_bytes() == before


def test_rewrite_copy_preserves_source_and_unrelated_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    boot = tmp_path / "source"
    boot.mkdir()
    config = boot / "grub.cfg"
    config.write_text("menuentry 'A' {\n linux /a\n}\n# separator\nmenuentry 'B' {\n linux /b\n}\n")
    unrelated = boot / "kernel.bin"
    unrelated.write_bytes(b"\x00\xff\x10")
    before = module.inventory(boot)
    monkeypatch.setenv("TAILS_CLONER_LAB_ALLOW_DESTRUCTIVE", "1")
    result = module.validate(boot, ["B", "A"], False)
    assert result["success"] is True
    assert result["rewrites"][0]["after"] == ["B", "A"]
    assert result["source_preserved"] and result["unrelated_bytes_preserved"]
    assert module.inventory(boot) == before
    assert result["layers"]["real_device_boot"] == "not_verified"


def test_execution_requires_explicit_guard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "grub.cfg"
    config.write_text("menuentry 'A' {}\nmenuentry 'B' {}\n")
    monkeypatch.delenv("TAILS_CLONER_LAB_ALLOW_DESTRUCTIVE", raising=False)
    with pytest.raises(PermissionError):
        module.validate(tmp_path, ["B", "A"], False)


def test_reject_symlink_tree(tmp_path: Path) -> None:
    original = tmp_path / "real"
    original.mkdir()
    (original / "grub.cfg").write_text("menuentry 'A' {}\n")
    (tmp_path / "link").symlink_to(original, target_is_directory=True)
    with pytest.raises(ValueError):
        module.validate(tmp_path / "link", ["A"], True)


def test_missing_requested_entry_cannot_be_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "grub.cfg").write_text("menuentry 'A' {}\nmenuentry 'B' {}\n")
    monkeypatch.setenv("TAILS_CLONER_LAB_ALLOW_DESTRUCTIVE", "1")
    result = module.validate(tmp_path, ["B", "A", "Missing"], False)
    assert result["success"] is False


@pytest.mark.parametrize("entries", [["B", "B"], ["B", ""], [" B", "A"]])
def test_reject_ambiguous_requested_entries(tmp_path: Path, entries: list[str]) -> None:
    (tmp_path / "grub.cfg").write_text("menuentry 'A' {}\nmenuentry 'B' {}\n")
    with pytest.raises(ValueError, match="requested entries"):
        module.validate(tmp_path, entries, True)


def test_unsupported_second_config_cannot_be_hidden_by_successful_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "grub.cfg").write_text("menuentry 'A' {}\nmenuentry 'B' {}\n")
    (tmp_path / "syslinux.cfg").write_text("DEFAULT linux\n")
    monkeypatch.setenv("TAILS_CLONER_LAB_ALLOW_DESTRUCTIVE", "1")
    result = module.validate(tmp_path, ["B", "A"], False)
    assert result["success"] is False
