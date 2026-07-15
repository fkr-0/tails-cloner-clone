"""Integrity verification helpers for downloaded Tails images."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_BSD_SHA256_RE = re.compile(r"^SHA256 \((?P<name>.+)\) = (?P<digest>[0-9a-fA-F]{64})$")


def sha256_file(path: str | Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def parse_sha256_text(text: str, *, expected_filename: str | None = None) -> str:
    """Extract a SHA-256 digest from common checksum-file formats.

    Supports GNU coreutils lines, BSD-style output, and a bare digest. When a
    filename is supplied, a matching entry is preferred and a mismatching
    multi-entry checksum file is rejected.
    """
    expected_basename = Path(expected_filename).name if expected_filename else None
    candidates: list[tuple[str, str | None]] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        bsd_match = _BSD_SHA256_RE.match(line)
        if bsd_match:
            candidates.append((bsd_match.group("digest").lower(), Path(bsd_match.group("name")).name))
            continue

        fields = line.split(maxsplit=1)
        if fields and _SHA256_RE.fullmatch(fields[0]):
            filename = None
            if len(fields) == 2:
                filename = fields[1].lstrip("* ")
                filename = Path(filename).name if filename else None
            candidates.append((fields[0].lower(), filename))

    if expected_basename:
        for digest, filename in candidates:
            if filename == expected_basename:
                return digest
        unnamed = [digest for digest, filename in candidates if filename is None]
        if len(unnamed) == 1:
            return unnamed[0]
        raise ValueError(f"No SHA-256 entry found for {expected_basename}")

    if len(candidates) == 1:
        return candidates[0][0]
    if not candidates:
        raise ValueError("Checksum response did not contain a SHA-256 digest")
    raise ValueError("Checksum response contains multiple entries; an expected filename is required")


def verify_sha256(path: str | Path, expected_digest: str) -> str:
    expected = expected_digest.strip().lower()
    if not _SHA256_RE.fullmatch(expected):
        raise ValueError("Expected digest is not a valid SHA-256 value")

    actual = sha256_file(path)
    if actual != expected:
        raise ValueError(f"SHA-256 mismatch: expected {expected}, got {actual}")
    return actual
