from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TRACKED_LIVE_BOOT_PATCH = ROOT / "patches/tails-live-boot-honor-fsuuid.patch"
TAILS_TREE = ROOT / "tails_issue_fix" / "tails"
LOCAL_LIVE_BOOT_PATCH = (
    TAILS_TREE
    / "config/chroot_local-patches/live-boot:_honor-FSUUID-when-finding-the-live-medium.patch"
)
RFC_ENVELOPE = ROOT / "tails_issue_fix/patches/RFC-live-boot-honor-FSUUID.patch"
GRUB_CONFIG = TAILS_TREE / "config/binary_local-includes/EFI/debian/grub.cfg"
SYSLINUX_HOOK = TAILS_TREE / "config/binary_local-hooks/10-syslinux_customize"


def _embedded_file_from_rfc(text: str) -> str:
    hunk_start = next(index for index, line in enumerate(text.splitlines()) if line.startswith("@@ -0,0 +1,"))
    payload = text.splitlines()[hunk_start + 1 :]
    return "\n".join(line[1:] for line in payload if line.startswith("+")) + "\n"


def test_tracked_patch_makes_fsuuid_authoritative() -> None:
    patch = TRACKED_LIVE_BOOT_PATCH.read_text(encoding="utf-8")

    assert "--- a/lib/live/boot/9990-misc-helpers.sh" in patch
    assert 'if [ ! -b "/dev/disk/by-uuid/${FSUUID}" ]' in patch
    assert 'check_dev "null" "${devname}" "skip_uuid_check"' in patch

    fsuuid_branch = patch.index('+\tif [ -n "${FSUUID:-}" ]')
    live_media_fallback = patch.index(' \tcase "${LIVE_MEDIA}" in')
    assert fsuuid_branch < live_media_fallback

    authoritative_block = patch[fsuuid_branch:live_media_fallback]
    assert "+\t\treturn 1" in authoritative_block
    assert "removable" not in authoritative_block


def test_local_tails_tree_installs_the_tracked_patch_when_available() -> None:
    if not LOCAL_LIVE_BOOT_PATCH.exists():
        pytest.skip("optional nested Tails checkout is not present")

    assert LOCAL_LIVE_BOOT_PATCH.read_bytes() == TRACKED_LIVE_BOOT_PATCH.read_bytes()


def test_local_rfc_envelope_embeds_the_exact_patch_when_available() -> None:
    if not RFC_ENVELOPE.exists():
        pytest.skip("optional FSUUID submission workspace is not present")

    expected = TRACKED_LIVE_BOOT_PATCH.read_text(encoding="utf-8")
    actual = _embedded_file_from_rfc(RFC_ENVELOPE.read_text(encoding="utf-8"))
    assert actual == expected


def test_local_tails_bootloaders_append_fsuuid_when_available() -> None:
    if not GRUB_CONFIG.exists() or not SYSLINUX_HOOK.exists():
        pytest.skip("optional nested Tails checkout is not present")

    grub = GRUB_CONFIG.read_text(encoding="utf-8")
    linux_lines = [line.strip() for line in grub.splitlines() if line.lstrip().startswith("linux ")]
    syslinux = SYSLINUX_HOOK.read_text(encoding="utf-8")

    assert "probe --set rootuuid --fs-uuid ($root)" in grub
    assert linux_lines
    assert all("FSUUID=${rootuuid}" in line for line in linux_lines)
    assert "sysappend 0x40000" in syslinux
