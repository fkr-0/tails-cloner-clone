from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TAILS_TREE = ROOT / "tails_issue_fix" / "tails"
LIVE_BOOT_PATCH = (
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


def test_tails_tree_installs_authoritative_fsuuid_live_boot_patch() -> None:
    patch = LIVE_BOOT_PATCH.read_text(encoding="utf-8")

    assert "--- a/lib/live/boot/9990-misc-helpers.sh" in patch
    assert 'if [ ! -b "/dev/disk/by-uuid/${FSUUID}" ]' in patch
    assert 'check_dev "null" "${devname}" "skip_uuid_check"' in patch

    fsuuid_branch = patch.index('+\tif [ -n "${FSUUID:-}" ]')
    live_media_fallback = patch.index(' \tcase "${LIVE_MEDIA}" in')
    assert fsuuid_branch < live_media_fallback

    authoritative_block = patch[fsuuid_branch:live_media_fallback]
    assert "+\t\treturn 1" in authoritative_block
    assert "removable" not in authoritative_block


def test_rfc_envelope_embeds_the_exact_tails_patch() -> None:
    expected = LIVE_BOOT_PATCH.read_text(encoding="utf-8")
    actual = _embedded_file_from_rfc(RFC_ENVELOPE.read_text(encoding="utf-8"))

    assert actual == expected


def test_grub_appends_the_boot_filesystem_uuid_to_every_kernel_entry() -> None:
    config = GRUB_CONFIG.read_text(encoding="utf-8")
    linux_lines = [line.strip() for line in config.splitlines() if line.lstrip().startswith("linux ")]

    assert "probe --set rootuuid --fs-uuid ($root)" in config
    assert linux_lines
    assert all("FSUUID=${rootuuid}" in line for line in linux_lines)


def test_syslinux_requests_the_filesystem_uuid_append_bit() -> None:
    hook = SYSLINUX_HOOK.read_text(encoding="utf-8")

    assert "sysappend 0x40000" in hook
