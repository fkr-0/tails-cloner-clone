"""Bounded synthetic GPT/hybrid regular-file fixtures; no devices or mounts."""
from __future__ import annotations

import struct
import zlib
from hashlib import sha256
from pathlib import Path

import pytest

from tails_cloner.image_probe import (
    EFI_SYSTEM_GUID,
    MICROSOFT_BASIC_GUID,
    DiscoveryError,
    probe_boot_loader_entries,
)


def _image(*, hybrid: bool = False, syslinux: bool = False) -> bytearray:
    sectors = 128
    disk = bytearray(sectors * 512)
    # A 64-sector FAT12 system partition at LBA 40.
    base = 40 * 512
    boot = memoryview(disk)[base:base + 512]
    boot[:3] = b"\xeb\x3c\x90"
    boot[11:13] = struct.pack("<H", 512)
    boot[13] = 1
    boot[14:16] = struct.pack("<H", 1)
    boot[16] = 1
    boot[17:19] = struct.pack("<H", 16)
    boot[19:21] = struct.pack("<H", 64)
    boot[22:24] = struct.pack("<H", 1)
    boot[510:512] = b"\x55\xaa"
    disk[base + 512:base + 517] = b"\xf8\xff\xff\xff\x0f"
    config = (b"label live\n menu label Tails fixture\n" if syslinux
              else b"menuentry 'GPT Tails fixture' {\n}\n")
    disk[base + 1024:base + 1035] = b"LIVE64  CFG" if syslinux else b"GRUB    CFG"
    disk[base + 1035] = 0x20
    disk[base + 1050:base + 1052] = struct.pack("<H", 2)
    disk[base + 1052:base + 1056] = struct.pack("<I", len(config))
    disk[base + 1536:base + 1536 + len(config)] = config
    # Primary and backup GPT arrays, each four 128-byte entries.
    table = bytearray(512)
    table[:16] = EFI_SYSTEM_GUID
    table[16:32] = bytes(range(16))
    struct.pack_into("<QQ", table, 32, 40, 103)
    disk[2 * 512:3 * 512] = table
    disk[126 * 512:127 * 512] = table
    table_crc = zlib.crc32(table)
    for lba, other, array_lba in ((1, 127, 2), (127, 1, 126)):
        header = bytearray(512)
        header[:8] = b"EFI PART"
        struct.pack_into("<III", header, 8, 0x10000, 92, 0)
        struct.pack_into("<QQQQ", header, 24, lba, other, 34, 125)
        header[56:72] = bytes(range(16))
        struct.pack_into("<QIII", header, 72, array_lba, 4, 128, table_crc)
        struct.pack_into("<I", header, 16, zlib.crc32(header[:92]))
        disk[lba * 512:(lba + 1) * 512] = header
    disk[510:512] = b"\x55\xaa"
    disk[450] = 0xee
    struct.pack_into("<II", disk, 454, 1, 127)
    if hybrid:
        disk[466] = 0x0c
        struct.pack_into("<II", disk, 470, 40, 64)
    return disk


@pytest.mark.parametrize("hybrid", [False, True])
def test_gpt_fat_menu_discovery(tmp_path: Path, hybrid: bool) -> None:
    path = tmp_path / "synthetic.img"
    path.write_bytes(_image(hybrid=hybrid))
    result = probe_boot_loader_entries(path)
    assert result.format == "fat-img"
    assert [(entry.title, entry.config_path) for entry in result.entries] == [
        ("GPT Tails fixture", "partition1/grub.cfg")
    ]


def test_gpt_tails_syslinux_config_filename(tmp_path: Path) -> None:
    path = tmp_path / "tails-like.img"
    path.write_bytes(_image(syslinux=True))
    result = probe_boot_loader_entries(path)
    assert any(entry.config_path == "partition1/live64.cfg" for entry in result.entries)


def _with_basic_data(disk: bytearray, signature: bytes) -> bytearray:
    """Add GPT Basic Data at LBA 108..119 and repair both GPT CRC copies."""
    for lba in (2, 126):
        offset = lba * 512 + 128
        disk[offset:offset + 16] = MICROSOFT_BASIC_GUID
        disk[offset + 16:offset + 32] = bytes(range(16, 32))
        struct.pack_into("<QQ", disk, offset + 32, 108, 119)
    base = 108 * 512
    disk[base:base + 3] = b"\xeb\x52\x90"
    disk[base + 3:base + 11] = signature
    disk[base + 510:base + 512] = b"\x55\xaa"
    table_crc = zlib.crc32(disk[2 * 512:3 * 512])
    for lba in (1, 127):
        offset = lba * 512
        struct.pack_into("<I", disk, offset + 88, table_crc)
        struct.pack_into("<I", disk, offset + 16, 0)
        struct.pack_into("<I", disk, offset + 16, zlib.crc32(disk[offset:offset + 92]))
    return disk


@pytest.mark.parametrize(("signature", "expected_sha256"), [
    (b"NTFS    ", "49b8dd462348ecf6cb56ff6807bab26481c92b6116c54507ff4565706b11070a"),
    (b"EXFAT   ", "721e35be3dfbb79d09c97666a8e7d8dbd3bf1ee4843caddb7e888016bdb43e64"),
])
def test_gpt_efi_fat_and_nonfat_basic_data(
    tmp_path: Path, signature: bytes, expected_sha256: str,
) -> None:
    disk = _with_basic_data(_image(), signature)
    assert sha256(disk).hexdigest() == expected_sha256
    path = tmp_path / "efi-with-data.img"
    path.write_bytes(disk)
    result = probe_boot_loader_entries(path)
    assert result.format == "fat-img"
    assert [(entry.title, entry.config_path) for entry in result.entries] == [
        ("GPT Tails fixture", "partition1/grub.cfg")
    ]


@pytest.mark.parametrize("mutation", ["unknown_fs", "invalid_nonfat_signature", "corrupt_efi_fat"])
def test_gpt_mixed_image_rejects_ambiguous_or_corrupt_fat(tmp_path: Path, mutation: str) -> None:
    disk = _with_basic_data(_image(), b"NTFS    " if mutation != "unknown_fs" else b"BROKEN  ")
    if mutation == "invalid_nonfat_signature":
        disk[108 * 512 + 510:108 * 512 + 512] = b"\x00\x00"
    elif mutation == "corrupt_efi_fat":
        disk[40 * 512 + 11:40 * 512 + 13] = b"\x00\x00"
    path = tmp_path / "corrupt-mixed.img"
    path.write_bytes(disk)
    with pytest.raises(DiscoveryError):
        probe_boot_loader_entries(path)


@pytest.mark.parametrize("mutation", ["primary_crc", "backup_crc", "table_crc",
                                      "hybrid_mismatch", "truncated", "overlap"])
def test_gpt_corruption_fails_closed(tmp_path: Path, mutation: str) -> None:
    disk = _image(hybrid=mutation == "hybrid_mismatch")
    if mutation == "primary_crc":
        disk[512 + 24] ^= 1
    elif mutation == "backup_crc":
        disk[127 * 512 + 24] ^= 1
    elif mutation == "table_crc":
        disk[2 * 512 + 16] ^= 1
    elif mutation == "hybrid_mismatch":
        struct.pack_into("<I", disk, 470, 41)
    elif mutation == "truncated":
        del disk[-512:]
    elif mutation == "overlap":
        # A second overlapping GPT partition, with both table/header CRCs repaired.
        for lba in (2, 126):
            start = lba * 512 + 128
            disk[start:start + 16] = EFI_SYSTEM_GUID
            struct.pack_into("<QQ", disk, start + 32, 45, 50)
        crc = zlib.crc32(disk[2 * 512:3 * 512])
        for lba in (1, 127):
            offset = lba * 512
            struct.pack_into("<I", disk, offset + 88, crc)
            struct.pack_into("<I", disk, offset + 16, 0)
            struct.pack_into("<I", disk, offset + 16, zlib.crc32(disk[offset:offset + 92]))
    path = tmp_path / "corrupt.img"
    path.write_bytes(disk)
    with pytest.raises(DiscoveryError):
        probe_boot_loader_entries(path)
