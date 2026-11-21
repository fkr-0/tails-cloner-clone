from __future__ import annotations

from pathlib import Path

import pytest

from tails_cloner.image_probe import (
    DiscoveryError,
    discover_boot_loader_entries,
    probe_boot_loader_entries,
)


def test_directory_provenance_is_per_config_and_not_merged(tmp_path: Path) -> None:
    (tmp_path / 'EFI' / 'BOOT').mkdir(parents=True)
    (tmp_path / 'EFI' / 'BOOT' / 'grub.cfg').write_text("menuentry 'EFI' {\n}\n")
    (tmp_path / 'isolinux.cfg').write_text('label bios\n menu label BIOS\n')
    result = probe_boot_loader_entries(tmp_path)
    assert [(entry.title, entry.loader, entry.config_path) for entry in result.entries] == [
        ('EFI', 'grub', 'EFI/BOOT/grub.cfg'), ('BIOS', 'syslinux', 'isolinux.cfg')
    ]
    assert discover_boot_loader_entries(tmp_path) == ['EFI', 'BIOS']


def test_unreadable_or_invalid_image_is_not_empty_success(tmp_path: Path) -> None:
    with pytest.raises(DiscoveryError):
        probe_boot_loader_entries(tmp_path / 'missing.iso')
    invalid = tmp_path / 'bad.iso'
    invalid.write_bytes(b'not an image\nmenuentry \'Noise\' {\n}\n')
    with pytest.raises(DiscoveryError):
        probe_boot_loader_entries(invalid)


def test_cancellation_during_directory_probe(tmp_path: Path) -> None:
    (tmp_path / 'grub.cfg').write_text("menuentry 'A' {\n}\n")
    with pytest.raises(DiscoveryError, match='cancel'):
        probe_boot_loader_entries(tmp_path, cancelled=lambda: True)


def test_iso_config_is_structurally_located_not_scanned_from_noise(tmp_path: Path) -> None:
    image = tmp_path / 'test.iso'
    image.write_bytes(_iso_fixture())
    result = probe_boot_loader_entries(image)
    assert [(e.title, e.config_path, e.loader) for e in result.entries] == [
        ('Real', 'BOOT/GRUB.CFG', 'grub')
    ]
    assert result.bytes_read < image.stat().st_size


def test_iso_entry_crossing_sector_and_utf8_boundary(tmp_path: Path) -> None:
    image = tmp_path / 'boundary.iso'
    config = b'#' * 2034 + b'\nmenuentry \'Caf\xc3\xa9\' {\n}\n'
    image.write_bytes(_iso_fixture(config))
    assert discover_boot_loader_entries(image) == ['Café']


def _iso_fixture(config: bytes = b'menuentry "Real" {\n}\n') -> bytes:
    sector = 2048
    data = bytearray(30 * sector)
    def record(name: bytes, extent: int, size: int, flags: int = 0) -> bytes:
        length = 33 + len(name) + (len(name) % 2 == 0)
        r = bytearray(length)
        r[0] = length
        r[2:6] = extent.to_bytes(4, 'little')
        r[10:14] = size.to_bytes(4, 'little')
        r[25] = flags
        r[28:30] = (1).to_bytes(2, 'little')
        r[32] = len(name)
        r[33:33+len(name)] = name
        return bytes(r)
    root = record(b'\x00', 20, sector, 2)
    data[16*sector+1:16*sector+6] = b'CD001'
    data[16*sector] = 1
    data[16*sector+80:16*sector+84] = (30).to_bytes(4, 'little')
    data[16*sector+156:16*sector+156+len(root)] = root
    data[17*sector] = 255
    data[17*sector+1:17*sector+6] = b'CD001'
    directory = record(b'BOOT', 21, sector, 2)
    data[20*sector:20*sector+len(directory)] = directory
    file_record = record(b'GRUB.CFG;1', 22, len(config))
    data[21*sector:21*sector+len(file_record)] = file_record
    data[22*sector:22*sector+len(config)] = config
    data[24*sector:24*sector+50] = b"menuentry 'Noise' {\n}\n".ljust(50, b'!')
    return bytes(data)
