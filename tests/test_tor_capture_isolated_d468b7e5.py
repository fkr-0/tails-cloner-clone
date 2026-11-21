"""Capture curl's SOCKS handshake on loopback without any external request.

The fake proxy deliberately refuses CONNECT; it cannot relay packets.
"""

from __future__ import annotations

import shutil
import socketserver
import subprocess
import threading

import pytest

from tails_cloner.network import curl_proxy_isolated_env, tor_curl_options


class _CaptureProxy(socketserver.TCPServer):
    allow_reuse_address = True

    def __init__(self) -> None:
        self.requests: list[tuple[int, str, int]] = []
        super().__init__(("127.0.0.1", 0), _Handler)


class _Handler(socketserver.BaseRequestHandler):
    def _read(self, count: int) -> bytes:
        result = bytearray()
        while len(result) < count:
            chunk = self.request.recv(count - len(result))
            if not chunk:
                raise EOFError("incomplete SOCKS request")
            result.extend(chunk)
        return bytes(result)

    def handle(self) -> None:
        self.request.settimeout(3)
        version, methods = self._read(2)
        assert version == 5
        self._read(methods)
        self.request.sendall(b"\x05\x00")
        version, command, reserved, address_type = self._read(4)
        assert (version, reserved) == (5, 0)
        if address_type == 3:
            hostname = self._read(self._read(1)[0]).decode("ascii")
        elif address_type == 1:
            hostname = ".".join(str(value) for value in self._read(4))
        else:
            raise AssertionError(f"unexpected address type {address_type}")
        port = int.from_bytes(self._read(2), "big")
        self.server.requests.append((command, hostname, port))  # type: ignore[attr-defined]
        # Explicit refusal: this server never initiates a connection to the host.
        self.request.sendall(b"\x05\x01\x00\x01\x00\x00\x00\x00\x00\x00")


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl unavailable")
def test_curl_socks5h_sends_hostname_to_proxy_without_external_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://invalid-proxy.invalid:3128")
    monkeypatch.setenv("ALL_PROXY", "http://invalid-proxy.invalid:3128")
    monkeypatch.setenv("NO_PROXY", "*")
    with _CaptureProxy() as proxy:
        worker = threading.Thread(target=proxy.handle_request, daemon=True)
        worker.start()
        port = proxy.server_address[1]
        # Preserve production option semantics while substituting a private port.
        opts = tor_curl_options(9050)
        opts[1] = f"socks5h://127.0.0.1:{port}"
        result = subprocess.run(
            ["curl", "--disable", "-fsSL", *opts, "--max-time", "3",
             "https://capture-only.invalid/path"],
            env=curl_proxy_isolated_env(), capture_output=True, text=True,
            timeout=5, check=False,
        )
        worker.join(timeout=3)
        assert not worker.is_alive()
    assert result.returncode != 0  # Proxy failure is intentional.
    assert proxy.requests == [(1, "capture-only.invalid", 443)]


@pytest.mark.parametrize("port", [9050, 9150])
def test_production_options_pin_remote_dns_and_https(port: int) -> None:
    options = tor_curl_options(port)
    assert options[options.index("--proxy") + 1] == f"socks5h://127.0.0.1:{port}"
    assert options[options.index("--noproxy") + 1] == ""
    assert options[options.index("--proto") + 1] == "=https"
    assert options[options.index("--proto-redir") + 1] == "=https"
