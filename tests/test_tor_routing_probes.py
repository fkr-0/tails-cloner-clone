"""Loopback-only transport falsification probes; never an anonymity certificate."""
from __future__ import annotations

import socket
from contextlib import closing
from email.message import Message
from unittest.mock import patch
from urllib.request import ProxyHandler, Request, build_opener

import pytest

from tails_cloner import network
from tails_cloner.download import HttpsOnlyRedirectHandler
from tails_cloner.remote_index import HttpsCatalogRedirectHandler


def test_fake_loopback_listener_is_not_identified_as_tor() -> None:
    with closing(socket.socket()) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        with patch.object(network, "TOR_SOCKS_PORTS", (server.getsockname()[1],)):
            # A bare TCP listener never answers the SOCKS5 greeting.
            assert network.tor_socks_port(timeout_seconds=0.2) is None
    # Protocol behavior is checked; this is still not proof of process identity.


def test_required_mode_fails_closed_without_torify(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAILS_CLONER_TOR_MODE", "required")
    with (
        patch.object(network, "torify_available", return_value=False),
        pytest.raises(network.TorUnavailableError),
    ):
        network.should_use_torify()


def test_auto_mode_downgrades_to_direct(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAILS_CLONER_TOR_MODE", "auto")
    with patch.object(network, "torify_available", return_value=False):
        assert network.should_use_torify() is False


def test_detected_socks_port_is_passed_explicitly_to_curl() -> None:
    with (
        patch.object(network, "tor_socks_port", return_value=9150),
        patch.object(network, "torify_available", return_value=True),
        patch.object(network.subprocess, "run") as run,
    ):
        run.return_value.stdout = "ok"
        network.fetch_text_torified("https://example.invalid/catalog", 3)
    argv = run.call_args.args[0]
    assert argv[0] == "curl" and "torify" not in argv
    assert argv[argv.index("--proxy") + 1] == "socks5h://127.0.0.1:9150"
    assert argv[argv.index("--proto") + 1] == "=https"
    assert argv[argv.index("--proto-redir") + 1] == "=https"
    assert argv[argv.index("--noproxy") + 1] == ""


@pytest.mark.parametrize("handler", [HttpsOnlyRedirectHandler, HttpsCatalogRedirectHandler])
def test_https_downgrade_rejected_but_cross_host_https_allowed(handler: type) -> None:
    instance = handler()
    with pytest.raises(ValueError, match="HTTPS"):
        instance.redirect_request(Request("https://origin.invalid/a"), None, 302,
                                  "Found", Message(), "http://other.invalid/b")
    redirect = instance.redirect_request(Request("https://origin.invalid/a"), None,
                                         302, "Found", Message(), "https://other.invalid/b")
    assert redirect is not None and redirect.full_url == "https://other.invalid/b"


def test_direct_opener_inherits_proxy_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:9")
    opener = build_opener(HttpsOnlyRedirectHandler())
    proxy_handlers = [h for h in opener.handlers if isinstance(h, ProxyHandler)]
    assert proxy_handlers and proxy_handlers[0].proxies["https"] == "http://127.0.0.1:9"
