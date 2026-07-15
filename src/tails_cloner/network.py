from __future__ import annotations

import shutil
import socket
import ssl
import subprocess
from urllib.error import URLError
from urllib.request import urlopen

TOR_SOCKS_HOST = "127.0.0.1"
TOR_SOCKS_PORTS = (9050, 9150)


def tor_socks_port(timeout_seconds: float = 0.2) -> int | None:
    for port in TOR_SOCKS_PORTS:
        try:
            with socket.create_connection((TOR_SOCKS_HOST, port), timeout=timeout_seconds):
                return port
        except OSError:
            continue
    return None


def torify_available() -> bool:
    return shutil.which("torify") is not None


def should_use_torify() -> bool:
    return torify_available() and tor_socks_port() is not None


def fetch_text_direct(url: str, timeout_seconds: int, ssl_context: ssl.SSLContext) -> str:
    with urlopen(url, timeout=timeout_seconds, context=ssl_context) as response:  # noqa: S310 - app-managed catalog URL
        payload: bytes = response.read()
    return payload.decode("utf-8", errors="replace")


def fetch_text_torified(url: str, timeout_seconds: int) -> str:
    result = subprocess.run(
        [
            "torify",
            "curl",
            "-fsSL",
            "--proto",
            "=https",
            "--proto-redir",
            "=https",
            "--max-time",
            str(timeout_seconds),
            url,
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout


def is_cert_verification_error(error: Exception) -> bool:
    if isinstance(error, ssl.SSLCertVerificationError):
        return True
    if isinstance(error, URLError):
        reason = getattr(error, "reason", None)
        if isinstance(reason, ssl.SSLCertVerificationError):
            return True
        if isinstance(reason, str) and "CERTIFICATE_VERIFY_FAILED" in reason:
            return True
    return "CERTIFICATE_VERIFY_FAILED" in str(error)
