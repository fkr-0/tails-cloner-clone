"""Integrity verification helpers for downloaded Tails images."""

from __future__ import annotations

import hashlib
import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory

_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_BSD_SHA256_RE = re.compile(r"^SHA256 \((?P<name>.+)\) = (?P<digest>[0-9a-fA-F]{64})$")
TAILS_SIGNING_PRIMARY_FINGERPRINT = "A490D0F4D311A4153E2BB7CADBB802B258ACD84F"
RunCommand = Callable[..., subprocess.CompletedProcess[str]]


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


def _run_gpg(command: list[str], run: RunCommand) -> subprocess.CompletedProcess[str]:
    try:
        result = run(command, check=False, text=True, capture_output=True)
    except FileNotFoundError as error:
        raise RuntimeError("GnuPG is required to verify Tails image signatures") from error
    return result


def _gpg_error(result: subprocess.CompletedProcess[str], action: str) -> ValueError:
    detail = (result.stderr or result.stdout).strip()
    return ValueError(f"OpenPGP {action} failed: {detail or f'gpg exited with {result.returncode}'}")


def verify_openpgp_detached_signature(
    image_path: str | Path,
    signature_path: str | Path,
    key_path: str | Path,
    *,
    expected_primary_fingerprint: str = TAILS_SIGNING_PRIMARY_FINGERPRINT,
    run: RunCommand = subprocess.run,
) -> str:
    """Verify a detached Tails image signature against a pinned primary key.

    A fresh temporary GnuPG home prevents the user's keyring and trust settings
    from influencing verification. The imported key must contain the exact
    expected Tails primary fingerprint, and the valid signature must chain to
    that primary key. Expired or revoked signatures/keys are rejected.
    """
    image = Path(image_path)
    signature = Path(signature_path)
    key = Path(key_path)
    for candidate, description in (
        (image, "image"),
        (signature, "detached signature"),
        (key, "Tails signing key"),
    ):
        if not candidate.is_file():
            raise FileNotFoundError(f"Missing {description}: {candidate}")

    expected = expected_primary_fingerprint.replace(" ", "").upper()
    if not re.fullmatch(r"[0-9A-F]{40}", expected):
        raise ValueError("Pinned OpenPGP fingerprint must contain exactly 40 hexadecimal characters")

    with TemporaryDirectory(prefix="tails-cloner-gpg-") as gnupg_home:
        base = ["gpg", "--batch", "--no-options", "--homedir", gnupg_home]
        imported = _run_gpg([*base, "--import", str(key)], run)
        if imported.returncode != 0:
            raise _gpg_error(imported, "key import")

        listed = _run_gpg([*base, "--with-colons", "--fingerprint", expected], run)
        if listed.returncode != 0:
            raise _gpg_error(listed, "fingerprint lookup")
        fingerprints = {
            fields[9].upper()
            for line in listed.stdout.splitlines()
            if (fields := line.split(":")) and fields[0] == "fpr" and len(fields) > 9
        }
        if expected not in fingerprints:
            raise ValueError(f"Bundled Tails signing key does not contain pinned fingerprint {expected}")

        verified = _run_gpg(
            [*base, "--status-fd", "1", "--verify", str(signature), str(image)],
            run,
        )
        status = verified.stdout
        rejected_markers = (
            "BADSIG",
            "ERRSIG",
            "EXPSIG",
            "EXPKEYSIG",
            "REVKEYSIG",
            "KEYREVOKED",
            "NO_PUBKEY",
        )
        if verified.returncode != 0 or any(f"[GNUPG:] {marker}" in status for marker in rejected_markers):
            raise _gpg_error(verified, "signature verification")

        for line in status.splitlines():
            if not line.startswith("[GNUPG:] VALIDSIG "):
                continue
            fields = line.split()
            signing_fingerprint = fields[2].upper()
            primary_fingerprint = fields[-1].upper() if len(fields) >= 12 else signing_fingerprint
            if expected not in {signing_fingerprint, primary_fingerprint}:
                raise ValueError(
                    "OpenPGP signature is valid but does not chain to the pinned Tails signing key"
                )
            return signing_fingerprint

        raise ValueError("OpenPGP verification did not emit a valid-signature record")
