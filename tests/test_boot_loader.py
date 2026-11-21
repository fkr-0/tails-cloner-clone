from __future__ import annotations

import random
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


def test_rewrite_refuses_duplicate_titles_without_dropping_entries() -> None:
    text = "menuentry 'A' {\n linux /first\n}\nmenuentry 'B' {\n}\nmenuentry 'A' {\n linux /second\n}\n"
    rewritten, result = rewrite_boot_loader_config_text(text, ["B", "A"])
    assert rewritten == text
    assert not result.changed
    assert "duplicate" in result.unsupported_reason


def test_rewrite_preserves_inter_entry_commands_by_refusing_unsafe_order() -> None:
    text = "menuentry 'A' {\n}\nset root=hd1\nmenuentry 'B' {\n}\n"
    rewritten, result = rewrite_boot_loader_config_text(text, ["B", "A"])
    assert rewritten == text
    assert not result.changed
    assert result.unsupported_reason


def test_rewrite_preserves_comments_between_entries() -> None:
    text = "menuentry 'A' {\n}\n# keep this comment\n\nmenuentry 'B' {\n}\n"
    rewritten, result = rewrite_boot_loader_config_text(text, ["B", "A"])
    assert result.changed
    assert rewritten == "menuentry 'B' {\n}\n# keep this comment\n\nmenuentry 'A' {\n}\n"


def test_rewrite_refuses_incomplete_grub_menu() -> None:
    text = "menuentry 'A' {\n}\nmenuentry 'B' {\n}\nmenuentry 'C' {\n"
    rewritten, result = rewrite_boot_loader_config_text(text, ["B", "A"])
    assert rewritten == text
    assert not result.changed
    assert result.unsupported_reason


def test_rewrite_ignores_braces_in_grub_comments() -> None:
    text = "menuentry 'A' {\n # } ignored\n linux /a\n}\nmenuentry 'B' {\n # { ignored\n linux /b\n}\n"
    rewritten, result = rewrite_boot_loader_config_text(text, ["B", "A"])
    assert result.changed
    assert rewritten == "menuentry 'B' {\n # { ignored\n linux /b\n}\nmenuentry 'A' {\n # } ignored\n linux /a\n}\n"


def test_rewrite_refuses_overlapping_grub_blocks() -> None:
    text = "menuentry 'A' {\n menuentry 'B' {\n }\n}\n"
    rewritten, result = rewrite_boot_loader_config_text(text, ["B", "A"])
    assert rewritten == text
    assert not result.changed
    assert result.unsupported_reason


def test_rewrite_refuses_duplicate_syslinux_titles() -> None:
    text = "label a\n menu label Same\nlabel b\n menu label Other\nlabel c\n menu label Same\n"
    rewritten, result = rewrite_boot_loader_config_text(text, ["Other", "Same"])
    assert rewritten == text
    assert "duplicate" in result.unsupported_reason


def test_rewrite_refuses_to_move_entries_across_submenus() -> None:
    text = "submenu 'One' {\n menuentry 'A' {\n }\n}\nsubmenu 'Two' {\n menuentry 'B' {\n }\n}\n"
    rewritten, result = rewrite_boot_loader_config_text(text, ["B", "A"])
    assert rewritten == text
    assert result.unsupported_reason


def test_rewrite_handles_missing_final_newline() -> None:
    text = "menuentry 'A' {\n}\nmenuentry 'B' {\n}"
    rewritten, result = rewrite_boot_loader_config_text(text, ["B", "A"])
    assert result.changed
    assert rewritten == "menuentry 'B' {\n}\nmenuentry 'A' {\n}"


def test_unsupported_file_stays_untouched_without_backup(tmp_path: Path) -> None:
    config = tmp_path / "grub.cfg"
    original = b"menuentry 'A' {\n}\nset root=hd1\nmenuentry 'B' {\n}\n"
    config.write_bytes(original)
    result = apply_boot_loader_order_to_file(config, ["B", "A"])
    assert not result.changed
    assert result.unsupported_reason
    assert config.read_bytes() == original
    assert list(tmp_path.iterdir()) == [config]


def test_syslinux_global_directives_are_not_moved() -> None:
    for directive in ("default other", "timeout 50", "prompt 1", "menu title Boot", "include extra.cfg", "say Hello", "ui vesamenu.c32"):
        for placement in ("middle", "end"):
            lines = ["label a\n menu label A\n kernel /a\n", "label b\n menu label B\n kernel /b\n"]
            text = lines[0] + (directive + "\n" if placement == "middle" else "") + lines[1] + (directive + "\n" if placement == "end" else "")
            rewritten, result = rewrite_boot_loader_config_text(text, ["B", "A"])
            assert rewritten == text, (directive, placement)
            assert not result.changed
            assert result.unsupported_reason


def test_grub_unsafe_scopes_are_refused() -> None:
    for prefix in ("submenu 'Other' {\n", "if true; then\n", "function boot {\n", "source /boot/extra.cfg\n"):
        text = prefix + "menuentry 'A' {\n}\nmenuentry 'B' {\n}\n"
        rewritten, result = rewrite_boot_loader_config_text(text, ["B", "A"])
        assert rewritten == text
        assert result.unsupported_reason, prefix


def test_grub_dynamic_titles_are_refused() -> None:
    for title in ("$title", "$" + "{title}"):
        text = f"menuentry '{title}' {{\n}}\nmenuentry 'B' {{\n}}\n"
        rewritten, result = rewrite_boot_loader_config_text(text, ["B", "A"])
        assert rewritten == text
        assert result.unsupported_reason, title


def test_binary_and_nonstandard_headers_fail_closed() -> None:
    text = "menuentry 'A' {\n}\nmenuentry 'B' {\n}\n"
    for value in ("\ufeff" + text, text + "\x00", text.replace("\n", "\r\n", 1)):
        rewritten, result = rewrite_boot_loader_config_text(value, ["B", "A"])
        assert rewritten == value
        assert result.unsupported_reason


def test_syslinux_rejects_unrecognized_label_headers() -> None:
    text = "label a\n menu label A\nlabel malformed extra\n kernel /bad\nlabel b\n menu label B\n"
    rewritten, result = rewrite_boot_loader_config_text(text, ["B", "A"])
    assert rewritten == text
    assert result.unsupported_reason


def test_seeded_rewrite_invariants() -> None:
    rng = random.Random(20261121)
    for _ in range(80):
        names = rng.sample("ABCDEFGH", rng.randint(2, 8))
        order = rng.sample(names, len(names))
        text = "set timeout=5\n" + "".join(f"menuentry '{name}' {{\n linux /{name}\n}}\n# slot\n" for name in names)
        rewritten, result = rewrite_boot_loader_config_text(text, order)
        assert result.entries_after == parse_boot_loader_entries_from_text(rewritten)
        assert rewrite_boot_loader_config_text(rewritten, order)[0] == rewritten
        for name in names:
            assert rewritten.count(f"menuentry '{name}' {{\n linux /{name}\n}}") == 1
        assert rewritten.count("# slot\n") == len(names)
        assert rewritten.startswith("set timeout=5\n")


def test_apply_order_is_idempotent_and_keeps_original_backup(tmp_path: Path) -> None:
    config = tmp_path / "grub.cfg"
    original = b"menuentry 'A' {\n}\n# comment\nmenuentry 'B' {\n}\n"
    config.write_bytes(original)
    first = apply_boot_loader_order_to_file(config, ["B", "A"])
    assert first.backup_path is not None
    assert first.backup_path.read_bytes() == original
    second = apply_boot_loader_order_to_file(config, ["B", "A"])
    assert not second.changed
    assert second.backup_path is None
    assert len(list(tmp_path.iterdir())) == 2
