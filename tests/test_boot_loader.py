from __future__ import annotations

from pathlib import Path

from tails_cloner.boot_loader import (
    apply_boot_loader_order_to_directory,
    apply_boot_loader_order_to_file,
    parse_boot_loader_entries_from_text,
    reorder_entries,
    rewrite_boot_loader_config_text,
    unique_entries,
)


def test_parse_grub_menu_entries() -> None:
    text = """
menuentry 'Tails' {
}
menuentry "Tails Troubleshooting Mode" {
}
"""
    assert parse_boot_loader_entries_from_text(text) == ["Tails", "Tails Troubleshooting Mode"]


def test_parse_syslinux_menu_labels() -> None:
    text = """
label live
  menu label Tails
label failsafe
  menu label Tails Troubleshooting Mode
"""
    assert parse_boot_loader_entries_from_text(text) == ["Tails", "Tails Troubleshooting Mode"]


def test_unique_entries_cleans_and_deduplicates() -> None:
    assert unique_entries([" ^Tails ", "Tails", "", "Tails Admin"]) == ["Tails", "Tails Admin"]


def test_reorder_entries_preserves_unknown_tail_entries() -> None:
    assert reorder_entries(["A", "B", "C"], ["C", "A"]) == ["C", "A", "B"]


def test_rewrite_grub_menuentry_blocks() -> None:
    text = """set timeout=5
menuentry 'A' {
  linux /live/vmlinuz a
}
menuentry 'B' {
  linux /live/vmlinuz b
}
"""
    rewritten, result = rewrite_boot_loader_config_text(text, ["B", "A"])

    assert result.changed is True
    assert result.entries_before == ["A", "B"]
    assert result.entries_after == ["B", "A"]
    assert rewritten.index("menuentry 'B'") < rewritten.index("menuentry 'A'")
    assert rewritten.startswith("set timeout=5")


def test_rewrite_syslinux_label_blocks() -> None:
    text = """default live
label live
  menu label A
  kernel /live/vmlinuz
label failsafe
  menu label B
  kernel /live/vmlinuz
"""
    rewritten, result = rewrite_boot_loader_config_text(text, ["B", "A"])

    assert result.changed is True
    assert result.entries_after == ["B", "A"]
    assert rewritten.index("menu label B") < rewritten.index("menu label A")
    assert rewritten.startswith("default live")


def test_apply_boot_loader_order_to_file_creates_backup(tmp_path: Path) -> None:
    config = tmp_path / "grub.cfg"
    config.write_text("menuentry 'A' {\n}\nmenuentry 'B' {\n}\n", encoding="utf-8")

    result = apply_boot_loader_order_to_file(config, ["B", "A"])

    assert result.changed is True
    assert result.backup_path is not None
    assert result.backup_path.exists()
    assert parse_boot_loader_entries_from_text(config.read_text(encoding="utf-8")) == ["B", "A"]
    assert parse_boot_loader_entries_from_text(result.backup_path.read_text(encoding="utf-8")) == ["A", "B"]


def test_apply_boot_loader_order_to_directory_updates_supported_configs(tmp_path: Path) -> None:
    boot = tmp_path / "EFI" / "BOOT"
    boot.mkdir(parents=True)
    config = boot / "grub.cfg"
    config.write_text("menuentry 'A' {\n}\nmenuentry 'B' {\n}\n", encoding="utf-8")

    result = apply_boot_loader_order_to_directory(tmp_path, ["B", "A"])

    assert result.changed is True
    assert result.changed_paths == [config]
    assert parse_boot_loader_entries_from_text(config.read_text(encoding="utf-8")) == ["B", "A"]
