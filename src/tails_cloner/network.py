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
_TOR_RESOLVE_HOST = b"check.torproject.org"
_PROXY_ENV_KEYS = frozenset({
    "http_proxy", "https_proxy", "all_proxy", "no_proxy",
    "ftp_proxy", "proxy", "curl_proxy",
})


class TorUnavailableError(ConnectionError):
    """An explicitly required Tor transport is not ready."""


def _receive_exact(sock: socket.socket, count: int) -> bytes:
    data = bytearray()
    while len(data) < count:
        chunk = sock.recv(count - len(data))
        if not chunk:
            raise OSError("SOCKS peer closed before completing the response")
        data.extend(chunk)
    return bytes(data)


def _probe_tor_socks(port: int, timeout_seconds: float) -> bool:
    """Check SOCKS5 no-auth and Tor's nonstandard RESOLVE (0xF0) extension.

    A TCP listener or generic SOCKS proxy is insufficient. This checks
    protocol behavior, not cryptographic identity of the local process.
    """
    try:
        with socket.create_connection((TOR_SOCKS_HOST, port), timeout=timeout_seconds) as sock:
            sock.settimeout(timeout_seconds)
            sock.sendall(b"\x05\x01\x00")
            if _receive_exact(sock, 2) != b"\x05\x00":
                return False
            sock.sendall(b"\x05\xf0\x00\x03" + bytes([len(_TOR_RESOLVE_HOST)]) + _TOR_RESOLVE_HOST + b"\x00\x00")
            header = _receive_exact(sock, 4)
            if header[:3] != b"\x05\x00\x00":
                return False
            address_type = header[3]
            if address_type == 1:
                _receive_exact(sock, 4 + 2)
            elif address_type == 4:
                _receive_exact(sock, 16 + 2)
            elif address_type == 3:
                size = _receive_exact(sock, 1)[0]
                _receive_exact(sock, size + 2)
            else:
                return False
            return True
    except (OSError, ValueError):
        return False


def tor_socks_port(timeout_seconds: float = 2.0) -> int | None:
    for port in TOR_SOCKS_PORTS:
        if _probe_tor_socks(port, timeout_seconds):
            return port
    return None


def torify_available() -> bool:
    """Compatibility name: curl is now used with explicit SOCKS5 routing."""
    return shutil.which("curl") is not None


def should_use_torify() -> bool:
    mode = os.environ.get("TAILS_CLONER_TOR_MODE", "auto").casefold()
    if mode not in {"auto", "required", "off"}:
        raise ValueError("Invalid TAILS_CLONER_TOR_MODE (expected auto, required, off)")
    if mode == "off":
        return False
    available = torify_available() and tor_socks_port() is not None
    if mode == "required" and not available:
        raise TorUnavailableError("Tor is required but a Tor-compatible SOCKS5 endpoint or curl is unavailable")
    return available


def tor_curl_options(port: int) -> list[str]:
    if port not in TOR_SOCKS_PORTS:
        raise TorUnavailableError("Unvalidated Tor SOCKS port")
    return [
        "--proxy", f"socks5h://{TOR_SOCKS_HOST}:{port}",
        "--noproxy", "",
        "--proto", "=https",
        "--proto-redir", "=https",
    ]


def curl_proxy_isolated_env() -> dict[str, str]:
    """Prevent proxy environment variables from overriding explicit routing."""
    return {key: value for key, value in os.environ.items() if key.casefold() not in _PROXY_ENV_KEYS}


def fetch_text_direct(url: str, timeout_seconds: int, ssl_context: ssl.SSLContext) -> str:
    with urlopen(url, timeout=timeout_seconds, context=ssl_context) as response:  # noqa: S310 - app-managed catalog URL
        payload: bytes = response.read()
    return payload.decode("utf-8", errors="replace")


def fetch_text_torified(url: str, timeout_seconds: int) -> str:
    port = tor_socks_port()
    if port is None or not torify_available():
        raise TorUnavailableError("Tor SOCKS5 routing is unavailable")
    result = subprocess.run(
        [
            "curl",
            "--disable",
            "-fsSL",
            *tor_curl_options(port),
            "--max-time",
            str(timeout_seconds),
            "--max-filesize",
            str(MAX_CATALOG_BYTES),
            url,
        ],
        check=True,
        text=True,
        capture_output=True,
        env=curl_proxy_isolated_env(),
    )
    if len(result.stdout.encode("utf-8")) > MAX_CATALOG_BYTES:
        raise ValueError("Remote catalog response is too large")
    return result.stdout
