from __future__ import annotations

import subprocess

from tails_cloner import network


def test_should_use_torify_requires_binary_and_tor_port(monkeypatch) -> None:
    monkeypatch.setattr(network, "torify_available", lambda: True)
    monkeypatch.setattr(network, "tor_socks_port", lambda: 9050)
    assert network.should_use_torify() is True

    monkeypatch.setattr(network, "tor_socks_port", lambda: None)
    assert network.should_use_torify() is False


def test_fetch_text_torified_uses_torify_curl(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_run(command, check, text, capture_output):
        seen["command"] = command
        seen["check"] = check
        seen["text"] = text
        seen["capture_output"] = capture_output
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert network.fetch_text_torified("https://example.invalid", 11) == "ok"
    assert seen["command"] == ["torify", "curl", "-fsSL", "--max-time", "11", "https://example.invalid"]
