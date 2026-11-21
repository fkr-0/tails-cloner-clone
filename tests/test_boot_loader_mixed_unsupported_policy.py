"""Host-only regression fixture for atomic boot-order policy."""

import subprocess
from pathlib import Path

import pytest

from tails_cloner.boot_loader import (
    UnsupportedBootLoaderConfigError,
    apply_boot_loader_order_to_directory,
)
from tails_cloner.models import BootLoaderOrderOptions, PostWriteOptions
from tails_cloner.post_write import PostWriteFailure, apply_post_write_options

GRUB = b"menuentry 'A' {\n linux /a\n}\nmenuentry 'B' {\n linux /b\n}\n"
SYSLINUX = b"label a\n menu label A\n kernel /a\nlabel b\n menu label B\n kernel /b\n"


@pytest.mark.parametrize(
    "unsupported",
    [GRUB + b"# opaque \xff\n", GRUB.replace(b"\n", b"\r\n", 1), GRUB.replace(b"'A'", b"'$A'")],
    ids=["non-utf8", "mixed-newlines", "dynamic-title"],
)
@pytest.mark.parametrize("invalid_first", [False, True], ids=["valid-first", "invalid-first"])
def test_unsupported_match_aborts_before_all_writes(
    tmp_path: Path, unsupported: bytes, invalid_first: bool
) -> None:
    a, b = tmp_path / "a" / "grub.cfg", tmp_path / "b" / "grub.cfg"
    a.parent.mkdir()
    b.parent.mkdir()
    valid, invalid = (b, a) if invalid_first else (a, b)
    valid.write_bytes(GRUB)
    invalid.write_bytes(unsupported)
    with pytest.raises(UnsupportedBootLoaderConfigError) as caught:
        apply_boot_loader_order_to_directory(tmp_path, ["B", "A"])
    assert caught.value.path == invalid
    assert caught.value.reason
    # Inspect files even when the expected exception is absent.
    assert valid.read_bytes() == GRUB
    assert invalid.read_bytes() == unsupported
    assert not list(tmp_path.rglob("*.bak"))


@pytest.mark.parametrize("family,valid_name,invalid_name,valid,unsupported", [
    ("bios", "syslinux.cfg", "live.cfg", SYSLINUX, SYSLINUX + b"# opaque \xff\n"),
    ("efi", "grub.cfg", "loopback.cfg", GRUB, GRUB.replace(b"'A'", b"'$A'")),
])
def test_bios_efi_preflight_rejects_every_unsupported_candidate(
    tmp_path: Path, family: str, valid_name: str, invalid_name: str,
    valid: bytes, unsupported: bytes,
) -> None:
    first = tmp_path / "a" / valid_name
    second = tmp_path / "b" / invalid_name
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_bytes(valid)
    second.write_bytes(unsupported)
    with pytest.raises(UnsupportedBootLoaderConfigError) as caught:
        apply_boot_loader_order_to_directory(tmp_path, ["B", "A"])
    assert caught.value.path == second, family
    assert first.read_bytes() == valid
    assert second.read_bytes() == unsupported
    assert not list(tmp_path.rglob("*.bak"))


def test_unsupported_preflight_crosses_post_write_boundary_after_unmount(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    calls: list[str] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if "mount" in command:
            calls.append("mount")
            mount_root = Path(command[-1])
            (mount_root / "grub.cfg").write_bytes(GRUB)
            (mount_root / "loopback.cfg").write_bytes(GRUB + b"\xff")
        elif "umount" in command:
            calls.append("umount")
            mount_root = Path(command[-1])
            assert (mount_root / "grub.cfg").read_bytes() == GRUB
            assert not list(mount_root.glob("*.bak"))
            (mount_root / "grub.cfg").unlink()
            (mount_root / "loopback.cfg").unlink()
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("tails_cloner.post_write._find_tails_boot_partition", lambda _: "/dev/fake2")
    monkeypatch.setattr("tails_cloner.post_write.build_privileged_command", lambda command: command)
    audit = tmp_path / "audit.log"
    options = PostWriteOptions(
        enabled=True, sync_device=False, settle_seconds=0, log_file_path=str(audit),
        boot_loader_order=BootLoaderOrderOptions(enabled=True, entries=["B", "A"]),
    )
    with pytest.raises(PostWriteFailure) as caught:
        apply_post_write_options("/dev/fake", options, command_runner=fake_run)
    assert isinstance(caught.value.__cause__, UnsupportedBootLoaderConfigError)
    assert caught.value.write_succeeded is True
    assert caught.value.customization_succeeded is False
    assert caught.value.unmount_succeeded is True
    assert calls == ["mount", "umount"]
    assert "post_write_failed" in audit.read_text()
    assert "post_write_complete" not in audit.read_text()
