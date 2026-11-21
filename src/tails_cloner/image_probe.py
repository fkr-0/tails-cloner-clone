"""Read-only boot-menu discovery with per-configuration provenance.

ISO9660 directory entries are resolved structurally. Unsupported image formats
fail closed instead of mining arbitrary binary regions for plausible menu text.
"""
# ruff: noqa: B023  # Per-volume nested walkers are fully executed before the next iteration.
from __future__ import annotations

import struct
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from tails_cloner.boot_loader import BOOT_LOADER_CONFIG_GLOBS, parse_boot_loader_entries_from_text

MAX_CONFIG_BYTES = 1024 * 1024
MAX_DIRECTORY_BYTES = 4 * 1024 * 1024
MAX_ISO_DEPTH = 12
MAX_FAT_CLUSTERS = 65536
MAX_FAT_DIRECTORY_BYTES = 4 * 1024 * 1024
MAX_PARTITIONS = 4
MAX_GPT_TABLE_BYTES = 1024 * 1024
EFI_SYSTEM_GUID = bytes.fromhex("28732ac11ff8d211ba4b00a0c93ec93b")
MICROSOFT_BASIC_GUID = bytes.fromhex("a2a0d0ebe5b9334487c068b6b72699c7")
SECTOR_BYTES = 2048
Progress = Callable[[int, int], None]
Cancellation = Callable[[], bool]


class DiscoveryError(ValueError):
    """Menu discovery could not be determined reliably."""


@dataclass(frozen=True, slots=True)
class DiscoveredEntry:
    title: str
    config_path: str
    loader: str


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    entries: tuple[DiscoveredEntry, ...]
    bytes_read: int
    format: str


def _check(cancelled: Cancellation | None) -> None:
    if cancelled is not None and cancelled():
        raise DiscoveryError("boot entry discovery cancelled")


def _loader(name: str) -> str:
    return "grub" if name.casefold().endswith(("grub.cfg", "loopback.cfg")) else "syslinux"


def _is_config(name: str) -> bool:
    return name.casefold().split("/")[-1] in {
        "grub.cfg", "loopback.cfg", "syslinux.cfg", "isolinux.cfg", "live.cfg",
        "live64.cfg",
    }


def _entries(data: bytes, path: str) -> list[DiscoveredEntry]:
    if b"\x00" in data:
        return []
    text = data.decode("utf-8", errors="replace")
    return [DiscoveredEntry(title, path, _loader(path)) for title in parse_boot_loader_entries_from_text(text)]


def _directory(root: Path, cancelled: Cancellation | None, progress: Progress | None) -> DiscoveryResult:
    entries: list[DiscoveredEntry] = []
    count = 0
    seen: set[Path] = set()
    # Use the same candidate set as the post-write rewriter, sorted by path.
    candidates: list[Path] = []
    for pattern in BOOT_LOADER_CONFIG_GLOBS:
        candidates.extend(p for p in root.glob(pattern) if p.is_file())
    for path in sorted(set(candidates)):
        _check(cancelled)
        if path in seen:
            continue
        seen.add(path)
        try:
            if path.stat().st_size > MAX_CONFIG_BYTES:
                raise DiscoveryError(f"boot config too large: {path}")
            data = path.read_bytes()
        except OSError as exc:
            raise DiscoveryError(f"cannot read boot config: {path}") from exc
        count += len(data)
        entries.extend(_entries(data, path.relative_to(root).as_posix()))
        if progress:
            progress(count, count)
    return DiscoveryResult(tuple(entries), count, "directory")


def _iso(path: Path, cancelled: Cancellation | None, progress: Progress | None) -> DiscoveryResult:
    entries: list[DiscoveredEntry] = []
    bytes_read = 0
    try:
        with path.open("rb") as handle:
            total = path.stat().st_size
            def read_at(offset: int, size: int) -> bytes:
                nonlocal bytes_read
                _check(cancelled)
                if size < 0 or offset < 0 or offset + size > total:
                    raise DiscoveryError("truncated or invalid ISO extent")
                handle.seek(offset)
                value = handle.read(size)
                bytes_read += len(value)
                if len(value) != size:
                    raise DiscoveryError("truncated ISO read")
                if progress:
                    progress(min(offset + size, total), total)
                return value

            descriptor = read_at(16 * SECTOR_BYTES, SECTOR_BYTES)
            if descriptor[0] != 1 or descriptor[1:6] != b"CD001":
                raise DiscoveryError("not a supported ISO9660 primary volume")
            volume_sectors = int.from_bytes(descriptor[80:84], "little")
            if not volume_sectors or volume_sectors * SECTOR_BYTES > total:
                raise DiscoveryError("truncated ISO volume")
            root = descriptor[156:190]

            def walk(record: bytes, prefix: str, depth: int, visited: set[tuple[int, int]]) -> None:
                _check(cancelled)
                if depth > MAX_ISO_DEPTH:
                    raise DiscoveryError("ISO directory nesting exceeds limit")
                if len(record) < 34:
                    raise DiscoveryError("invalid ISO directory record")
                extent = int.from_bytes(record[2:6], "little")
                size = int.from_bytes(record[10:14], "little")
                if extent < 16 or not size or extent * SECTOR_BYTES + size > volume_sectors * SECTOR_BYTES:
                    raise DiscoveryError("invalid ISO file extent")
                if record[25] & 2:
                    if size > MAX_DIRECTORY_BYTES:
                        raise DiscoveryError("ISO directory exceeds limit")
                    key = (extent, size)
                    if key in visited:
                        return
                    visited.add(key)
                    data = read_at(extent * SECTOR_BYTES, size)
                    index = 0
                    while index < len(data):
                        _check(cancelled)
                        length = data[index]
                        if not length:
                            index = ((index // SECTOR_BYTES) + 1) * SECTOR_BYTES
                            continue
                        if index + length > len(data) or length < 34:
                            raise DiscoveryError("malformed ISO directory entry")
                        child = data[index:index + length]
                        name = child[33:33 + child[32]].decode("ascii", errors="replace")
                        if name not in {"\x00", "\x01"}:
                            name = name.split(";", 1)[0]
                            if name not in {"", ".", ".."}:
                                walk(child, f"{prefix}/{name}".lstrip("/"), depth + 1, visited)
                        index += length
                elif _is_config(prefix):
                    if size > MAX_CONFIG_BYTES:
                        raise DiscoveryError("ISO boot config exceeds limit")
                    entries.extend(_entries(read_at(extent * SECTOR_BYTES, size), prefix))

            walk(root, "", 0, set())
    except OSError as exc:
        raise DiscoveryError(f"cannot read ISO: {path}") from exc
    return DiscoveryResult(tuple(entries), bytes_read, "iso9660")


def _fat_image(
    path: Path, cancelled: Cancellation | None, progress: Progress | None,
    gpt_volumes: list[tuple[int, int, str]] | None = None,
) -> DiscoveryResult:
    """Read FAT directory chains from a regular image, never mount or scan raw bytes."""
    entries: list[DiscoveredEntry] = []
    bytes_read = 0
    with path.open("rb") as handle:
        total = path.stat().st_size

        def read_at(offset: int, size: int) -> bytes:
            nonlocal bytes_read
            _check(cancelled)
            if offset < 0 or size < 0 or offset + size > total:
                raise DiscoveryError("FAT extent outside image")
            handle.seek(offset)
            data = handle.read(size)
            if len(data) != size:
                raise DiscoveryError("truncated FAT extent")
            bytes_read += size
            if progress:
                progress(min(offset + size, total), total)
            return data

        first = read_at(0, 512)
        volumes: list[tuple[int, int, str]] = []
        if gpt_volumes is not None:
            volumes = gpt_volumes
        elif first[510:512] == b"\x55\xaa" and any(
            first[446 + index * 16 + 4] != 0
            for index in range(MAX_PARTITIONS)
        ):
            all_extents: list[tuple[int, int]] = []
            for index in range(MAX_PARTITIONS):
                part = first[446 + index * 16:462 + index * 16]
                kind = part[4]
                start = int.from_bytes(part[8:12], "little") * 512
                size = int.from_bytes(part[12:16], "little") * 512
                if kind == 0 and start == 0 and size == 0:
                    continue
                if not start or not size or start + size > total:
                    raise DiscoveryError("invalid MBR partition extent")
                all_extents.append((start, size))
                if kind in (0x01, 0x04, 0x06, 0x0b, 0x0c, 0x0e):
                    volumes.append((start, size, f"partition{index + 1}"))
            ordered = sorted(all_extents)
            if any(a[0] + a[1] > b[0] for a, b in zip(ordered, ordered[1:], strict=False)):
                raise DiscoveryError("overlapping MBR partitions")
            if not volumes:
                raise DiscoveryError("no supported MBR FAT partition")
        else:
            volumes.append((0, total, "volume"))

        for base, capacity, label in volumes:
            boot = read_at(base, 512)
            if boot[510:512] != b"\x55\xaa" or boot[0] not in (0xeb, 0xe9):
                raise DiscoveryError("not a supported FAT boot sector")
            sector = int.from_bytes(boot[11:13], "little")
            sectors_per_cluster = boot[13]
            reserved = int.from_bytes(boot[14:16], "little")
            fats = boot[16]
            root_entries = int.from_bytes(boot[17:19], "little")
            sectors = int.from_bytes(boot[19:21], "little") or int.from_bytes(boot[32:36], "little")
            fat_sectors = int.from_bytes(boot[22:24], "little") or int.from_bytes(boot[36:40], "little")
            if (sector not in (512, 1024, 2048, 4096)
                    or not sectors_per_cluster or sectors_per_cluster & (sectors_per_cluster - 1)
                    or sectors_per_cluster > 128 or not reserved or fats not in (1, 2)
                    or not fat_sectors or not sectors or sectors * sector > capacity):
                raise DiscoveryError("invalid FAT geometry")
            root_sectors = (root_entries * 32 + sector - 1) // sector
            data_start = reserved + fats * fat_sectors + root_sectors
            if data_start >= sectors:
                raise DiscoveryError("invalid FAT data offset")
            count = (sectors - data_start) // sectors_per_cluster
            bits = 12 if count < 4085 else 16 if count < 65525 else 32
            if bits == 32 and (root_entries != 0 or int.from_bytes(boot[22:24], "little")):
                raise DiscoveryError("inconsistent FAT32 geometry")
            if bits != 32 and root_entries == 0:
                raise DiscoveryError("missing FAT12/16 root directory")
            fat_bytes = (count + 2) * bits // 8 + 4
            if fat_bytes > fat_sectors * sector or fat_bytes > 16 * 1024 * 1024:
                raise DiscoveryError("FAT table exceeds limit")
            fat = read_at(base + reserved * sector, fat_bytes)
            cluster_bytes = sector * sectors_per_cluster

            def next_cluster(cluster: int) -> int:
                if bits == 12:
                    offset = cluster + cluster // 2
                    value = int.from_bytes(fat[offset:offset + 2], "little")
                    return (value >> 4 if cluster & 1 else value & 0xfff)
                if bits == 16:
                    return int.from_bytes(fat[cluster * 2:cluster * 2 + 2], "little")
                return int.from_bytes(fat[cluster * 4:cluster * 4 + 4], "little") & 0x0fffffff

            def chain(first_cluster: int, max_bytes: int) -> bytes:
                data = bytearray()
                visited: set[int] = set()
                cluster = first_cluster
                end = {12: 0xff8, 16: 0xfff8, 32: 0x0ffffff8}[bits]
                while cluster < end:
                    _check(cancelled)
                    if cluster < 2 or cluster >= count + 2 or cluster in visited:
                        raise DiscoveryError("invalid or cyclic FAT cluster chain")
                    visited.add(cluster)
                    if len(visited) > MAX_FAT_CLUSTERS or len(data) + cluster_bytes > max_bytes + cluster_bytes:
                        raise DiscoveryError("FAT cluster chain exceeds limit")
                    data.extend(read_at(base + (data_start + (cluster - 2) * sectors_per_cluster) * sector, cluster_bytes))
                    cluster = next_cluster(cluster)
                return bytes(data)

            root_cluster = int.from_bytes(boot[44:48], "little") if bits == 32 else 0
            root_data = (chain(root_cluster, MAX_FAT_DIRECTORY_BYTES) if bits == 32 else
                         read_at(base + (reserved + fats * fat_sectors) * sector, root_sectors * sector))
            seen_dirs: set[int] = set()

            def walk(directory: bytes, prefix: str, depth: int) -> None:
                _check(cancelled)
                if depth > MAX_ISO_DEPTH or len(directory) > MAX_FAT_DIRECTORY_BYTES:
                    raise DiscoveryError("FAT directory exceeds limit")
                for offset in range(0, len(directory) - 31, 32):
                    item = directory[offset:offset + 32]
                    if item[0] == 0:
                        break
                    if item[0] == 0xe5 or item[11] == 0x0f or item[11] & 0x08:
                        continue
                    name = item[:8].decode("ascii", errors="replace").rstrip()
                    ext = item[8:11].decode("ascii", errors="replace").rstrip()
                    name = (name + ("." + ext if ext else "")).lower()
                    if name in (".", ".."):
                        continue
                    cluster = int.from_bytes(item[26:28], "little") | (int.from_bytes(item[20:22], "little") << 16 if bits == 32 else 0)
                    relative = f"{prefix}/{name}".lstrip("/")
                    if item[11] & 0x10:
                        if cluster in seen_dirs:
                            raise DiscoveryError("cyclic FAT directory")
                        seen_dirs.add(cluster)
                        walk(chain(cluster, MAX_FAT_DIRECTORY_BYTES), relative, depth + 1)
                    elif _is_config(relative):
                        size = int.from_bytes(item[28:32], "little")
                        if size > MAX_CONFIG_BYTES:
                            raise DiscoveryError("FAT boot config exceeds limit")
                        content = chain(cluster, MAX_CONFIG_BYTES)[:size] if size else b""
                        if len(content) < size:
                            raise DiscoveryError("truncated FAT config chain")
                        entries.extend(_entries(content, f"{label}/{relative}"))

            walk(root_data, "", 0)
    return DiscoveryResult(tuple(entries), bytes_read, "fat-img")


def _gpt_volumes(path: Path, cancelled: Cancellation | None) -> list[tuple[int, int, str]]:
    """Validate both GPT headers/tables; never trust hybrid MBR extents alone."""
    total = path.stat().st_size
    if total < 34 * 512 or total % 512:
        raise DiscoveryError("invalid GPT image length")
    sectors = total // 512
    with path.open("rb") as handle:
        def read(lba: int, size: int) -> bytes:
            _check(cancelled)
            if lba < 0 or size < 0 or lba * 512 + size > total:
                raise DiscoveryError("GPT extent outside image")
            handle.seek(lba * 512)
            data = handle.read(size)
            if len(data) != size:
                raise DiscoveryError("truncated GPT extent")
            return data

        mbr = read(0, 512)
        if mbr[510:512] != b"\x55\xaa":
            raise DiscoveryError("GPT protective MBR signature missing")
        mbr_parts = []
        for index in range(4):
            part = mbr[446 + index * 16:462 + index * 16]
            kind = part[4]
            start, count = struct.unpack_from("<II", part, 8)
            if kind == 0 and start == count == 0:
                continue
            if kind == 0 or start == 0 or count == 0:
                raise DiscoveryError("invalid hybrid MBR entry")
            mbr_parts.append((kind, start, count))
        if not any(kind == 0xee and start == 1 for kind, start, _ in mbr_parts):
            raise DiscoveryError("GPT protective MBR entry missing")

        def header(lba: int) -> tuple[int, int, int, int, int, int, bytes, bytes]:
            raw = read(lba, 512)
            if raw[:8] != b"EFI PART":
                raise DiscoveryError("GPT header signature missing")
            revision, size, crc = struct.unpack_from("<III", raw, 8)
            if revision != 0x00010000 or not 92 <= size <= 512:
                raise DiscoveryError("unsupported GPT header")
            copy = bytearray(raw[:size])
            copy[16:20] = b"\x00" * 4
            if zlib.crc32(copy) != crc:
                raise DiscoveryError("GPT header CRC mismatch")
            current, other, first, last = struct.unpack_from("<QQQQ", raw, 24)
            table_lba, count, width, table_crc = struct.unpack_from("<QIII", raw, 72)
            table_bytes = count * width
            table_sectors = (table_bytes + 511) // 512
            if (current != lba or other >= sectors or other == current
                    or first < 2 or first > last or last >= sectors
                    or width < 128 or width % 8 or not count
                    or table_bytes > MAX_GPT_TABLE_BYTES
                    or table_lba < 2 or table_lba * 512 + table_bytes > total
                    or not (table_lba + table_sectors <= first
                            or table_lba > last)
                    or table_lba <= other < table_lba + table_sectors):
                raise DiscoveryError("invalid GPT header geometry")
            table = read(table_lba, table_bytes)
            if zlib.crc32(table) != table_crc:
                raise DiscoveryError("GPT partition array CRC mismatch")
            return other, first, last, count, width, table_crc, table, raw[56:72]

        backup_lba, first, last, count, width, table_crc, table, disk_guid = header(1)
        if backup_lba != sectors - 1:
            raise DiscoveryError("GPT backup header not at image end")
        back_other, back_first, back_last, back_count, back_width, back_crc, back_table, back_guid = header(backup_lba)
        if (back_other != 1 or (back_first, back_last, back_count, back_width, back_crc)
                != (first, last, count, width, table_crc) or back_table != table
                or back_guid != disk_guid):
            raise DiscoveryError("inconsistent GPT backup")
        partitions: list[tuple[int, int, bytes, int]] = []
        for index in range(count):
            entry = table[index * width:(index + 1) * width]
            guid = entry[:16]
            if guid == bytes(16):
                continue
            start, end = struct.unpack_from("<QQ", entry, 32)
            if not first <= start <= end <= last:
                raise DiscoveryError("invalid GPT partition extent")
            partitions.append((start, end, guid, index + 1))
        ordered = sorted(partitions)
        if any(a[1] >= b[0] for a, b in zip(ordered, ordered[1:], strict=False)):
            raise DiscoveryError("overlapping GPT partitions")
        for kind, start, length in mbr_parts:
            if kind == 0xee:
                continue
            if not any(start == a and length == b - a + 1 for a, b, _, _ in partitions):
                raise DiscoveryError("hybrid MBR extent disagrees with GPT")
        volumes = []
        for start, end, guid, index in partitions:
            if guid not in (EFI_SYSTEM_GUID, MICROSOFT_BASIC_GUID):
                continue
            # Basic Data does not imply FAT: explicitly recognized NTFS/exFAT
            # volumes are not boot-menu candidates. Unknown or damaged FAT
            # candidates still undergo strict validation in _fat_image.
            if guid == MICROSOFT_BASIC_GUID:
                boot = read(start, 512)
                if (boot[510:512] == b"\x55\xaa"
                        and boot[3:11] in (b"NTFS    ", b"EXFAT   ")):
                    continue
            volumes.append((start * 512, (end - start + 1) * 512, f"partition{index}"))
        if not volumes:
            raise DiscoveryError("no supported FAT GPT partition")
        return volumes


def probe_boot_loader_entries(
    source_path: str | Path,
    *,
    cancelled: Cancellation | None = None,
    progress: Progress | None = None,
) -> DiscoveryResult:
    """Return provenance or raise DiscoveryError (including unsupported formats)."""
    path = Path(source_path)
    _check(cancelled)
    if path.is_dir():
        return _directory(path, cancelled, progress)
    if not path.is_file():
        raise DiscoveryError(f"source is missing or not a regular file: {path}")
    with path.open("rb") as handle:
        header = handle.read(16 * SECTOR_BYTES + 6)
    if len(header) >= 16 * SECTOR_BYTES + 6 and header[16 * SECTOR_BYTES + 1:16 * SECTOR_BYTES + 6] == b"CD001":
        return _iso(path, cancelled, progress)
    with path.open("rb") as handle:
        mbr = handle.read(1024)
    if mbr[512:520] == b"EFI PART" or (len(mbr) >= 512 and any(
        mbr[450 + index * 16] == 0xee for index in range(4)
    )):
        return _fat_image(path, cancelled, progress, _gpt_volumes(path, cancelled))
    return _fat_image(path, cancelled, progress)


def discover_boot_loader_entries_from_directory(root: Path) -> list[str]:
    return list(dict.fromkeys(entry.title for entry in _directory(root, None, None).entries))


def discover_boot_loader_entries_from_image_file(image_path: Path) -> list[str]:
    return list(dict.fromkeys(entry.title for entry in probe_boot_loader_entries(image_path).entries))


def discover_boot_loader_entries(source_path: str | Path) -> list[str]:
    return list(dict.fromkeys(entry.title for entry in probe_boot_loader_entries(source_path).entries))
