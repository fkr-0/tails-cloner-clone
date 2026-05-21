from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "infra" / "vagrant-lab" / "real-boot-lane" / "prepare_tails_debug_boot_image.py"

spec = importlib.util.spec_from_file_location("prepare_tails_debug_boot_image", SCRIPT)
assert spec and spec.loader
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def test_patch_kernel_args_removes_quiet_splash_noautologin() -> None:
    line = "\tappend initrd=/live/initrd.img boot=live quiet splash noautologin"

    patched = builder.patch_kernel_args(line)

    assert "quiet" not in patched
    assert "splash" not in patched
    assert "noautologin" not in patched
    assert "console=ttyS0,115200n8" in patched
    assert "systemd.log_target=console" in patched
    assert "loglevel=7" in patched


def test_patch_kernel_args_accepts_extra_lab_args() -> None:
    line = "\tappend initrd=/live/initrd.img boot=live"

    patched = builder.patch_kernel_args(line, extra_kernel_args=["tailsclonerlab=test"])

    assert "tailsclonerlab=test" in patched


def test_patch_text_patches_bios_and_efi_kernel_lines_only() -> None:
    text = "menu title Tails\n\tappend initrd=/live/initrd.img boot=live quiet\n\tlinux /live/vmlinuz boot=live splash\n"

    patched = builder.patch_text(text, extra_kernel_args=["tailsclonerlab=test"])

    assert "menu title Tails" in patched
    assert patched.count("tailsclonerlab=test") == 2
    assert " quiet" not in patched
    assert " splash" not in patched
