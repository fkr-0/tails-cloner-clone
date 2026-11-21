"""No-mount Syslinux path parity tests using ordinary temporary files only."""

from pathlib import Path

from tails_cloner.boot_loader import apply_boot_loader_order_to_directory
from tails_cloner.image_probe import probe_boot_loader_entries


def test_live64_nested_include_target_matches_preview_and_post_write(tmp_path: Path) -> None:
    root = tmp_path / "extracted"
    syslinux = root / "syslinux"
    syslinux.mkdir(parents=True)
    # The selector has no menu blocks: its INCLUDE target carries the entries.
    selector = syslinux / "syslinux.cfg"
    selector.write_text("DEFAULT vesamenu.c32\nINCLUDE live64.cfg\n", encoding="utf-8")
    config = syslinux / "live64.cfg"
    original = (
        "LABEL live\n MENU LABEL Tails (normal)\n KERNEL /live/vmlinuz\n"
        "LABEL troubleshooting\n MENU LABEL Tails (Troubleshooting Mode)\n"
        " KERNEL /live/vmlinuz\n"
    )
    config.write_text(original, encoding="utf-8")

    preview = probe_boot_loader_entries(root)
    assert [(entry.config_path, entry.title) for entry in preview.entries] == [
        ("syslinux/live64.cfg", "Tails (normal)"),
        ("syslinux/live64.cfg", "Tails (Troubleshooting Mode)"),
    ]

    result = apply_boot_loader_order_to_directory(root, [
        "Tails (Troubleshooting Mode)", "Tails (normal)",
    ])
    assert result.changed_paths == [config]
    assert selector.read_text(encoding="utf-8") == "DEFAULT vesamenu.c32\nINCLUDE live64.cfg\n"
    assert config.read_text(encoding="utf-8").index("LABEL troubleshooting") < config.read_text(encoding="utf-8").index("LABEL live")
    assert config.with_name("live64.cfg.tails-cloner.bak").read_text(encoding="utf-8") == original


def test_live64_outside_known_config_tree_is_not_rewritten(tmp_path: Path) -> None:
    root = tmp_path / "extracted"
    root.mkdir()
    ignored = root / "unrelated.cfg"
    ignored.write_text("LABEL a\n MENU LABEL A\nLABEL b\n MENU LABEL B\n", encoding="utf-8")
    result = apply_boot_loader_order_to_directory(root, ["B", "A"])
    assert not result.changed
    assert list(root.iterdir()) == [ignored]


def test_live64_two_level_include_chain_keeps_selectors_intact(tmp_path: Path) -> None:
    """Only the config containing MENU LABEL blocks is a rewrite target."""
    syslinux = tmp_path / "syslinux"
    syslinux.mkdir()
    selector = syslinux / "syslinux.cfg"
    selector.write_text("DEFAULT menu.c32\nINCLUDE main.cfg\n", encoding="utf-8")
    middle = syslinux / "main.cfg"
    middle.write_text("MENU TITLE Tails\nINCLUDE live64.cfg\n", encoding="utf-8")
    leaf = syslinux / "live64.cfg"
    leaf.write_text(
        "LABEL live\n MENU LABEL Normal\n KERNEL /live/vmlinuz\n"
        "LABEL debug\n MENU LABEL Troubleshooting\n KERNEL /live/vmlinuz\n",
        encoding="utf-8",
    )

    entries = probe_boot_loader_entries(tmp_path).entries
    assert [(item.title, item.config_path) for item in entries] == [
        ("Normal", "syslinux/live64.cfg"),
        ("Troubleshooting", "syslinux/live64.cfg"),
    ]
    outcome = apply_boot_loader_order_to_directory(tmp_path, ["Troubleshooting", "Normal"])
    assert outcome.changed_paths == [leaf]
    assert selector.read_text(encoding="utf-8") == "DEFAULT menu.c32\nINCLUDE main.cfg\n"
    assert middle.read_text(encoding="utf-8") == "MENU TITLE Tails\nINCLUDE live64.cfg\n"
