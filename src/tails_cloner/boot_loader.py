from __future__ import annotations

import contextvars
import ctypes
import errno
import hashlib
import os
import re
import stat
import tempfile
from collections import Counter
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path


def _pinned_parent(path: Path) -> int:
    """Traverse the absolute parent path without following symlink components."""
    components = Path(os.path.abspath(path)).parent.parts[1:]
    fd = os.open(os.sep, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in components:
            next_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except BaseException:
        os.close(fd)
        raise


def _source_snapshot(path: Path) -> tuple[bytes, os.stat_result]:
    parent_fd = _pinned_parent(path)
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode):
                raise ValueError(f"unsafe non-regular boot-loader source: {path}")
            with os.fdopen(os.dup(fd), "rb") as handle:
                raw = handle.read()
            after = os.fstat(fd)
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise RuntimeError(f"boot-loader source changed during read: {path}")
            return raw, after
        finally:
            os.close(fd)
    finally:
        os.close(parent_fd)


def _assert_source_unchanged(path: Path, raw: bytes, original_stat: os.stat_result) -> None:
    current, snapshot = _source_snapshot(path)
    if ((snapshot.st_dev, snapshot.st_ino, snapshot.st_mtime_ns, snapshot.st_ctime_ns)
            != (original_stat.st_dev, original_stat.st_ino, original_stat.st_mtime_ns, original_stat.st_ctime_ns)
            or hashlib.sha256(current).digest() != hashlib.sha256(raw).digest()):
        raise RuntimeError(f"boot-loader source identity/content conflict: {path}")


class BackupDurabilityError(OSError):
    """Backup name published, but parent directory sync failed; durability uncertain."""


class ReplacementDurabilityError(OSError):
    """Destination was renamed successfully, but its directory sync failed."""

    replacement_applied = True


class RollbackConflictError(RuntimeError):
    """Refuse to roll back untrusted backup or independently changed target."""


class UnsupportedBootLoaderConfigError(ValueError):
    """A relevant config cannot be safely reordered; no directory writes began."""

    def __init__(self, path: Path, reason: str):
        self.path = path
        self.reason = reason
        super().__init__(f"unsupported boot-loader config {path}: {reason}")


_last_applied: contextvars.ContextVar[dict[Path, tuple[bytes, os.stat_result]] | None] = contextvars.ContextVar("boot_loader_last_applied", default=None)

_write_guard: contextvars.ContextVar[tuple[Path, bytes, os.stat_result] | None] = contextvars.ContextVar("boot_loader_write_guard", default=None)

BOOT_LOADER_CONFIG_GLOBS = (
    "**/grub.cfg",
    "**/loopback.cfg",
    "**/syslinux.cfg",
    "**/isolinux.cfg",
    "**/live.cfg",
    "**/live64.cfg",
)

_GRUB_MENUENTRY_RE = re.compile(r"^[ \t]*menuentry\s+(['\"])(?P<title>.+?)\1", re.MULTILINE)
_SYSLINUX_MENU_LABEL_RE = re.compile(r"^[ \t]*menu[ \t]+label[ \t]+(?P<title>.+?)[ \t]*$", re.MULTILINE | re.IGNORECASE)
_SYSLINUX_LABEL_RE = re.compile(r"^[ \t]*label[ \t]+(?P<title>[^\s#]+)[ \t]*$", re.MULTILINE | re.IGNORECASE)
_ANSI_MARKUP_RE = re.compile(r"\^|\\[a-z]+")


@dataclass(frozen=True, slots=True)
class BootMenuBlock:
    title: str
    start: int
    end: int
    text: str


@dataclass(slots=True)
class BootLoaderRewriteResult:
    changed: bool
    entries_before: list[str]
    entries_after: list[str]
    unsupported_reason: str = ""


@dataclass(slots=True)
class BootLoaderFileApplyResult:
    path: Path
    changed: bool
    entries_before: list[str]
    entries_after: list[str]
    backup_path: Path | None = None
    unsupported_reason: str = ""


@dataclass(slots=True)
class BootLoaderApplyResult:
    files: list[BootLoaderFileApplyResult] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return any(result.changed for result in self.files)

    @property
    def changed_paths(self) -> list[Path]:
        return [result.path for result in self.files if result.changed]


def _clean_entry_title(value: str) -> str:
    title = _ANSI_MARKUP_RE.sub("", value).strip().strip('"\'')
    return " ".join(title.split())


def unique_entries(entries: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for raw_entry in entries:
        entry = _clean_entry_title(raw_entry)
        if not entry or entry in seen:
            continue
        seen.add(entry)
        result.append(entry)
    return result


def parse_boot_loader_entries_from_text(text: str) -> list[str]:
    """Parse visible boot menu entries from GRUB or Syslinux-style config text."""
    grub_entries = [match.group("title") for match in _GRUB_MENUENTRY_RE.finditer(text)]
    syslinux_menu_labels = [match.group("title") for match in _SYSLINUX_MENU_LABEL_RE.finditer(text)]
    if grub_entries or syslinux_menu_labels:
        return unique_entries([*grub_entries, *syslinux_menu_labels])

    # Fallback for sparse Syslinux configs where only internal labels are present.
    return unique_entries([match.group("title") for match in _SYSLINUX_LABEL_RE.finditer(text)])


def reorder_entries(current_entries: list[str], desired_order: list[str]) -> list[str]:
    """Return current entries ordered by desired_order, preserving unknown tail entries."""
    current = unique_entries(current_entries)
    desired = unique_entries(desired_order)
    current_set = set(current)
    ordered = [entry for entry in desired if entry in current_set]
    ordered.extend(entry for entry in current if entry not in set(ordered))
    return ordered


def _find_matching_brace(text: str, opening_brace_index: int) -> int | None:
    depth = 0
    in_quote: str | None = None
    escaped = False
    in_comment = False
    for index in range(opening_brace_index, len(text)):
        char = text[index]
        if in_comment:
            if char == "\n":
                in_comment = False
            continue
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if in_quote is not None:
            if char == in_quote:
                in_quote = None
            continue
        if char == "#" and (index == 0 or text[index - 1].isspace()):
            in_comment = True
            continue
        if char in {"'", '"'}:
            in_quote = char
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = index + 1
                if end < len(text) and text[end] == "\n":
                    end += 1
                return end
    return None


def _parse_grub_menu_blocks(text: str) -> list[BootMenuBlock]:
    blocks: list[BootMenuBlock] = []
    for match in _GRUB_MENUENTRY_RE.finditer(text):
        opening_brace = text.find("{", match.end())
        if opening_brace == -1:
            continue
        end = _find_matching_brace(text, opening_brace)
        if end is None:
            continue
        blocks.append(
            BootMenuBlock(
                title=_clean_entry_title(match.group("title")),
                start=match.start(),
                end=end,
                text=text[match.start() : end],
            )
        )
    return blocks


def _parse_syslinux_menu_blocks(text: str) -> list[BootMenuBlock]:
    label_matches = list(_SYSLINUX_LABEL_RE.finditer(text))
    blocks: list[BootMenuBlock] = []
    for index, match in enumerate(label_matches):
        start = match.start()
        end = label_matches[index + 1].start() if index + 1 < len(label_matches) else len(text)
        block_text = text[start:end]
        menu_label_match = _SYSLINUX_MENU_LABEL_RE.search(block_text)
        title = menu_label_match.group("title") if menu_label_match else match.group("title")
        blocks.append(BootMenuBlock(title=_clean_entry_title(title), start=start, end=end, text=block_text))
    return blocks


def _rewrite_blocks(text: str, blocks: list[BootMenuBlock], desired_order: list[str]) -> tuple[str, list[str], list[str], bool]:
    entries_before = unique_entries([block.title for block in blocks])
    entries_after = reorder_entries(entries_before, desired_order)
    if entries_after == entries_before:
        return text, entries_before, entries_after, False

    block_by_title = {block.title: block for block in blocks}
    ordered_blocks = [block_by_title[entry] for entry in entries_after if entry in block_by_title]
    if not ordered_blocks:
        return text, entries_before, entries_after, False

    # Replace only menu slots; preserve all surrounding bytes, including comments.
    pieces: list[str] = []
    cursor = 0
    for slot, replacement in zip(blocks, ordered_blocks, strict=True):
        block_text = replacement.text.removesuffix("\n")
        if slot.text.endswith("\n"):
            block_text += "\n"
        pieces.extend((text[cursor:slot.start], block_text))
        cursor = slot.end
    pieces.append(text[cursor:])
    return "".join(pieces), entries_before, entries_after, True


def rewrite_boot_loader_config_text(text: str, desired_order: list[str]) -> tuple[str, BootLoaderRewriteResult]:
    desired = unique_entries(desired_order)
    # A text rewrite cannot preserve opaque bytes or ambiguous newline conventions.
    # In particular, mixed CRLF/LF causes slot replacement to corrupt line endings.
    if "\x00" in text or text.startswith("\ufeff"):
        entries = parse_boot_loader_entries_from_text(text)
        return text, BootLoaderRewriteResult(False, entries, entries, "NUL byte or UTF-8 BOM in boot configuration")
    if "\r" in text:
        entries = parse_boot_loader_entries_from_text(text)
        return text, BootLoaderRewriteResult(False, entries, entries, "CRLF, mixed or unsupported newline encoding")
    if not desired:
        entries = parse_boot_loader_entries_from_text(text)
        return text, BootLoaderRewriteResult(changed=False, entries_before=entries, entries_after=entries, unsupported_reason="empty desired order")

    entries = parse_boot_loader_entries_from_text(text)
    blocks = _parse_grub_menu_blocks(text) if _GRUB_MENUENTRY_RE.search(text) else _parse_syslinux_menu_blocks(text)
    titles = [block.title for block in blocks]
    if len(set(titles)) != len(titles):
        return text, BootLoaderRewriteResult(False, entries, entries, "duplicate boot menu titles")

    grub_blocks = blocks
    if _GRUB_MENUENTRY_RE.search(text):
        reason = ""
        # Reordering entries inside a submenu, conditional, shell function or
        # sourced configuration changes lexical scope and cannot be proven safe.
        outside = text
        for block in reversed(grub_blocks):
            outside = outside[:block.start] + "\n" + outside[block.end:]
        unsafe_scope = re.search(
            r"(?im)^[ \t]*(?:submenu|if|then|else|elif|fi|function|for|while|done|"
            r"source|configfile|include|[.][ \t]+)\b",
            outside,
        )
        if unsafe_scope or re.search(r"(?m)^[ \t]*[{}][ \t]*(?:#.*)?$", outside):
            reason = "GRUB scope, sourced config, or conditional outside menu entries"
        elif any("$" in block.title or "\\" in block.title for block in grub_blocks):
            reason = "dynamic or escaped GRUB menu title"
        elif len(list(re.finditer(r"(?m)^[ \t]*menuentry\b", text))) != len(grub_blocks):
            reason = "unrecognized GRUB menuentry title syntax"
        elif any(
            "{" not in text[match.end():text.find("\n", match.end()) if "\n" in text[match.end():] else len(text)]
            for match in _GRUB_MENUENTRY_RE.finditer(text)
        ):
            reason = "GRUB menuentry opening brace is not on its declaration line"
        if len(grub_blocks) != len(list(_GRUB_MENUENTRY_RE.finditer(text))):
            reason = "incomplete GRUB menu blocks"
        elif any(left.end > right.start for left, right in zip(grub_blocks, grub_blocks[1:], strict=False)):
            reason = "overlapping GRUB menu blocks"
        elif any(
            line.strip() and not line.lstrip().startswith("#")
            for left, right in zip(grub_blocks, grub_blocks[1:], strict=False)
            for line in text[left.end:right.start].splitlines()
        ):
            reason = "commands or scope boundaries between GRUB menu blocks"
        if reason:
            entries = parse_boot_loader_entries_from_text(text)
            return text, BootLoaderRewriteResult(False, entries, entries, reason)
        rewritten, before, after, changed = _rewrite_blocks(text, grub_blocks, desired)
        return rewritten, BootLoaderRewriteResult(changed=changed, entries_before=before, entries_after=after)

    syslinux_blocks = blocks
    if syslinux_blocks:
        if len(re.findall(r"(?im)^[ \t]*label\b", text)) != len(syslinux_blocks):
            return text, BootLoaderRewriteResult(False, entries, entries, "unrecognized Syslinux label syntax")
        # Syslinux has no explicit end-of-label marker. Restrict movable blocks
        # to known label-local directives; global directives anywhere after the
        # first label would otherwise travel with a different menu entry.
        local_directives = {
            "label", "menu label", "menu default", "menu hide", "menu disable",
            "kernel", "linux", "append", "initrd", "fdt", "fdtdir",
            "localboot", "ipappend", "sysappend", "text help", "endtext",
        }
        for block in syslinux_blocks:
            for line in block.text.splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                directive = " ".join(stripped.lower().split()[:2])
                key = directive if directive in local_directives else stripped.split()[0].lower()
                if key not in local_directives:
                    return text, BootLoaderRewriteResult(
                        False, entries, entries,
                        f"global or unknown Syslinux directive inside label block: {stripped.split()[0]}",
                    )
        rewritten, before, after, changed = _rewrite_blocks(text, syslinux_blocks, desired)
        return rewritten, BootLoaderRewriteResult(changed=changed, entries_before=before, entries_after=after)

    entries = parse_boot_loader_entries_from_text(text)
    return text, BootLoaderRewriteResult(changed=False, entries_before=entries, entries_after=entries, unsupported_reason="no supported boot menu blocks")


def _backup_path(path: Path) -> Path:
    """Return the first free backup name; reservation uses exclusive creation."""
    candidate = path.with_name(f"{path.name}.tails-cloner.bak")
    counter = 0
    while candidate.exists() or candidate.is_symlink():
        counter += 1
        candidate = path.with_name(f"{path.name}.tails-cloner.{counter}.bak")
    return candidate


def _sync_directory(directory: Path) -> None:
    """Best effort: some FAT mounts cannot fsync directory handles."""
    try:
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError:
        pass


def _copy_file_metadata(fd: int, source: os.stat_result) -> None:
    """FAT mount options synthesize Unix metadata; tolerate unsupported operations."""
    unsupported = {errno.EPERM, errno.EINVAL, errno.EOPNOTSUPP}
    try:
        os.fchmod(fd, stat.S_IMODE(source.st_mode))
    except OSError as error:
        if error.errno not in unsupported:
            raise
    if (os.fstat(fd).st_uid, os.fstat(fd).st_gid) != (source.st_uid, source.st_gid):
        try:
            os.fchown(fd, source.st_uid, source.st_gid)
        except OSError as error:
            if error.errno not in unsupported:
                raise


def _rename_no_replace(source_name: str, destination_name: str, parent_fd: int) -> None:
    """Linux renameat2 RENAME_NOREPLACE; vfat supports this since Linux 4.9."""
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise OSError(errno.ENOSYS, "atomic no-clobber rename unavailable")
    renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    renameat2.restype = ctypes.c_int
    if renameat2(parent_fd, os.fsencode(source_name), parent_fd, os.fsencode(destination_name), 1):
        value = ctypes.get_errno()
        raise OSError(value, os.strerror(value))


def _atomic_replace_bytes(path: Path, data: bytes, source_stat: os.stat_result) -> None:
    """Stage in the same directory, sync data and replace a regular file."""
    parent_fd = _pinned_parent(path)
    # /proc/self/fd resolves the pinned directory even if its old name is moved.
    pinned_dir = Path(f"/proc/self/fd/{parent_fd}")
    try:
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=pinned_dir)
    except BaseException:
        os.close(parent_fd)
        raise
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            _copy_file_metadata(handle.fileno(), source_stat)
            os.fsync(handle.fileno())
        guard = _write_guard.get()
        if guard is not None and guard[0] == path:
            _assert_source_unchanged(path, guard[1], guard[2])
            # A renamed parent cannot redirect the pinned destination.
            parent_now = os.stat(path.parent, follow_symlinks=False)
            parent_pinned = os.fstat(parent_fd)
            if (parent_now.st_dev, parent_now.st_ino) != (parent_pinned.st_dev, parent_pinned.st_ino):
                raise RuntimeError(f"boot-loader parent directory changed: {path}")
        # The final path operation never re-traverses a mutable parent pathname.
        os.replace(temporary.name, path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        applied = _last_applied.get()
        if applied is not None:
            applied[path] = _source_snapshot(path)
        try:
            os.fsync(parent_fd)
        except OSError as error:
            raise ReplacementDurabilityError(
                error.errno, f"replacement applied, directory durability unknown: {path}"
            ) from error
    finally:
        temporary.unlink(missing_ok=True)
        os.close(parent_fd)


def _read_config(path: Path, raw: bytes | None = None) -> tuple[bytes, str | None, str]:
    if raw is None:
        raw, _ = _source_snapshot(path)
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return raw, None, "non-UTF-8 boot configuration"
    if b"\r" in raw:
        if raw.replace(b"\r\n", b"").find(b"\r") >= 0 or b"\n" in raw.replace(b"\r\n", b""):
            return raw, None, "mixed or unsupported newline convention"
        text = text.replace("\r\n", "\n")
    return raw, text, ""


def _config_blocks(text: str) -> list[BootMenuBlock]:
    return _parse_grub_menu_blocks(text) if _GRUB_MENUENTRY_RE.search(text) else _parse_syslinux_menu_blocks(text)


def _verify_rewrite_shape(original: str, rewritten: str) -> bool:
    """Only menu blocks may move; immutable slots and block multiset must match."""
    before, after = _config_blocks(original), _config_blocks(rewritten)
    if len(before) != len(after):
        return False
    if Counter(block.text.rstrip("\n") for block in before) != Counter(
        block.text.rstrip("\n") for block in after
    ):
        return False

    def outside(text: str, blocks: list[BootMenuBlock]) -> list[str]:
        cursor = 0
        pieces = []
        for block in blocks:
            pieces.append(text[cursor:block.start])
            cursor = block.end
        pieces.append(text[cursor:])
        return pieces

    return outside(original, before) == outside(rewritten, after)


def _create_backup(path: Path, original: bytes) -> Path:
    """Publish a fully synced staged backup via atomic no-clobber rename."""
    parent_fd = _pinned_parent(path)
    temporary: str | None = None
    try:
        source = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.backup.", dir=f"/proc/self/fd/{parent_fd}")
        with os.fdopen(fd, "wb") as handle:
            handle.write(original)
            handle.flush()
            _copy_file_metadata(handle.fileno(), source)
            os.fsync(handle.fileno())
        for counter in range(10000):
            suffix = ".tails-cloner.bak" if counter == 0 else f".tails-cloner.{counter}.bak"
            name = path.name + suffix
            try:
                _rename_no_replace(Path(temporary).name, name, parent_fd)
            except FileExistsError:
                continue
            try:
                os.fsync(parent_fd)
            except OSError as error:
                raise BackupDurabilityError(error.errno, f"backup publication durability uncertain: {path}") from error
            return path.with_name(name)
        raise RuntimeError(f"exhausted boot-loader backup names for {path}")
    finally:
        if temporary is not None:
            with suppress(FileNotFoundError):
                os.unlink(Path(temporary).name, dir_fd=parent_fd)
        os.close(parent_fd)


def _restore_backups(
    restores: list[tuple[Path, Path, os.stat_result, bytes]],
    cause: Exception,
) -> None:
    errors: list[Exception] = []
    for path, backup, original_stat, trusted_original in reversed(restores):
        try:
            backup_raw, backup_stat = _source_snapshot(backup)
            if backup_raw != trusted_original or not stat.S_ISREG(backup_stat.st_mode):
                raise RollbackConflictError(f"rollback backup provenance conflict: {backup}")
            applied = _last_applied.get()
            if applied is None or path not in applied:
                raise RollbackConflictError(f"rollback target ownership unknown: {path}")
            written, written_stat = applied[path]
            _assert_source_unchanged(path, written, written_stat)
            _atomic_replace_bytes(path, trusted_original, original_stat)
            if path.read_bytes() != trusted_original:
                raise RuntimeError(f"rollback verification failed for {path}")
        except Exception as error:
            errors.append(error)
    if errors:
        raise ExceptionGroup("boot-loader rewrite and rollback failed; backups retained", [cause, *errors]) from cause


def apply_boot_loader_order_to_file(path: Path, desired_order: list[str]) -> BootLoaderFileApplyResult:
    raw, original_stat = _source_snapshot(path)
    raw, original, unsupported = _read_config(path, raw)
    if original is None:
        return BootLoaderFileApplyResult(path, False, [], [], unsupported_reason=unsupported)
    rewritten, result = rewrite_boot_loader_config_text(original, desired_order)
    if not result.changed:
        return BootLoaderFileApplyResult(
            path, False, result.entries_before, result.entries_after,
            unsupported_reason=result.unsupported_reason,
        )
    if not _verify_rewrite_shape(original, rewritten):
        raise RuntimeError(f"boot-loader rewrite verification failed before writing {path}: block or non-menu bytes differ")

    # Restore the original, uniform newline style without any text IO conversion.
    data = rewritten.encode("utf-8")
    if b"\r\n" in raw:
        data = data.replace(b"\n", b"\r\n")
    _assert_source_unchanged(path, raw, original_stat)
    backup = _create_backup(path, raw)
    writes_token = _last_applied.set({})
    replaced = False
    try:
        token = _write_guard.set((path, raw, original_stat))
        try:
            _atomic_replace_bytes(path, data, original_stat)
        finally:
            _write_guard.reset(token)
        replaced = True
        verified_raw, verified, reason = _read_config(path)
        expected_entries = reorder_entries(result.entries_before, desired_order)
        actual_entries = parse_boot_loader_entries_from_text(verified or "") if not reason else []
        if verified_raw != data or actual_entries != expected_entries:
            raise RuntimeError(f"boot-loader rewrite verification failed for {path}: expected {expected_entries}, got {actual_entries}")
    except ReplacementDurabilityError:
        # Rename already happened; do not misreport an unchanged target.
        raise
    except Exception as error:
        if replaced:
            _restore_backups([(path, backup, original_stat, raw)], error)
        raise
    finally:
        _last_applied.reset(writes_token)
    return BootLoaderFileApplyResult(path, True, result.entries_before, actual_entries, backup_path=backup)


def apply_boot_loader_order_to_directory(root: Path, desired_order: list[str]) -> BootLoaderApplyResult:
    """Apply all discovered configs or restore every previously modified config.

    Rollback is best-effort after interruption/power loss; on rollback failure an
    ExceptionGroup exposes both errors and retains individual backup files.
    """
    files: list[BootLoaderFileApplyResult] = []
    restores: list[tuple[Path, Path, os.stat_result, bytes]] = []
    seen: set[Path] = set()
    candidates = sorted({candidate for pattern in BOOT_LOADER_CONFIG_GLOBS for candidate in root.glob(pattern)})
    eligible: list[Path] = []
    # Preflight every eligible file before creating even the first backup.
    # Filename matches are relevant even when their encoding is opaque.
    for candidate in candidates:
        if candidate.is_symlink() or not candidate.is_file():
            continue
        if root.is_symlink() or any(
            part.is_symlink() for part in candidate.parents
            if part != root and root in part.parents
        ):
            continue
        raw, _ = _source_snapshot(candidate)
        _, text, reason = _read_config(candidate, raw)
        if text is None:
            raise UnsupportedBootLoaderConfigError(candidate, reason)
        # Include-only dispatchers and empty configs have no entries to move.
        # A malformed menu declaration must still fail closed.
        if not re.search(r"(?im)^[ \t]*(?:menuentry|submenu|label|menu[ \t]+label)\b", text):
            continue
        rewritten, rewrite = rewrite_boot_loader_config_text(text, desired_order)
        if rewrite.unsupported_reason:
            raise UnsupportedBootLoaderConfigError(candidate, rewrite.unsupported_reason)
        if not rewrite.entries_before:
            continue
        if rewrite.changed and not _verify_rewrite_shape(text, rewritten):
            raise UnsupportedBootLoaderConfigError(candidate, "rewrite shape verification failed")
        eligible.append(candidate)
    writes_token = _last_applied.set({})
    try:
        for candidate in eligible:
            if candidate in seen:
                continue
            seen.add(candidate)
            raw, _ = _source_snapshot(candidate)
            source_stat = candidate.stat()
            result = apply_boot_loader_order_to_file(candidate, desired_order)
            files.append(result)
            if result.changed and result.backup_path is not None:
                restores.append((candidate, result.backup_path, source_stat, raw))
                # File apply restores its own context; directory transaction tracks
                # each successfully applied target independently.
                applied = _last_applied.get()
                if applied is not None:
                    applied[candidate] = _source_snapshot(candidate)
    except Exception as error:
        _restore_backups(restores, error)
        raise
    finally:
        _last_applied.reset(writes_token)
    return BootLoaderApplyResult(files=files)
