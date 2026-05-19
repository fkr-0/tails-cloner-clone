import tkinter as tk
import unittest

from tails_cloner.app import TailsClonerApp


class _FakeTk:
    def __init__(self, should_fail: bool = False) -> None:
        self.calls = []
        self.should_fail = should_fail

    def call(self, *args):
        self.calls.append(args)
        if self.should_fail:
            raise tk.TclError("wm class unsupported")
        return ""


class _FakeStringVar:
    def __init__(self, value: str = "") -> None:
        self.value = value

    def get(self) -> str:
        return self.value

    def set(self, value: str) -> None:
        self.value = value


class _FakeLabel:
    def __init__(self) -> None:
        self.kwargs = {}

    def config(self, **kwargs) -> None:
        self.kwargs.update(kwargs)


class AppWindowClassTests(unittest.TestCase):
    def test_set_window_class_uses_tcl_wm_class(self) -> None:
        app = TailsClonerApp.__new__(TailsClonerApp)
        app.tk = _FakeTk()
        app._w = "."

        app._set_window_class()

        self.assertEqual(app.tk.calls, [("wm", "class", ".", "tails-cloner-clone")])

    def test_set_window_class_handles_tcl_error(self) -> None:
        app = TailsClonerApp.__new__(TailsClonerApp)
        app.tk = _FakeTk(should_fail=True)
        app._w = "."

        app._set_window_class()

        self.assertEqual(app.tk.calls, [("wm", "class", ".", "tails-cloner-clone")])

    def test_action_warning_copy_distinguishes_destructive_install_from_upgrade(self) -> None:
        app = TailsClonerApp.__new__(TailsClonerApp)

        self.assertIn("All data", app._install_warning_text())
        self.assertIn("Persistent Storage", app._install_warning_text())
        self.assertIn("permanently lost", app._install_warning_text())
        self.assertIn("Persistent Storage is kept intact", app._upgrade_warning_text())

    def test_action_mode_accepts_new_upgrade_value_and_legacy_update_value(self) -> None:
        app = TailsClonerApp.__new__(TailsClonerApp)
        app.action_mode_var = _FakeStringVar("upgrade")
        self.assertTrue(app._upgrade_mode_enabled())

        app.action_mode_var.set("update")
        self.assertTrue(app._upgrade_mode_enabled())

        app.action_mode_var.set("install")
        self.assertFalse(app._upgrade_mode_enabled())

    def test_action_mode_change_updates_visible_warning_copy(self) -> None:
        app = TailsClonerApp.__new__(TailsClonerApp)
        app.action_mode_var = _FakeStringVar("upgrade")
        app.install_warning_label = _FakeLabel()
        app._last_devices_snapshot = ()
        app.controller = type(
            "Controller",
            (),
            {"state": type("State", (), {"running_tails_device": "", "devices": [], "source_mode": object()})()},
        )()
        app.device_var = _FakeStringVar("")
        app.device_combo = {}
        app._device_labels = {}
        app._update_device_warnings_and_button = lambda: None

        app._on_action_mode_changed()

        self.assertIn("Persistent Storage is kept intact", app.install_warning_label.kwargs["text"])
        self.assertEqual(app.install_warning_label.kwargs["foreground"], "#2e7d32")


if __name__ == "__main__":
    unittest.main()
