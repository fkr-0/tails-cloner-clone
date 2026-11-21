from __future__ import annotations

import os
import re
import shutil
import stat
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

BOOT_LOADER_CONFIG_GLOBS = (
    "**/grub.cfg",
    "**/loopback.cfg",
    "**/syslinux.cfg",
    "**/isolinux.cfg",
    "**/live.cfg",
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


def _atomic_replace_bytes(path: Path, data: bytes, source_stat: os.stat_result) -> None:
    """Stage in the same directory, sync data and replace a regular file."""
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fchmod(handle.fileno(), stat.S_IMODE(source_stat.st_mode))
            if (os.fstat(handle.fileno()).st_uid, os.fstat(handle.fileno()).st_gid) != (
                source_stat.st_uid, source_stat.st_gid
            ):
                os.fchown(handle.fileno(), source_stat.st_uid, source_stat.st_gid)
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _read_config(path: Path) -> tuple[bytes, str | None, str]:
    raw = path.read_bytes()
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
    # O_EXCL avoids clobbering a backup made by a concurrent/earlier invocation.
    for _ in range(10000):
        candidate = _backup_path(path)
        try:
            with candidate.open("xb") as handle:
                handle.write(original)
                handle.flush()
                os.fsync(handle.fileno())
            shutil.copystat(path, candidate, follow_symlinks=False)
            _sync_directory(candidate.parent)
            return candidate
        except FileExistsError:
            continue
        except BaseException:
            candidate.unlink(missing_ok=True)
            raise
    raise RuntimeError(f"exhausted boot-loader backup names for {path}")


def _restore_backups(
    restores: list[tuple[Path, Path, os.stat_result]],
    cause: Exception,
) -> None:
    errors: list[Exception] = []
    for path, backup, original_stat in reversed(restores):
        try:
            _atomic_replace_bytes(path, backup.read_bytes(), original_stat)
            if path.read_bytes() != backup.read_bytes():
                raise RuntimeError(f"rollback verification failed for {path}")
        except Exception as error:
            errors.append(error)
    if errors:
        raise ExceptionGroup("boot-loader rewrite and rollback failed; backups retained", [cause, *errors]) from cause


def apply_boot_loader_order_to_file(path: Path, desired_order: list[str]) -> BootLoaderFileApplyResult:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"unsafe boot-loader path (not a regular non-symlink file): {path}")
    original_stat = path.stat()
    raw, original, unsupported = _read_config(path)
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
    backup = _create_backup(path, raw)
    replaced = False
    try:
        _atomic_replace_bytes(path, data, original_stat)
        replaced = True
        verified_raw, verified, reason = _read_config(path)
        expected_entries = reorder_entries(result.entries_before, desired_order)
        actual_entries = parse_boot_loader_entries_from_text(verified or "") if not reason else []
        if verified_raw != data or actual_entries != expected_entries:
            raise RuntimeError(f"boot-loader rewrite verification failed for {path}: expected {expected_entries}, got {actual_entries}")
    except Exception as error:
        if replaced or path.read_bytes() != raw:
            _restore_backups([(path, backup, original_stat)], error)
        raise
    return BootLoaderFileApplyResult(path, True, result.entries_before, actual_entries, backup_path=backup)


def apply_boot_loader_order_to_directory(root: Path, desired_order: list[str]) -> BootLoaderApplyResult:
    """Apply all discovered configs or restore every previously modified config.

    Rollback is best-effort after interruption/power loss; on rollback failure an
    ExceptionGroup exposes both errors and retains individual backup files.
    """
    files: list[BootLoaderFileApplyResult] = []
    restores: list[tuple[Path, Path, os.stat_result]] = []
    seen: set[Path] = set()
    candidates = sorted({candidate for pattern in BOOT_LOADER_CONFIG_GLOBS for candidate in root.glob(pattern)})
    try:
        for candidate in candidates:
            if candidate in seen or candidate.is_symlink() or not candidate.is_file():
                continue
            seen.add(candidate)
            # Refuse every symlink in the ancestry, not just the config leaf.
            if root.is_symlink() or any(part.is_symlink() for part in candidate.parents if part != root and root in part.parents):
                continue
            raw, text, _ = _read_config(candidate)
            if text is None or not parse_boot_loader_entries_from_text(text):
                continue
            source_stat = candidate.stat()
            result = apply_boot_loader_order_to_file(candidate, desired_order)
            files.append(result)
            if result.changed and result.backup_path is not None:
                restores.append((candidate, result.backup_path, source_stat))
    except Exception as error:
        _restore_backups(restores, error)
        raise
    return BootLoaderApplyResult(files=files)
