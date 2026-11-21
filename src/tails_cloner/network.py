from __future__ import annotations

import os
import shutil
import socket
import ssl
import subprocess
from urllib.request import urlopen

TOR_SOCKS_HOST = "127.0.0.1"
TOR_SOCKS_PORTS = (9050, 9150)
MAX_CATALOG_BYTES = 1024 * 1024


class TorUnavailableError(ConnectionError):
    """An explicitly required Tor transport is not ready."""


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
    available = torify_available() and tor_socks_port() is not None
    if os.environ.get("TAILS_CLONER_TOR_MODE", "auto").casefold() == "required" and not available:
        raise TorUnavailableError("Tor is required but torify or the local SOCKS listener is unavailable")
    return available


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
            "--max-filesize",
            str(MAX_CATALOG_BYTES),
            "--noproxy",
            "*",
            url,
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    if len(result.stdout.encode("utf-8")) > MAX_CATALOG_BYTES:
        raise ValueError("Remote catalog response is too large")
    return result.stdout
