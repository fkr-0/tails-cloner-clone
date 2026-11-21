"""Read-only boot-menu discovery with per-configuration provenance.

ISO9660 directory entries are resolved structurally. Unsupported image formats
fail closed instead of mining arbitrary binary regions for plausible menu text.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from tails_cloner.boot_loader import BOOT_LOADER_CONFIG_GLOBS, parse_boot_loader_entries_from_text

MAX_CONFIG_BYTES = 1024 * 1024
MAX_DIRECTORY_BYTES = 4 * 1024 * 1024
MAX_ISO_DEPTH = 12
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
        "grub.cfg", "loopback.cfg", "syslinux.cfg", "isolinux.cfg", "live.cfg"
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
    # Never scan opaque IMG/FAT/binary bytes: that creates fabricated menu entries.
    return _iso(path, cancelled, progress)


def discover_boot_loader_entries_from_directory(root: Path) -> list[str]:
    return list(dict.fromkeys(entry.title for entry in _directory(root, None, None).entries))


def discover_boot_loader_entries_from_image_file(image_path: Path) -> list[str]:
    return list(dict.fromkeys(entry.title for entry in _iso(image_path, None, None).entries))


def discover_boot_loader_entries(source_path: str | Path) -> list[str]:
    return list(dict.fromkeys(entry.title for entry in probe_boot_loader_entries(source_path).entries))
