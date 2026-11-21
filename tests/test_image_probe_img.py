"""Synthetic regular-file FAT images only; never touch block devices or mounts."""
from __future__ import annotations

from pathlib import Path

import pytest

from tails_cloner.image_probe import DiscoveryError, probe_boot_loader_entries


def _fat12(*, corrupt: bool = False, no_config: bool = False) -> bytes:
    image = bytearray(128 * 512)
    boot = memoryview(image)[:512]
    boot[0:3] = b"\xeb\x3c\x90"
    boot[11:13] = (512).to_bytes(2, "little")
    boot[13] = 1
    boot[14:16] = (1).to_bytes(2, "little")
    boot[16] = 1
    boot[17:19] = (16).to_bytes(2, "little")
    boot[19:21] = (128).to_bytes(2, "little")
    boot[22:24] = (1).to_bytes(2, "little")
    boot[510:512] = b"\x55\xaa"
    image[512:515] = b"\xf8\xff\xff"
    # FAT12 cluster 2: end of chain or intentional self-loop.
    image[515:517] = b"\x02\xf0" if corrupt else b"\xff\x0f"
    config = b"menuentry 'Real IMG' {\n}\n"
    if not no_config:
        image[1024:1035] = b"GRUB    CFG"
        image[1035] = 0x20
        image[1050:1052] = (2).to_bytes(2, "little")
        image[1052:1056] = len(config).to_bytes(4, "little")
    image[1536:1536 + len(config)] = config
    image[4096:4128] = b"menuentry 'Noise' {\n}\n".ljust(32, b"!")
    return bytes(image)


def _mbr(volume: bytes, *, kind: int = 0x01, size_override: int | None = None) -> bytes:
    disk = bytearray(512 + len(volume))
    disk[510:512] = b"\x55\xaa"
    disk[450] = kind
    disk[454:458] = (1).to_bytes(4, "little")
    disk[458:462] = (size_override if size_override is not None else len(volume) // 512).to_bytes(4, "little")
    disk[512:] = volume
    return bytes(disk)


def _fat32() -> bytes:
    sectors = 70000
    image = bytearray(sectors * 512)
    boot = memoryview(image)[:512]
    boot[0:3] = b"\xeb\x58\x90"
    boot[11:13] = (512).to_bytes(2, "little")
    boot[13] = 1
    boot[14:16] = (32).to_bytes(2, "little")
    boot[16] = 1
    boot[32:36] = sectors.to_bytes(4, "little")
    boot[36:40] = (600).to_bytes(4, "little")
    boot[44:48] = (2).to_bytes(4, "little")
    boot[510:512] = b"\x55\xaa"
    fat_start = 32 * 512
    image[fat_start:fat_start + 16] = b"\xf8\xff\xff\x0f\xff\xff\xff\x0f\xff\xff\xff\x0f\xff\xff\xff\x0f"
    data_start = (32 + 600) * 512
    image[data_start:data_start + 11] = b"GRUB    CFG"
    image[data_start + 11] = 0x20
    image[data_start + 20:data_start + 22] = b"\x00\x00"
    image[data_start + 26:data_start + 28] = (3).to_bytes(2, "little")
    config = b"menuentry 'FAT32' {\n}\n"
    image[data_start + 28:data_start + 32] = len(config).to_bytes(4, "little")
    image[data_start + 512:data_start + 512 + len(config)] = config
    return bytes(image)


@pytest.mark.parametrize("partitioned", [False, True])  # type: ignore[untyped-decorator]
def test_fat12_structural_discovery_with_provenance(tmp_path: Path, partitioned: bool) -> None:
    image = tmp_path / "source.img"
    image.write_bytes(_mbr(_fat12()) if partitioned else _fat12())
    result = probe_boot_loader_entries(image)
    assert result.format == "fat-img"
    assert [(entry.title, entry.config_path, entry.loader) for entry in result.entries] == [
        ("Real IMG", "partition1/grub.cfg" if partitioned else "volume/grub.cfg", "grub")
    ]
    assert result.bytes_read < image.stat().st_size


def test_fat_does_not_scan_unreferenced_menu_text(tmp_path: Path) -> None:
    image = tmp_path / "noise.img"
    image.write_bytes(_fat12(no_config=True))
    assert probe_boot_loader_entries(image).entries == ()


def test_partitioned_fat32_directory_chain(tmp_path: Path) -> None:
    image = tmp_path / "fat32.img"
    image.write_bytes(_mbr(_fat32(), kind=0x0c))
    result = probe_boot_loader_entries(image)
    assert [(entry.title, entry.config_path) for entry in result.entries] == [
        ("FAT32", "partition1/grub.cfg")
    ]


@pytest.mark.parametrize("image_bytes", [  # type: ignore[untyped-decorator]
    _fat12(corrupt=True),
    _mbr(_fat12(), kind=0x83),
    _mbr(_fat12(), size_override=1000),
    b"opaque menuentry 'Forged' {\n}\n",
])
def test_fat_corruption_and_unsupported_layout_fail_closed(tmp_path: Path, image_bytes: bytes) -> None:
    image = tmp_path / "invalid.img"
    image.write_bytes(image_bytes)
    with pytest.raises(DiscoveryError):
        probe_boot_loader_entries(image)


@pytest.mark.parametrize("mbr_opcode", [0x00, 0xEB, 0xE9])  # type: ignore[untyped-decorator]
def test_mbr_fat_with_unrelated_partition_and_boot_code(tmp_path: Path, mbr_opcode: int) -> None:
    disk = bytearray(_mbr(_fat12()))
    disk[0] = mbr_opcode
    # A disjoint, non-FAT partition must not suppress FAT boot discovery.
    disk.extend(b"\x00" * 512)
    disk[466] = 0x83
    disk[470:474] = (129).to_bytes(4, "little")
    disk[474:478] = (1).to_bytes(4, "little")
    image = tmp_path / "mixed.img"
    image.write_bytes(disk)
    result = probe_boot_loader_entries(image)
    assert [(entry.title, entry.config_path) for entry in result.entries] == [
        ("Real IMG", "partition1/grub.cfg")
    ]


def test_mbr_overlapping_unrelated_partition_fails_closed(tmp_path: Path) -> None:
    disk = bytearray(_mbr(_fat12()))
    disk[466] = 0x83
    disk[470:474] = (2).to_bytes(4, "little")
    disk[474:478] = (1).to_bytes(4, "little")
    image = tmp_path / "overlap.img"
    image.write_bytes(disk)
    with pytest.raises(DiscoveryError, match="overlapping MBR"):
        probe_boot_loader_entries(image)


def test_fat_cancellation(tmp_path: Path) -> None:
    image = tmp_path / "source.img"
    image.write_bytes(_fat12())
    with pytest.raises(DiscoveryError, match="cancelled"):
        probe_boot_loader_entries(image, cancelled=lambda: True)
