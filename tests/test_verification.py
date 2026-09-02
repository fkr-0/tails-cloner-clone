from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from tails_cloner.verification import (
    TAILS_SIGNING_PRIMARY_FINGERPRINT,
    parse_sha256_text,
    sha256_file,
    verify_openpgp_detached_signature,
    verify_sha256,
)

ROOT = Path(__file__).resolve().parents[1]
TAILS_SIGNING_KEY = ROOT / "assets/tails-signing-minimal.key"
SIGNING_SUBKEY_FINGERPRINT = "CEB36DE785728E708F593B75C69FF0E4C08F8209"


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


def _signature_fixture_files(tmp_path: Path) -> tuple[Path, Path, Path]:
    image = tmp_path / "tails.img"
    signature = tmp_path / "tails.img.sig"
    key = tmp_path / "tails-signing.key"
    image.write_bytes(b"image")
    signature.write_bytes(b"signature")
    key.write_bytes(b"key")
    return image, signature, key


def _validsig_status(primary_fingerprint: str = TAILS_SIGNING_PRIMARY_FINGERPRINT) -> str:
    return (
        "[GNUPG:] GOODSIG C69FF0E4C08F8209 Tails developers <tails@boum.org>\n"
        f"[GNUPG:] VALIDSIG {SIGNING_SUBKEY_FINGERPRINT} 2026-07-15 1784131200 "
        f"0 4 0 22 10 00 {primary_fingerprint}\n"
    )


def test_openpgp_verification_requires_pinned_primary_fingerprint(tmp_path: Path) -> None:
    image, signature, key = _signature_fixture_files(tmp_path)
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if "--fingerprint" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=f"fpr:::::::::{TAILS_SIGNING_PRIMARY_FINGERPRINT}:\n",
                stderr="",
            )
        if "--verify" in command:
            return subprocess.CompletedProcess(command, 0, stdout=_validsig_status(), stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    signer = verify_openpgp_detached_signature(image, signature, key, run=fake_run)

    assert signer == SIGNING_SUBKEY_FINGERPRINT
    assert any("--import" in command for command in commands)
    assert any("--verify" in command for command in commands)
    assert all("--homedir" in command for command in commands)


def test_openpgp_verification_rejects_signature_from_other_primary_key(tmp_path: Path) -> None:
    image, signature, key = _signature_fixture_files(tmp_path)

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if "--fingerprint" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=f"fpr:::::::::{TAILS_SIGNING_PRIMARY_FINGERPRINT}:\n",
                stderr="",
            )
        if "--verify" in command:
            return subprocess.CompletedProcess(command, 0, stdout=_validsig_status("F" * 40), stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    with pytest.raises(ValueError, match="does not chain to the pinned Tails signing key"):
        verify_openpgp_detached_signature(image, signature, key, run=fake_run)


def test_openpgp_verification_rejects_expired_key_status(tmp_path: Path) -> None:
    image, signature, key = _signature_fixture_files(tmp_path)

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if "--fingerprint" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=f"fpr:::::::::{TAILS_SIGNING_PRIMARY_FINGERPRINT}:\n",
                stderr="",
            )
        if "--verify" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    "[GNUPG:] EXPKEYSIG C69FF0E4C08F8209 Tails developers\n"
                    + _validsig_status()
                ),
                stderr="expired signing key",
            )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    with pytest.raises(ValueError, match="OpenPGP signature verification failed"):
        verify_openpgp_detached_signature(image, signature, key, run=fake_run)


def test_local_tails_signed_upgrade_metadata_verifies_when_available() -> None:
    signed_metadata = (
        ROOT
        / "tails_issue_fix/tails/wiki/src/upgrade/v2/Tails/7.7.2/amd64/stable/upgrades.yml"
    )
    detached_signature = signed_metadata.with_suffix(".yml.pgp")
    if not signed_metadata.exists() or not detached_signature.exists():
        pytest.skip("optional nested Tails source tree is not present")

    signer = verify_openpgp_detached_signature(
        signed_metadata,
        detached_signature,
        TAILS_SIGNING_KEY,
    )

    assert signer in {
        "CEB36DE785728E708F593B75C69FF0E4C08F8209",
        "4FDE1D065B10343FBD642A14BC8BD3DAC9CD2979",
        "A013A001BEDF1AFADF0B0B3AE26AE7BE8FA5B8D1",
    }


def test_bundled_tails_signing_key_has_pinned_fingerprint() -> None:
    result = subprocess.run(
        ["gpg", "--batch", "--show-keys", "--with-colons", str(TAILS_SIGNING_KEY)],
        check=True,
        text=True,
        capture_output=True,
    )

    assert f"fpr:::::::::{TAILS_SIGNING_PRIMARY_FINGERPRINT}:" in result.stdout
