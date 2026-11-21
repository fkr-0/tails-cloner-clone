from __future__ import annotations

import socket
import subprocess
from unittest.mock import Mock

import pytest

from tails_cloner import network


def test_tor_probe_rejects_open_non_socks_listener(monkeypatch) -> None:
    peer = Mock()
    peer.recv.return_value = b"HTTP/1.1 200 OK"
    context = Mock()
    context.__enter__ = Mock(return_value=peer)
    context.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(socket, "create_connection", lambda *_args, **_kwargs: context)
    assert network._probe_tor_socks(9050, 0.1) is False


def test_tor_probe_requires_tor_resolve_extension(monkeypatch) -> None:
    class FakeSocket:
        def __init__(self, chunks: list[bytes]) -> None:
            self.chunks = chunks
            self.sent: list[bytes] = []

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def settimeout(self, _timeout):
            pass

        def sendall(self, payload):
            self.sent.append(payload)

        def recv(self, count):
            chunk = self.chunks.pop(0)
            assert len(chunk) <= count
            return chunk

    generic = FakeSocket([b"\x05\x00", b"\x05\x07\x00\x01"])
    monkeypatch.setattr(socket, "create_connection", lambda *_args, **_kwargs: generic)
    assert network._probe_tor_socks(9050, 0.1) is False
    assert generic.sent[1].startswith(b"\x05\xf0\x00\x03")

    tor = FakeSocket([b"\x05\x00", b"\x05\x00\x00\x01", b"\x7f\x00\x00\x01\x00\x00"])
    monkeypatch.setattr(socket, "create_connection", lambda *_args, **_kwargs: tor)
    assert network._probe_tor_socks(9050, 0.1) is True


def test_required_tor_does_not_fall_back_to_direct(monkeypatch) -> None:
    monkeypatch.setenv("TAILS_CLONER_TOR_MODE", "required")
    monkeypatch.setattr(network, "torify_available", lambda: False)
    monkeypatch.setattr(network, "tor_socks_port", lambda: None)
    with pytest.raises(network.TorUnavailableError, match="Tor is required"):
        network.should_use_torify()


def test_should_use_torify_checks_mode_and_endpoint(monkeypatch) -> None:
    monkeypatch.setattr(network, "torify_available", lambda: True)
    monkeypatch.setattr(network, "tor_socks_port", lambda: 9150)
    assert network.should_use_torify() is True
    monkeypatch.setenv("TAILS_CLONER_TOR_MODE", "off")
    assert network.should_use_torify() is False
    monkeypatch.setenv("TAILS_CLONER_TOR_MODE", "typo")
    with pytest.raises(ValueError, match="Invalid"):
        network.should_use_torify()


def test_fetch_text_torified_uses_selected_socks5_remote_dns_and_clean_env(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_run(command, **kwargs):
        seen.update(command=command, **kwargs)
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr(network, "tor_socks_port", lambda: 9150)
    monkeypatch.setattr(network, "torify_available", lambda: True)
    monkeypatch.setenv("HTTPS_PROXY", "http://untrusted.invalid:3128")
    monkeypatch.setenv("ALL_PROXY", "socks5://untrusted.invalid:1080")
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert network.fetch_text_torified("https://example.invalid", 11) == "ok"
    command = seen["command"]
    assert command[:3] == ["curl", "--disable", "-fsSL"]
    assert command[command.index("--proxy") + 1] == "socks5h://127.0.0.1:9150"
    assert command[command.index("--noproxy") + 1] == ""
    assert command[command.index("--proto") + 1] == "=https"
    assert command[command.index("--proto-redir") + 1] == "=https"
    assert "torify" not in command
    assert "HTTPS_PROXY" not in seen["env"]
    assert "ALL_PROXY" not in seen["env"]
    assert "NO_PROXY" not in seen["env"]


def test_fetch_text_required_transport_failure_does_not_retry_direct(monkeypatch) -> None:
    monkeypatch.setattr(network, "tor_socks_port", lambda: None)
    monkeypatch.setattr(network, "torify_available", lambda: True)
    monkeypatch.setattr(network, "fetch_text_direct", lambda *_: pytest.fail("direct fallback"))
    with pytest.raises(network.TorUnavailableError):
        network.fetch_text_torified("https://example.invalid", 11)


def test_tor_curl_options_rejects_nonstandard_port() -> None:
    with pytest.raises(network.TorUnavailableError):
        network.tor_curl_options(1080)
