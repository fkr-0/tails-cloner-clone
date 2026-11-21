"""Local TLS transport probes, not evidence of Tor anonymity.

All sockets bind loopback; the test CA/certificate is ephemeral.
"""

from __future__ import annotations

import shutil
import ssl
import subprocess
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import URLError

import pytest

from tails_cloner.download import open_https
from tails_cloner.remote_index import open_catalog_https


@pytest.fixture
def local_certificate(tmp_path: Path) -> tuple[Path, Path]:
    if not shutil.which("openssl"):
        pytest.skip("openssl required for ephemeral local test CA")
    cert, key = tmp_path / "local-ca.pem", tmp_path / "local-key.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
         "-days", "1", "-subj", "/CN=localhost", "-addext",
         "subjectAltName=DNS:localhost", "-keyout", str(key), "-out", str(cert)],
        check=True, capture_output=True, timeout=15,
    )
    return cert, key


@contextmanager
def local_https(cert: Path, key: Path) -> Iterator[tuple[str, list[str]]]:
    requests: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            requests.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"test catalog")

        def log_message(self, *_args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(cert), str(key))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"https://localhost:{server.server_port}/catalog", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_catalog_tls_rejects_untrusted_then_accepts_explicit_local_ca(
    local_certificate: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    cert, key = local_certificate
    # No ambient proxy and no outbound socket: local hostname resolves to loopback.
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1")
    with local_https(cert, key) as (url, requests):
        with (
            pytest.raises((ssl.SSLCertVerificationError, URLError)),
            open_catalog_https(url, 3, ssl.create_default_context()) as response,
        ):
            response.read()
        assert requests == []  # TLS rejection happens before HTTP request delivery.

        trusted = ssl.create_default_context(cafile=str(cert))
        with open_catalog_https(url, 3, trusted) as response:
            assert response.read() == b"test catalog"
        assert requests == ["/catalog"]


def test_image_opener_does_not_disable_tls_verification(
    local_certificate: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    cert, key = local_certificate
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1")
    with local_https(cert, key) as (url, requests):
        with (
            pytest.raises((ssl.SSLCertVerificationError, URLError)),
            open_https(url, timeout=3) as response,
        ):
            response.read()
        assert requests == []
