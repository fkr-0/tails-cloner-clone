from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tails_cloner.verification import parse_sha256_text, sha256_file, verify_sha256


def test_parse_gnu_checksum_prefers_expected_filename() -> None:
    digest_a = "a" * 64
    digest_b = "b" * 64
    text = f"{digest_a}  other.img\n{digest_b} *tails.img\n"

    assert parse_sha256_text(text, expected_filename="tails.img") == digest_b


def test_parse_bsd_checksum() -> None:
    digest = "c" * 64

    assert parse_sha256_text(f"SHA256 (tails.img) = {digest}\n", expected_filename="tails.img") == digest


def test_parse_checksum_rejects_wrong_filename() -> None:
    with pytest.raises(ValueError, match="No SHA-256 entry"):
        parse_sha256_text(f"{'d' * 64}  other.img\n", expected_filename="tails.img")


def test_verify_sha256_accepts_matching_file(tmp_path: Path) -> None:
    image = tmp_path / "tails.img"
    image.write_bytes(b"tails image")
    expected = hashlib.sha256(b"tails image").hexdigest()

    assert sha256_file(image) == expected
    assert verify_sha256(image, expected) == expected


def test_verify_sha256_rejects_mismatch(tmp_path: Path) -> None:
    image = tmp_path / "tails.img"
    image.write_bytes(b"corrupt")

    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        verify_sha256(image, "0" * 64)
