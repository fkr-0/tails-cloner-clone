"""Headless GUI feedback contract for a successful image write with failed customization."""

from __future__ import annotations

from unittest.mock import Mock

from pytest import MonkeyPatch

from tails_cloner.app import TailsClonerApp


def _check_warning(monkeypatch: MonkeyPatch, reason: str) -> None:
    app = TailsClonerApp.__new__(TailsClonerApp)
    app._write_in_progress = True
    methods = ["_set_write_controls_locked", "_finish_clone_progress",
               "_sync_source_mode", "_sync_experimental_state",
               "_update_device_warnings_and_button"]
    mocks: dict[str, Mock] = {}
    for method in methods:
        mocked = Mock()
        monkeypatch.setattr(TailsClonerApp, method, mocked)
        mocks[method] = mocked
    warning = Mock()
    monkeypatch.setattr("tails_cloner.app.messagebox.showwarning", warning)

    app._finish_write_partial_success(reason)

    assert app._write_in_progress is False
    mocks["_set_write_controls_locked"].assert_called_once_with(False)
    progress = mocks["_finish_clone_progress"].call_args.args[0]
    assert "Image write completed" in progress
    assert "customization failed" in progress
    assert reason in progress
    title, body = warning.call_args.args
    assert "customization failed" in title
    assert "original image write was successful" in body
    assert "post-write changes were not completed" in body
    assert reason in body
    assert "Check the target device state" in body
    for method in methods[2:]:
        mocks[method].assert_called_once_with()


def test_customization_failure_warning() -> None:
    with MonkeyPatch.context() as patcher:
        _check_warning(patcher, "boot-loader change rejected")


def test_uncertain_unmount_warning() -> None:
    with MonkeyPatch.context() as patcher:
        _check_warning(patcher, "unmount failed; device state unknown")
