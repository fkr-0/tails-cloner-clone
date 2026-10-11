from __future__ import annotations

import hashlib
from email.message import Message
from pathlib import Path
from unittest import mock
from urllib.request import Request

import pytest

from tails_cloner.download import (
    DownloadNetworkError,
    DownloadVerificationError,
    HttpsOnlyRedirectHandler,
    RemoteImageRequest,
    VerifiedImageDownloader,
)


def request(destination: Path) -> RemoteImageRequest:
    return RemoteImageRequest(
        version="7.7.2",
        image_url="https://example.invalid/tails-amd64-7.7.2.img",
        checksum_url="https://example.invalid/tails-amd64-7.7.2.img.sha256",
        signature_url="https://example.invalid/tails-amd64-7.7.2.img.sig",
        destination=destination,
    )


def test_remote_image_request_rejects_plain_http_before_network(tmp_path: Path) -> None:
    downloader = VerifiedImageDownloader(use_torify=lambda: False)
    invalid = request(tmp_path / "tails.img")
    invalid = RemoteImageRequest(
        version=invalid.version,
        image_url="http://example.invalid/tails.img",
        checksum_url=invalid.checksum_url,
        signature_url=invalid.signature_url,
        destination=invalid.destination,
    )
    with (
        mock.patch.object(downloader, "_read_checksum") as read_checksum,
        pytest.raises(DownloadNetworkError, match="must use HTTPS"),
    ):
        downloader.download(invalid)
    read_checksum.assert_not_called()


def test_direct_https_redirect_handler_rejects_downgrade_before_fetch() -> None:
    handler = HttpsOnlyRedirectHandler()
    request = Request("https://example.invalid/tails.img")
    with pytest.raises(ValueError, match="must use HTTPS"):
        handler.redirect_request(
            request,
            None,
            302,
            "Found",
            Message(),
            "http://attacker.invalid/tails.img",
        )


def test_existing_file_is_reused_only_after_fresh_sha_and_openpgp_verification(tmp_path: Path) -> None:
    destination = tmp_path / "tails-amd64-7.7.2.img"
    destination.write_bytes(b"already present verified image")
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    downloader = VerifiedImageDownloader(use_torify=lambda: False)

    def write_signature(_url: str, path: Path) -> None:
        path.write_bytes(b"signature")

    with (
        mock.patch.object(
            downloader,
            "_read_checksum",
            return_value=f"{digest}  tails-amd64-7.7.2.img\n",
        ),
        mock.patch.object(downloader, "_download_signature", side_effect=write_signature),
        mock.patch.object(
            downloader,
            "_verify_candidate",
            return_value=(digest, "0123456789ABCDEF0123456789ABCDEF01234567"),
        ) as verify_candidate,
        mock.patch.object(downloader, "_download_image") as download_image,
    ):
        result = downloader.download(request(destination))

    assert result.path == destination
    assert result.reused_existing is True
    assert result.sha256 == digest
    download_image.assert_not_called()
    verify_candidate.assert_called_once()
    assert destination.read_bytes() == b"already present verified image"
    assert list(tmp_path.glob(".*.part")) == []


def test_failed_verification_never_overwrites_existing_destination(tmp_path: Path) -> None:
    destination = tmp_path / "tails-amd64-7.7.2.img"
    destination.write_bytes(b"keep me")
    downloader = VerifiedImageDownloader(use_torify=lambda: False)

    def write_signature(_url: str, path: Path) -> None:
        path.write_bytes(b"signature")

    with (
        mock.patch.object(
            downloader,
            "_read_checksum",
            return_value=f"{'0' * 64}  tails-amd64-7.7.2.img\n",
        ),
        mock.patch.object(downloader, "_download_signature", side_effect=write_signature),
        mock.patch.object(
            downloader,
            "_verify_candidate",
            side_effect=DownloadVerificationError("SHA-256 mismatch"),
        ),
        mock.patch.object(downloader, "_download_image") as download_image,
        pytest.raises(DownloadVerificationError, match="SHA-256 mismatch"),
    ):
        downloader.download(request(destination))

    assert destination.read_bytes() == b"keep me"
    download_image.assert_not_called()
    assert list(tmp_path.glob(".*.part")) == []


def test_new_download_is_verified_before_atomic_promotion(tmp_path: Path) -> None:
    destination = tmp_path / "tails-amd64-7.7.2.img"
    image = b"new verified image"
    digest = hashlib.sha256(image).hexdigest()
    downloader = VerifiedImageDownloader(use_torify=lambda: False)

    def write_signature(_url: str, path: Path) -> None:
        path.write_bytes(b"signature")

    def write_image(_url: str, path: Path, _callback: object) -> None:
        path.write_bytes(image)

    def verify_candidate(path: Path, **_kwargs: object) -> tuple[str, str]:
        assert path != destination
        assert not destination.exists()
        assert path.read_bytes() == image
        return digest, "0123456789ABCDEF0123456789ABCDEF01234567"

    with (
        mock.patch.object(
            downloader,
            "_read_checksum",
            return_value=f"{digest}  tails-amd64-7.7.2.img\n",
        ),
        mock.patch.object(downloader, "_download_signature", side_effect=write_signature),
        mock.patch.object(downloader, "_download_image", side_effect=write_image),
        mock.patch.object(downloader, "_verify_candidate", side_effect=verify_candidate),
    ):
        result = downloader.download(request(destination))

    assert result.reused_existing is False
    assert destination.read_bytes() == image
    assert list(tmp_path.glob(".*.part")) == []


def test_torified_image_download_pins_https_for_initial_and_redirect_protocols(tmp_path: Path) -> None:
    downloader = VerifiedImageDownloader(use_torify=lambda: True)
    target = tmp_path / "tails.img.part"
    target.write_bytes(b"")
    with mock.patch("tails_cloner.download.subprocess.run") as run:
        downloader._download_image(
            "https://example.invalid/tails.img",
            target,
            None,
        )
    command = run.call_args.args[0]
    assert command[:3] == ["torify", "curl", "-fL"]
    assert "--proto" in command
    assert "=https" in command
    assert "--proto-redir" in command


def test_oversized_signature_is_reported_as_network_error_not_raw_value_error(tmp_path: Path) -> None:
    downloader = VerifiedImageDownloader(use_torify=lambda: False)
    destination = tmp_path / "tails-amd64-7.7.2.img"
    digest = "a" * 64

    class _Response:
        headers: dict[str, str] = {}

        def __enter__(self) -> "_Response":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def geturl(self) -> str:
            return "https://example.invalid/tails.sig"

        def read(self, _size: int = -1) -> bytes:
            return b"x" * (2 * 1024 * 1024)

    with (
        mock.patch.object(downloader, "_read_checksum", return_value=f"{digest}  tails-amd64-7.7.2.img\n"),
        mock.patch("tails_cloner.download.open_https", return_value=_Response()),
        pytest.raises(DownloadNetworkError, match="unexpectedly large"),
    ):
        downloader.download(request(destination))

    assert list(tmp_path.glob(".*")) == []


def test_https_downgrade_during_image_transfer_is_reported_as_network_error(tmp_path: Path) -> None:
    downloader = VerifiedImageDownloader(use_torify=lambda: False)
    destination = tmp_path / "tails-amd64-7.7.2.img"
    digest = "a" * 64

    def write_signature(_url: str, path: Path) -> None:
        path.write_bytes(b"signature")

    with (
        mock.patch.object(downloader, "_read_checksum", return_value=f"{digest}  tails-amd64-7.7.2.img\n"),
        mock.patch.object(downloader, "_download_signature", side_effect=write_signature),
        mock.patch(
            "tails_cloner.download.open_https",
            side_effect=ValueError("Remote redirect URL must use HTTPS: http://evil.invalid/"),
        ),
        pytest.raises(DownloadNetworkError, match="must use HTTPS"),
    ):
        downloader.download(request(destination))

    assert list(tmp_path.glob(".*")) == []


def test_fetch_checksum_text_enforces_https_and_size_limit() -> None:
    from tails_cloner.download import MAX_CHECKSUM_BYTES, fetch_checksum_text

    with pytest.raises(ValueError, match="must use HTTPS"):
        fetch_checksum_text("http://example.invalid/x.sha256", use_torify=False)

    class _Response:
        def __enter__(self) -> "_Response":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def geturl(self) -> str:
            return "https://example.invalid/x.sha256"

        def read(self, _size: int = -1) -> bytes:
            return b"0" * (MAX_CHECKSUM_BYTES + 1)

    with (
        mock.patch("tails_cloner.download.open_https", return_value=_Response()),
        pytest.raises(ValueError, match="unexpectedly large"),
    ):
        fetch_checksum_text("https://example.invalid/x.sha256", use_torify=False)


def test_fetch_checksum_text_uses_tor_when_requested() -> None:
    from tails_cloner.download import fetch_checksum_text

    with mock.patch("tails_cloner.download.fetch_text_torified", return_value="abc") as torified:
        assert fetch_checksum_text("https://example.invalid/x.sha256", use_torify=True) == "abc"
    torified.assert_called_once_with("https://example.invalid/x.sha256", 30)
