import hashlib
import tkinter as tk
import unittest
from io import BytesIO
from pathlib import Path
from queue import SimpleQueue
from tempfile import TemporaryDirectory
from unittest import mock

from tails_cloner.app import TailsClonerApp
from tails_cloner.models import SourceMode


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._stream = BytesIO(payload)

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        return self._stream.read(size)


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


class _FakeWidget:
    def __init__(self) -> None:
        self.state_calls = []

    def state(self, value) -> None:
        self.state_calls.append(value)


class _FakeButton(_FakeLabel):
    def __init__(self, text: str) -> None:
        super().__init__()
        self.kwargs["text"] = text

    def cget(self, name: str):
        return self.kwargs[name]


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
        self.assertIn("Persistent Storage, if present, is kept intact", app._upgrade_warning_text())

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
            {
                "state": type("State", (), {"running_tails_device": "", "devices": [], "source_mode": object()})(),
                "annotate_device_selection_state": lambda _self: None,
            },
        )()
        app.device_var = _FakeStringVar("")
        app.device_combo = {}
        app._device_labels = {}
        app._update_device_warnings_and_button = lambda: None
        app._sync_upgrade_plan = lambda: None

        app._on_action_mode_changed()

        self.assertIn("Persistent Storage, if present, is kept intact", app.install_warning_label.kwargs["text"])
        self.assertEqual(app.install_warning_label.kwargs["foreground"], "#2e7d32")

    def test_running_source_option_stays_enabled_after_switching_to_local_mode(self) -> None:
        app = TailsClonerApp.__new__(TailsClonerApp)
        state = type(
            "State",
            (),
            {
                "running_tails_version": "7.7.2",
                "running_tails_device": "/dev/sdb1",
                "running_tails_available": True,
                "source_mode": SourceMode.LOCAL,
            },
        )()
        app.controller = type("Controller", (), {"state": state})()
        app.running_tails_version_var = _FakeStringVar()
        app.running_tails_device_var = _FakeStringVar()
        app.source_mode_var = _FakeStringVar()
        app.source_running_radio = _FakeWidget()
        app.attached_source_device_entry = _FakeWidget()
        app.attached_source_mount_entry = _FakeWidget()
        app.attached_source_validate_button = _FakeWidget()
        app.image_entry = _FakeWidget()
        app.browse_button = _FakeWidget()
        app.download_button = _FakeWidget()

        app._sync_source_mode()

        self.assertEqual(app.source_running_radio.state_calls[-1], ["!disabled"])
        self.assertEqual(app.source_mode_var.get(), "local")

    def test_idle_device_scan_does_not_overwrite_operation_status(self) -> None:
        app = TailsClonerApp.__new__(TailsClonerApp)
        state = type(
            "State",
            (),
            {"versions_loading": False, "devices_loading": False, "devices": [object()]},
        )()
        app.controller = type("Controller", (), {"state": state})()
        app.version_status_label = _FakeLabel()
        app.device_status_label = _FakeLabel()
        app.device_status_label.kwargs["text"] = "Existing Tails installation detected."

        app._sync_loading_labels()

        self.assertEqual(app.device_status_label.kwargs["text"], "Existing Tails installation detected.")

    def test_worker_ui_callbacks_run_only_when_main_loop_drains_queue(self) -> None:
        app = TailsClonerApp.__new__(TailsClonerApp)
        app._ui_events = SimpleQueue()
        calls = []

        app._queue_ui(lambda: calls.append("done"))

        self.assertEqual(calls, [])
        app._drain_ui_events()
        self.assertEqual(calls, ["done"])

    def test_close_is_blocked_while_destructive_write_is_running(self) -> None:
        app = TailsClonerApp.__new__(TailsClonerApp)
        app._write_in_progress = True
        controller = mock.Mock()
        app.controller = controller
        app.destroy = mock.Mock()

        with mock.patch("tails_cloner.app.messagebox.showwarning") as showwarning:
            app._on_close()

        showwarning.assert_called_once()
        controller.shutdown.assert_not_called()
        app.destroy.assert_not_called()

    def test_verified_remote_download_is_promoted_atomically(self) -> None:
        app = TailsClonerApp.__new__(TailsClonerApp)
        app._ui_events = SimpleQueue()
        image = b"verified Tails image"
        digest = hashlib.sha256(image).hexdigest()
        successes: list[tuple[Path, str, str]] = []
        failures: list[str] = []
        app._apply_remote_download_success = lambda path, name, actual: successes.append((path, name, actual))
        app._apply_remote_download_failure = failures.append

        with (
            TemporaryDirectory() as tmpdir,
            mock.patch("tails_cloner.app.Path.home", return_value=Path(tmpdir)),
            mock.patch(
                "tails_cloner.app.urlopen",
                side_effect=[
                    _FakeResponse(f"{digest}  tails.img\n".encode()),
                    _FakeResponse(image),
                ],
            ),
        ):
            app._download_selected_remote_image(
                "https://example.invalid/tails.img",
                "https://example.invalid/tails.img.sha256",
                "tails.img",
            )
            app._drain_ui_events()

            target = Path(tmpdir) / ".cache/tails-cloner-clone/downloads/tails.img"
            self.assertEqual(target.read_bytes(), image)
            self.assertEqual(list(target.parent.glob(".*.part")), [])

        self.assertEqual(successes[0][1:], ("tails.img", digest))
        self.assertEqual(failures, [])

    def test_failed_remote_verification_keeps_existing_cached_image(self) -> None:
        app = TailsClonerApp.__new__(TailsClonerApp)
        app._ui_events = SimpleQueue()
        failures: list[str] = []
        app._apply_remote_download_success = lambda *_args: self.fail("verification unexpectedly succeeded")
        app._apply_remote_download_failure = failures.append

        with TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / ".cache/tails-cloner-clone/downloads/tails.img"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"previous verified image")
            with (
                mock.patch("tails_cloner.app.Path.home", return_value=Path(tmpdir)),
                mock.patch(
                    "tails_cloner.app.urlopen",
                    side_effect=[
                        _FakeResponse(f"{'0' * 64}  tails.img\n".encode()),
                        _FakeResponse(b"corrupt replacement"),
                    ],
                ),
            ):
                app._download_selected_remote_image(
                    "https://example.invalid/tails.img",
                    "https://example.invalid/tails.img.sha256",
                    "tails.img",
                )
                app._drain_ui_events()

            self.assertEqual(target.read_bytes(), b"previous verified image")
            self.assertEqual(list(target.parent.glob(".*.part")), [])

        self.assertEqual(len(failures), 1)
        self.assertIn("SHA-256 mismatch", failures[0])

    def test_upgrade_plan_names_attached_live_source(self) -> None:
        app = TailsClonerApp.__new__(TailsClonerApp)
        state = type(
            "State",
            (),
            {
                "source_mode": SourceMode.ATTACHED,
                "attached_live_source_version": "7.7.2",
                "attached_live_source_device": "/dev/sdc",
            },
        )()
        app.controller = type("Controller", (), {"state": state})()
        app.device_var = _FakeStringVar("target")
        app._device_labels = {"target": "/dev/sdb"}
        app.clone_button = _FakeButton("Upgrade")
        app.upgrade_plan_var = _FakeStringVar()

        app._sync_upgrade_plan()

        self.assertIn("attached Tails 7.7.2 from /dev/sdc", app.upgrade_plan_var.get())
        self.assertIn("Persistent Storage preserved if present", app.upgrade_plan_var.get())


if __name__ == "__main__":
    unittest.main()
