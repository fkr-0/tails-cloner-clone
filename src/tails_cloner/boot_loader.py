from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

BOOT_LOADER_CONFIG_GLOBS = (
    "**/grub.cfg",
    "**/loopback.cfg",
    "**/syslinux.cfg",
    "**/isolinux.cfg",
    "**/live.cfg",
)

_GRUB_MENUENTRY_RE = re.compile(r"^\s*menuentry\s+(['\"])(?P<title>.+?)\1", re.MULTILINE)
_SYSLINUX_MENU_LABEL_RE = re.compile(r"^\s*menu\s+label\s+(?P<title>.+?)\s*$", re.MULTILINE | re.IGNORECASE)
_SYSLINUX_LABEL_RE = re.compile(r"^\s*label\s+(?P<title>[^\s#]+)\s*$", re.MULTILINE | re.IGNORECASE)
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


def discover_boot_loader_entries_from_directory(root: Path) -> list[str]:
    entries: list[str] = []
    for pattern in BOOT_LOADER_CONFIG_GLOBS:
        for candidate in sorted(root.glob(pattern)):
            if not candidate.is_file():
                continue
            try:
                entries.extend(parse_boot_loader_entries_from_text(candidate.read_text(errors="replace")))
            except OSError:
                continue
    return unique_entries(entries)


def discover_boot_loader_entries_from_image_file(image_path: Path) -> list[str]:
    """Best-effort scan for boot menu labels embedded in an ISO/IMG.

    This intentionally does not mount or mutate the image. GRUB/Syslinux config
    snippets are stored as plain text inside Tails images, so a streaming text
    scan is enough to seed the experimental UI in most cases.
    """
    entries: list[str] = []
    overlap = ""
    chunk_size = 1024 * 1024
    try:
        with image_path.open("rb") as handle:
            while True:
                chunk = handle.read(chunk_size)
                if not chunk:
                    break
                text = overlap + chunk.decode("utf-8", errors="ignore")
                entries.extend(parse_boot_loader_entries_from_text(text))
                overlap = text[-4096:]
    except OSError:
        return []
    return unique_entries(entries)


def discover_boot_loader_entries(source_path: str | Path) -> list[str]:
    path = Path(source_path)
    if not path.exists():
        return []
    if path.is_dir():
        return discover_boot_loader_entries_from_directory(path)
    if path.is_file():
        return discover_boot_loader_entries_from_image_file(path)
    return []


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
    for index in range(opening_brace_index, len(text)):
        char = text[index]
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

    first_start = min(block.start for block in blocks)
    last_end = max(block.end for block in blocks)
    prefix = text[:first_start]
    suffix = text[last_end:]
    replacement = "\n".join(block.text.rstrip("\n") for block in ordered_blocks) + "\n"
    return prefix + replacement + suffix, entries_before, entries_after, True


def rewrite_boot_loader_config_text(text: str, desired_order: list[str]) -> tuple[str, BootLoaderRewriteResult]:
    desired = unique_entries(desired_order)
    if not desired:
        entries = parse_boot_loader_entries_from_text(text)
        return text, BootLoaderRewriteResult(changed=False, entries_before=entries, entries_after=entries, unsupported_reason="empty desired order")

    grub_blocks = _parse_grub_menu_blocks(text)
    if grub_blocks:
        rewritten, before, after, changed = _rewrite_blocks(text, grub_blocks, desired)
        return rewritten, BootLoaderRewriteResult(changed=changed, entries_before=before, entries_after=after)

    syslinux_blocks = _parse_syslinux_menu_blocks(text)
    if syslinux_blocks:
        rewritten, before, after, changed = _rewrite_blocks(text, syslinux_blocks, desired)
        return rewritten, BootLoaderRewriteResult(changed=changed, entries_before=before, entries_after=after)

    entries = parse_boot_loader_entries_from_text(text)
    return text, BootLoaderRewriteResult(changed=False, entries_before=entries, entries_after=entries, unsupported_reason="no supported boot menu blocks")


def _backup_path(path: Path) -> Path:
    candidate = path.with_name(f"{path.name}.tails-cloner.bak")
    if not candidate.exists():
        return candidate
    counter = 1
    while True:
        numbered = path.with_name(f"{path.name}.tails-cloner.{counter}.bak")
        if not numbered.exists():
            return numbered
        counter += 1


def apply_boot_loader_order_to_file(path: Path, desired_order: list[str]) -> BootLoaderFileApplyResult:
    original = path.read_text(encoding="utf-8", errors="replace")
    rewritten, result = rewrite_boot_loader_config_text(original, desired_order)
    if not result.changed:
        return BootLoaderFileApplyResult(
            path=path,
            changed=False,
            entries_before=result.entries_before,
            entries_after=result.entries_after,
            unsupported_reason=result.unsupported_reason,
        )

    backup = _backup_path(path)
    shutil.copy2(path, backup)
    path.write_text(rewritten, encoding="utf-8")
    verified_entries = parse_boot_loader_entries_from_text(path.read_text(encoding="utf-8", errors="replace"))
    expected_entries = reorder_entries(result.entries_before, desired_order)
    if verified_entries != expected_entries:
        shutil.copy2(backup, path)
        raise RuntimeError(
            f"boot-loader rewrite verification failed for {path}: expected {expected_entries}, got {verified_entries}"
        )
    return BootLoaderFileApplyResult(
        path=path,
        changed=True,
        entries_before=result.entries_before,
        entries_after=verified_entries,
        backup_path=backup,
    )


def apply_boot_loader_order_to_directory(root: Path, desired_order: list[str]) -> BootLoaderApplyResult:
    files: list[BootLoaderFileApplyResult] = []
    seen: set[Path] = set()
    for pattern in BOOT_LOADER_CONFIG_GLOBS:
        for candidate in sorted(root.glob(pattern)):
            if candidate in seen or not candidate.is_file():
                continue
            seen.add(candidate)
            text = candidate.read_text(encoding="utf-8", errors="replace")
            entries = parse_boot_loader_entries_from_text(text)
            if not entries:
                continue
            files.append(apply_boot_loader_order_to_file(candidate, desired_order))
    return BootLoaderApplyResult(files=files)
