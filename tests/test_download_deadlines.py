"""Host-only adversarial transfer tests; never contacts a public endpoint."""

from __future__ import annotations

import ssl
import subprocess
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import pytest

from tails_cloner.download import (
    DownloadCancelledError,
    DownloadTimeoutError,
    DownloadTruncatedError,
    RemoteImageRequest,
    VerifiedImageDownloader,
    fetch_checksum_text,
)


@contextmanager
def local_test_ca_https(tmp_path: Path, *, declared_length: int, body: bytes) -> Iterator[str]:
    """Real TLS/HTTP framing over loopback, with a generated disposable test CA."""
    key = tmp_path / "tls.key"
    cert = tmp_path / "tls.crt"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
         "-keyout", str(key), "-out", str(cert), "-days", "1", "-subj", "/CN=localhost",
         "-addext", "subjectAltName=DNS:localhost"],
        check=True, capture_output=True, timeout=15,
    )

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Length", str(declared_length))
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()

        def log_message(self, *_args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"https://localhost:{server.server_port}/checksum"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_real_local_https_test_ca_rejects_short_body(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with local_test_ca_https(tmp_path, declared_length=9, body=b"abc") as url:
        # Only the disposable loopback certificate is trusted in this process.
        monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "tls.crt"))
        with pytest.raises(DownloadTruncatedError, match="truncated"):
            fetch_checksum_text(url, use_torify=False)


class SlowResponse:
    headers = {"Content-Length": "9"}

    def __init__(self, *, delay: float = 0.3, payload: bytes = b"abc") -> None:
        self.delay = delay
        self.payload = payload

    def __enter__(self) -> SlowResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def geturl(self) -> str:
        return "https://example.invalid/test"

    def read(self, _size: int) -> bytes:
        time.sleep(self.delay)
        return self.payload


def test_checksum_slow_drip_deadline() -> None:
    started = time.monotonic()
    with (
        mock.patch("tails_cloner.download.open_https", return_value=SlowResponse()),
        mock.patch("tails_cloner.download.DOWNLOAD_METADATA_TIMEOUT_SECONDS", 0.04),
        pytest.raises(DownloadTimeoutError, match="deadline"),
    ):
        fetch_checksum_text("https://example.invalid/checksum", use_torify=False)
    assert time.monotonic() - started < 0.2


def test_truncated_checksum_content_length_is_rejected() -> None:
    with (
        mock.patch("tails_cloner.download.open_https", return_value=SlowResponse(delay=0)),
        pytest.raises(DownloadTruncatedError, match="truncated"),
    ):
        fetch_checksum_text("https://example.invalid/checksum", use_torify=False)


def test_truncated_signature_content_length_is_rejected(tmp_path: Path) -> None:
    downloader = VerifiedImageDownloader(use_torify=lambda: False)
    destination = tmp_path / "signature.part"
    with (
        mock.patch("tails_cloner.download.open_https", return_value=SlowResponse(delay=0)),
        pytest.raises(DownloadTruncatedError, match="truncated"),
    ):
        downloader._download_signature("https://example.invalid/sig", destination)
    assert not destination.exists()


def test_signature_slow_drip_deadline(tmp_path: Path) -> None:
    downloader = VerifiedImageDownloader(use_torify=lambda: False)
    started = time.monotonic()
    with (
        mock.patch("tails_cloner.download.open_https", return_value=SlowResponse()),
        mock.patch("tails_cloner.download.DOWNLOAD_METADATA_TIMEOUT_SECONDS", 0.04),
        pytest.raises(DownloadTimeoutError, match="deadline"),
    ):
        downloader._download_signature("https://example.invalid/signature", tmp_path / "sig")
    assert time.monotonic() - started < 0.2
    assert not (tmp_path / "sig").exists()


def test_image_connect_deadline(tmp_path: Path) -> None:
    downloader = VerifiedImageDownloader(use_torify=lambda: False)

    def block_open(_url: str, *, timeout: float) -> SlowResponse:
        _ = timeout
        time.sleep(0.3)
        return SlowResponse(delay=0)

    started = time.monotonic()
    with (
        mock.patch("tails_cloner.download.open_https", side_effect=block_open),
        mock.patch("tails_cloner.download.DOWNLOAD_TOTAL_TIMEOUT_SECONDS", 0.04),
        pytest.raises(DownloadTimeoutError, match="deadline"),
    ):
        downloader._download_image("https://example.invalid/image", tmp_path / "image", None)
    assert time.monotonic() - started < 0.2


def test_image_slow_drip_read_deadline_and_no_late_writes(tmp_path: Path) -> None:
    destination = tmp_path / "image.part"
    downloader = VerifiedImageDownloader(use_torify=lambda: False)
    with (
        mock.patch("tails_cloner.download.open_https", return_value=SlowResponse(delay=0.3)),
        mock.patch("tails_cloner.download.DOWNLOAD_TOTAL_TIMEOUT_SECONDS", 0.04),
        pytest.raises(DownloadTimeoutError),
    ):
        downloader._download_image("https://example.invalid/image", destination, None)
    size_at_timeout = destination.stat().st_size
    time.sleep(0.35)
    assert destination.stat().st_size == size_at_timeout == 0


def test_callback_cancellation_stays_typed(tmp_path: Path) -> None:
    downloader = VerifiedImageDownloader(use_torify=lambda: False)

    def cancel(_progress: object) -> None:
        raise DownloadCancelledError("cancelled by user")

    with (
        mock.patch("tails_cloner.download.open_https", return_value=SlowResponse(delay=0)),
        pytest.raises(DownloadCancelledError, match="cancelled by user"),
    ):
        downloader._download_image("https://example.invalid/image", tmp_path / "part", cancel)


def test_metadata_consumes_shared_image_deadline(tmp_path: Path) -> None:
    """Two individually quick metadata stages cannot reset the total budget."""
    downloader = VerifiedImageDownloader(use_torify=lambda: False)
    image = tmp_path / "new.img"
    request = RemoteImageRequest(
        version="test", image_url="https://example.invalid/new.img",
        checksum_url="https://example.invalid/checksum",
        signature_url="https://example.invalid/signature", destination=image,
    )

    def checksum(_url: str, _deadline: float | None = None) -> str:
        time.sleep(0.035)
        return f"{'0' * 64}  new.img\n"

    def signature(_url: str, path: Path, _deadline: float | None = None) -> None:
        time.sleep(0.035)
        path.write_bytes(b"signature")

    with (
        mock.patch.object(downloader, "_read_checksum", side_effect=checksum),
        mock.patch.object(downloader, "_download_signature", side_effect=signature),
        mock.patch("tails_cloner.download.DOWNLOAD_TOTAL_TIMEOUT_SECONDS", 0.05),
        mock.patch.object(downloader, "_download_image", wraps=downloader._download_image),
        pytest.raises(DownloadTimeoutError, match="deadline"),
    ):
        downloader.download(request)
    assert not image.exists()
    assert not list(tmp_path.glob("*.part"))


def test_expired_deadline_after_verification_cannot_promote_image(tmp_path: Path) -> None:
    """Even successful crypto verification must not promote after expiry."""
    downloader = VerifiedImageDownloader(use_torify=lambda: False)
    destination = tmp_path / "new.img"
    request = RemoteImageRequest(
        version="test", image_url="https://example.invalid/new.img",
        checksum_url="https://example.invalid/checksum",
        signature_url="https://example.invalid/signature", destination=destination,
    )

    def signature(_url: str, path: Path, _deadline: float | None = None) -> None:
        path.write_bytes(b"signature")

    def image(_url: str, path: Path, _callback: object, _deadline: float | None = None) -> None:
        path.write_bytes(b"image")

    def verify(_path: Path, **_kwargs: object) -> tuple[str, str]:
        time.sleep(0.06)
        return "a" * 64, "fingerprint"

    with (
        mock.patch.object(downloader, "_read_checksum", return_value=f"{'a' * 64}  new.img\n"),
        mock.patch.object(downloader, "_download_signature", side_effect=signature),
        mock.patch.object(downloader, "_download_image", side_effect=image),
        mock.patch.object(downloader, "_verify_candidate", side_effect=verify),
        mock.patch("tails_cloner.download.DOWNLOAD_TOTAL_TIMEOUT_SECONDS", 0.04),
        pytest.raises(DownloadTimeoutError),
    ):
        downloader.download(request)
    assert not destination.exists()
    assert not list(tmp_path.glob("*.part"))
