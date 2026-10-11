import inspect
import json
import tkinter as tk
import unittest
from collections.abc import Callable
from io import BytesIO, StringIO
from pathlib import Path
from queue import SimpleQueue
from tempfile import TemporaryDirectory
from typing import cast
from unittest import mock

from tails_cloner.app import TailsClonerApp
from tails_cloner.cli_events import EventSink, OutputMode
from tails_cloner.config import MIN_WINDOW_SIZE
from tails_cloner.download import DownloadProgress, DownloadVerificationError, VerifiedImageResult
from tails_cloner.models import BlockDevice, SourceMode, VersionAssets
from tails_cloner.planner import OperationKind, OperationSource, plan_operation


def _jsonl_events(buffer: StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in buffer.getvalue().splitlines()]


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

    configure = config


class _FakeProgressLog:
    def __init__(self, view: tuple[float, float]) -> None:
        self.view = view
        self.content = ""
        self.state = "disabled"
        self.seen: list[str] = []

    def yview(self) -> tuple[float, float]:
        return self.view

    def configure(self, **kwargs: str) -> None:
        self.state = kwargs.get("state", self.state)

    def index(self, _index: str) -> str:
        return "1.0" if not self.content else "1.1"

    def insert(self, _index: str, text: str) -> None:
        self.content += text

    def see(self, index: str) -> None:
        self.seen.append(index)


class _FakeProgressBar:
    def __init__(self) -> None:
        self.stop_calls = 0

    def stop(self) -> None:
        self.stop_calls += 1


class _FakeWidget:
    def __init__(self) -> None:
        self.state_calls: list[str] = []
        self.current_state = "normal"

    def configure(self, options: dict[str, str] | None = None, **kwargs: str) -> None:
        state = kwargs.get("state") or (options or {}).get("state")
        if state is not None:
            self.state_calls.append(state)
            self.current_state = state

    def cget(self, name: str) -> str:
        if name != "state":
            raise KeyError(name)
        return self.current_state


class _FakeButton(_FakeLabel):
    def __init__(self, text: str) -> None:
        super().__init__()
        self.kwargs["text"] = text

    def cget(self, name: str):
        return self.kwargs[name]


class AppWindowClassTests(unittest.TestCase):
    def test_minimum_window_height_allows_constrained_desktop_viewports(self) -> None:
        self.assertEqual(MIN_WINDOW_SIZE, (1000, 480))

    def test_notebook_tabs_use_scrollable_content_shells(self) -> None:
        build_source = inspect.getsource(TailsClonerApp._build_ui)

        self.assertIn('self._add_scrollable_tab(notebook, "Source")', build_source)
        self.assertIn('self._add_scrollable_tab(notebook, "Write")', build_source)

    def test_header_download_link_does_not_use_fixed_pixel_alignment(self) -> None:
        build_source = inspect.getsource(TailsClonerApp._build_ui)

        self.assertIn("info_row = ttk.Frame(header)", build_source)
        self.assertNotIn("padx=(370, 0)", build_source)

    def test_theme_application_uses_central_palette_for_native_and_ttk_widgets(self) -> None:
        theme_source = inspect.getsource(TailsClonerApp._apply_theme)

        self.assertIn("configure_ttk_style(style, palette)", theme_source)
        self.assertIn("configure_tk_option_database(self, palette)", theme_source)
        self.assertIn("configure_listbox_widget(self.versions_list, palette)", theme_source)
        self.assertIn("configure_combobox_popdown(self.device_combo, palette)", theme_source)
        self.assertIn("configure_canvas_widget(canvas, palette)", theme_source)
        self.assertIn("configure_text_widget(self.progress_log, palette)", theme_source)

    def test_app_does_not_reintroduce_scattered_hex_widget_colors(self) -> None:
        app_source = inspect.getsource(TailsClonerApp)

        self.assertNotRegex(app_source, r"#[0-9A-Fa-f]{6}")

    def test_download_link_is_keyboard_activatable(self) -> None:
        build_source = inspect.getsource(TailsClonerApp._build_ui)

        self.assertIn('takefocus=True', build_source)
        self.assertIn('bind("<Return>", self._open_downloads_link)', build_source)
        self.assertIn('bind("<space>", self._open_downloads_link)', build_source)

    def test_progress_log_autofollows_while_user_is_at_bottom(self) -> None:
        app = TailsClonerApp.__new__(TailsClonerApp)
        app.progress_label = _FakeLabel()
        app.progress_log = _FakeProgressLog((0.25, 1.0))

        app._set_progress_text("writing partition table")

        self.assertEqual(app.progress_label.kwargs["text"], "writing partition table")
        self.assertEqual(app.progress_log.content, "writing partition table")
        self.assertEqual(app.progress_log.seen, ["end"])
        self.assertEqual(app.progress_log.state, "disabled")

    def test_progress_log_does_not_fight_user_scrolling_back(self) -> None:
        app = TailsClonerApp.__new__(TailsClonerApp)
        app.progress_label = _FakeLabel()
        app.progress_log = _FakeProgressLog((0.0, 0.6))

        app._set_progress_text("syncing target")

        self.assertEqual(app.progress_log.content, "syncing target")
        self.assertEqual(app.progress_log.seen, [])

    def test_completed_progress_retains_history_for_review(self) -> None:
        app = TailsClonerApp.__new__(TailsClonerApp)
        app.progress_label = _FakeLabel()
        app.progress_log = _FakeProgressLog((0.0, 1.0))
        app.progress_bar = _FakeProgressBar()
        app._set_progress_text("writing system partition")

        app._finish_clone_progress("Installation complete")

        self.assertEqual(app.progress_bar.stop_calls, 1)
        self.assertEqual(app.progress_log.content, "writing system partition\nInstallation complete")
        self.assertEqual(app.progress_label.kwargs["text"], "Installation complete")

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
        app.clone_button = _FakeLabel()
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
        self.assertEqual(app.install_warning_label.kwargs["style"], "Success.TLabel")
        self.assertEqual(app.clone_button.kwargs["style"], "Accent.TButton")

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

        self.assertEqual(app.source_running_radio.state_calls[-1], "normal")
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

    def test_write_lock_disables_and_restores_all_mutable_controls(self) -> None:
        app = TailsClonerApp.__new__(TailsClonerApp)
        normal = _FakeWidget()
        readonly = _FakeWidget()
        readonly.current_state = "readonly"
        app._write_sensitive_widgets = cast(list[tk.Widget], [normal, readonly])
        app._write_control_states = []

        app._set_write_controls_locked(True)

        self.assertEqual(normal.current_state, "disabled")
        self.assertEqual(readonly.current_state, "disabled")

        app._set_write_controls_locked(False)

        self.assertEqual(normal.current_state, "normal")
        self.assertEqual(readonly.current_state, "readonly")

    def test_close_is_blocked_while_verified_download_is_running(self) -> None:
        app = TailsClonerApp.__new__(TailsClonerApp)
        app._write_in_progress = False
        app._download_in_progress = True
        controller = mock.Mock()
        app.controller = controller
        app.destroy = mock.Mock()

        with mock.patch("tails_cloner.app.messagebox.showwarning") as showwarning:
            app._on_close()

        showwarning.assert_called_once()
        controller.shutdown.assert_not_called()
        app.destroy.assert_not_called()

    def test_verified_remote_download_delegates_to_shared_service(self) -> None:
        app = TailsClonerApp.__new__(TailsClonerApp)
        app._ui_events = SimpleQueue()
        event_output = StringIO()
        app._operator_events = EventSink(
            command="gui",
            output_mode=OutputMode.JSONL,
            stdout=event_output,
            stderr=StringIO(),
        )
        digest = "a" * 64
        successes: list[tuple[Path, str, str, str, str, str]] = []
        failures: list[str] = []
        app._apply_remote_download_success = lambda *args: successes.append(args)
        app._apply_remote_download_failure = failures.append

        with TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / ".cache/tails-cloner-clone/downloads/tails.img"
            result = VerifiedImageResult(
                path=target,
                version="7.7.2",
                source_url="https://example.invalid/tails.img",
                checksum_url="https://example.invalid/tails.img.sha256",
                signature_url="https://example.invalid/tails.img.sig",
                sha256=digest,
                signing_fingerprint="0123456789ABCDEF0123456789ABCDEF01234567",
                pinned_primary_fingerprint="A490D0F4D311A4153E2BB7CADBB802B258ACD84F",
                reused_existing=False,
            )
            with (
                mock.patch("tails_cloner.app.Path.home", return_value=Path(tmpdir)),
                mock.patch("tails_cloner.app.VerifiedImageDownloader") as downloader_class,
            ):
                def fake_download(
                    _request: object,
                    progress_callback: Callable[[DownloadProgress], None] | None = None,
                ) -> VerifiedImageResult:
                    assert progress_callback is not None
                    progress_callback(
                        DownloadProgress(
                            phase="download",
                            message="Downloading Tails IMG",
                            bytes_received=1024,
                            bytes_total=2048,
                        )
                    )
                    progress_callback(
                        DownloadProgress(
                            phase="verification",
                            message="Verifying SHA-256 and OpenPGP",
                        )
                    )
                    return result

                downloader_class.return_value.download.side_effect = fake_download
                app._download_selected_remote_image(
                    result.source_url,
                    result.checksum_url,
                    result.signature_url,
                    "tails.img",
                    result.version,
                )
                app._drain_ui_events()
                request = downloader_class.return_value.download.call_args.args[0]
                self.assertEqual(request.destination, target)
                self.assertEqual(request.version, "7.7.2")

        self.assertEqual(successes[0][1:3], ("tails.img", digest))
        self.assertEqual(successes[0][3], "0123456789ABCDEF0123456789ABCDEF01234567")
        self.assertEqual(successes[0][4], "https://example.invalid/tails.img")
        self.assertEqual(failures, [])
        events = _jsonl_events(event_output)
        self.assertIn("progress", [event["kind"] for event in events])
        self.assertIn("verification", [event["kind"] for event in events])
        completion = next(event for event in events if event["kind"] == "completion")
        self.assertEqual(cast(dict[str, object], completion["result"])["activity"], "download")

    def test_failed_remote_verification_keeps_existing_cached_image(self) -> None:
        app = TailsClonerApp.__new__(TailsClonerApp)
        app._ui_events = SimpleQueue()
        event_output = StringIO()
        app._operator_events = EventSink(
            command="gui",
            output_mode=OutputMode.JSONL,
            stdout=event_output,
            stderr=StringIO(),
        )
        failures: list[str] = []
        app._apply_remote_download_success = lambda *_args: self.fail("verification unexpectedly succeeded")
        app._apply_remote_download_failure = failures.append

        with TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / ".cache/tails-cloner-clone/downloads/tails.img"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"previous verified image")
            with (
                mock.patch("tails_cloner.app.Path.home", return_value=Path(tmpdir)),
                mock.patch("tails_cloner.app.VerifiedImageDownloader") as downloader_class,
            ):
                downloader_class.return_value.download.side_effect = DownloadVerificationError("SHA-256 mismatch")
                app._download_selected_remote_image(
                    "https://example.invalid/tails.img",
                    "https://example.invalid/tails.img.sha256",
                    "https://example.invalid/tails.img.sig",
                    "tails.img",
                )
                app._drain_ui_events()

            self.assertEqual(target.read_bytes(), b"previous verified image")
            self.assertEqual(list(target.parent.glob(".*.part")), [])

        self.assertEqual(len(failures), 1)
        self.assertIn("SHA-256 mismatch", failures[0])
        failure = next(event for event in _jsonl_events(event_output) if event["kind"] == "failure")
        error = cast(dict[str, object], failure["error"])
        self.assertEqual(error["category"], "verification_failed")
        self.assertEqual(error["exit_code"], 5)

    def test_gui_write_emits_boundary_progress_and_completion(self) -> None:
        app = TailsClonerApp.__new__(TailsClonerApp)
        app._ui_events = SimpleQueue()
        event_output = StringIO()
        app._operator_events = EventSink(
            command="gui",
            output_mode=OutputMode.JSONL,
            stdout=event_output,
            stderr=StringIO(),
        )
        target = BlockDevice(
            path="/dev/sdz",
            stable_path="/dev/disk/by-id/usb-Test_Target",
            size_bytes=64_000_000_000,
            size_label="59.6 GiB",
            model="Synthetic USB",
            vendor="Test",
            transport="usb",
            removable=True,
            serial="TARGET123",
            is_big_enough_for_installation=True,
        )
        plan = plan_operation(
            OperationKind.INSTALL,
            OperationSource(type="image", path="/tmp/tails.img", size_bytes=1024),
            target,
        )
        app._emit_write_boundary(plan)

        class Controller:
            state = type("State", (), {"status_message": ""})()

            @staticmethod
            def clone_selected_image(
                _image_path: str | None,
                _device_path: str,
                progress_callback: Callable[[str], None] | None = None,
            ) -> None:
                assert progress_callback is not None
                progress_callback("1048576 bytes copied")

        app.controller = cast(object, Controller())
        app._run_write_operation("/tmp/tails.img", "/dev/sdz", False)

        events = _jsonl_events(event_output)
        self.assertEqual(events[0]["kind"], "write_boundary")
        self.assertIn("progress", [event["kind"] for event in events])
        completion = next(event for event in events if event["kind"] == "completion")
        result = cast(dict[str, object], completion["result"])
        self.assertEqual(result["activity"], "write")
        self.assertEqual(result["operation"], "install")

    def test_gui_write_failure_emits_stable_failure_record(self) -> None:
        app = TailsClonerApp.__new__(TailsClonerApp)
        app._ui_events = SimpleQueue()
        event_output = StringIO()
        app._operator_events = EventSink(
            command="gui",
            output_mode=OutputMode.JSONL,
            stdout=event_output,
            stderr=StringIO(),
        )

        class Controller:
            state = type("State", (), {"status_message": ""})()

            @staticmethod
            def clone_selected_image(
                _image_path: str | None,
                _device_path: str,
                progress_callback: Callable[[str], None] | None = None,
            ) -> None:
                del progress_callback
                raise RuntimeError("synthetic write failure")

        app.controller = cast(object, Controller())
        app._run_write_operation("/tmp/tails.img", "/dev/sdz", False)

        failure = next(event for event in _jsonl_events(event_output) if event["kind"] == "failure")
        error = cast(dict[str, object], failure["error"])
        self.assertEqual(error["category"], "install_failed")
        self.assertEqual(error["exit_code"], 7)

    def test_gui_discovery_and_state_changes_emit_stable_records(self) -> None:
        app = TailsClonerApp.__new__(TailsClonerApp)
        event_output = StringIO()
        app._operator_events = EventSink(
            command="gui",
            output_mode=OutputMode.JSONL,
            stdout=event_output,
            stderr=StringIO(),
        )
        app._last_versions_snapshot = ()
        app.versions_list = mock.Mock()
        version = VersionAssets(
            version="7.7.2",
            directory_url="https://example.invalid/7.7.2/",
            iso_url="https://example.invalid/7.7.2/tails.iso",
            img_url="https://example.invalid/7.7.2/tails.img",
            sig_url="https://example.invalid/7.7.2/tails.img.sig",
            sha256_url="https://example.invalid/7.7.2/tails.img.sha256",
        )
        state = type(
            "State",
            (),
            {
                "available_versions": [version],
                "selected_version": "",
                "status_message": "Scanning devices…",
                "source_mode": SourceMode.LOCAL,
                "versions_loading": False,
                "devices_loading": True,
            },
        )()
        app.controller = type("Controller", (), {"state": state})()
        app._sync_versions()

        app._last_status = ""
        app.status_var = _FakeStringVar()
        app._drain_ui_events = lambda: None
        for method_name in (
            "_sync_source_mode",
            "_sync_source_details",
            "_sync_tab2_source_summary",
            "_sync_versions",
            "_sync_remote_source_info",
            "_sync_devices",
            "_sync_selected_version_fields",
            "_sync_loading_labels",
            "_sync_upgrade_plan",
            "_sync_download_lock",
        ):
            setattr(app, method_name, lambda: None)
        app._write_in_progress = False
        app.after = lambda *_args: None
        app._sync_state()

        events = _jsonl_events(event_output)
        discovery = next(event for event in events if event["kind"] == "discovery")
        self.assertEqual(discovery["resource"], "versions")
        resolution = next(event for event in events if event["kind"] == "resolution")
        self.assertEqual(resolution["resource"], "application_state")
        self.assertEqual(resolution["status"], "Scanning devices…")

    def test_remote_download_rejects_plain_http_before_scheduling(self) -> None:
        app = TailsClonerApp.__new__(TailsClonerApp)
        state = type(
            "State",
            (),
            {
                "selected_image_url": "http://example.invalid/tails.img",
                "selected_checksum_url": "https://example.invalid/tails.img.sha256",
                "selected_signature_url": "https://example.invalid/tails.img.sig",
                "selected_version": "7.7.2",
                "status_message": "",
                "verified_image_path": "old",
                "verified_image_sha256": "old",
            },
        )()
        executor = mock.Mock()
        app.controller = type("Controller", (), {"state": state, "executor": executor})()

        app._start_remote_download()

        executor.submit.assert_not_called()
        self.assertIn("must use HTTPS", state.status_message)

    def test_remote_download_requires_gnupg_before_scheduling(self) -> None:
        app = TailsClonerApp.__new__(TailsClonerApp)
        state = type(
            "State",
            (),
            {
                "selected_image_url": "https://example.invalid/tails.img",
                "selected_checksum_url": "https://example.invalid/tails.img.sha256",
                "selected_signature_url": "https://example.invalid/tails.img.sig",
                "selected_version": "7.7.2",
                "status_message": "",
            },
        )()
        executor = mock.Mock()
        app.controller = type("Controller", (), {"state": state, "executor": executor})()

        with mock.patch("tails_cloner.app.gpg_available", return_value=False):
            app._start_remote_download()

        executor.submit.assert_not_called()
        self.assertIn("GnuPG", state.status_message)

    def test_remote_download_requires_detached_signature(self) -> None:
        app = TailsClonerApp.__new__(TailsClonerApp)
        state = type(
            "State",
            (),
            {
                "selected_image_url": "https://example.invalid/tails.img",
                "selected_checksum_url": "https://example.invalid/tails.img.sha256",
                "selected_signature_url": "",
                "selected_version": "7.7.2",
                "status_message": "",
            },
        )()
        executor = mock.Mock()
        app.controller = type("Controller", (), {"state": state, "executor": executor})()

        app._start_remote_download()

        executor.submit.assert_not_called()
        self.assertIn("no detached OpenPGP signature", state.status_message)

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


class GuiSafetyAndUxRegressionTests(unittest.TestCase):
    def test_escape_key_does_not_quit_the_application(self) -> None:
        build_source = inspect.getsource(TailsClonerApp._build_ui)

        self.assertNotIn("<Escape>", build_source)
        self.assertIn("<Control-q>", build_source)

    def test_destructive_confirmation_defaults_to_no(self) -> None:
        confirm_source = inspect.getsource(TailsClonerApp._confirm_and_clone)

        self.assertIn("default=messagebox.NO", confirm_source)

    def test_write_form_precedes_experimental_options_in_write_tab(self) -> None:
        build_source = inspect.getsource(TailsClonerApp._build_ui)

        # Grid rows decide visual order (the experimental panel is *built* first).
        self.assertIn('right.grid(row=0, column=0, sticky="nsew")', build_source)
        self.assertIn('exp_panel.grid(row=1, column=0, sticky="ew", pady=(8, 0))', build_source)

    def test_write_is_refused_while_a_verified_download_is_running(self) -> None:
        app = TailsClonerApp.__new__(TailsClonerApp)
        app._download_in_progress = True
        with mock.patch("tails_cloner.app.messagebox") as box:
            app._confirm_and_clone()

        box.showinfo.assert_called_once()
        box.askyesno.assert_not_called()

    def test_suggested_checksum_uses_shared_tor_aware_fetch_and_matches_filename(self) -> None:
        app = TailsClonerApp.__new__(TailsClonerApp)
        app._ui_events = SimpleQueue()
        digest = "ab" * 32
        text = f"{'cd' * 32}  other.img\n{digest}  tails-amd64-7.0.img\n"

        applied: list[tuple[str, str]] = []
        app._apply_suggested_checksum = lambda url, checksum: applied.append((url, checksum))  # type: ignore[method-assign]
        with (
            mock.patch("tails_cloner.app.should_use_torify", return_value=True),
            mock.patch("tails_cloner.app.fetch_checksum_text", return_value=text) as fetch,
        ):
            app._fetch_suggested_checksum(
                "https://example.invalid/tails-amd64-7.0.img.sha256",
                "https://example.invalid/tails-amd64-7.0.img",
            )

        fetch.assert_called_once_with("https://example.invalid/tails-amd64-7.0.img.sha256", use_torify=True)
        app._ui_events.get_nowait()()
        self.assertEqual(applied, [("https://example.invalid/tails-amd64-7.0.img.sha256", digest)])

    def test_stale_suggested_checksum_for_a_previous_version_is_ignored(self) -> None:
        app = TailsClonerApp.__new__(TailsClonerApp)
        app.suggested_checksum_var = _FakeStringVar("current")
        app.controller = type(
            "Controller", (), {"state": type("State", (), {"selected_checksum_url": "https://example.invalid/new"})()}
        )()

        app._apply_suggested_checksum("https://example.invalid/old", "stale")
        self.assertEqual(app.suggested_checksum_var.get(), "current")

        app._apply_suggested_checksum("https://example.invalid/new", "fresh")
        self.assertEqual(app.suggested_checksum_var.get(), "fresh")

    def test_dd_progress_switches_progress_bar_to_determinate_with_percentage(self) -> None:
        app = TailsClonerApp.__new__(TailsClonerApp)
        app._write_total_bytes = 1000
        app._write_in_progress = True
        calls: list[tuple[str, dict[str, object]]] = []

        class _Bar:
            mode = "indeterminate"

            def cget(self, name: str) -> str:
                assert name == "mode"
                return self.mode

            def stop(self) -> None:
                calls.append(("stop", {}))

            def configure(self, **kwargs: object) -> None:
                self.mode = str(kwargs.get("mode", self.mode))
                calls.append(("configure", kwargs))

        app.progress_bar = _Bar()  # type: ignore[assignment]
        app.progress_label = _FakeLabel()  # type: ignore[assignment]

        app._set_progress_fraction(250, "250 bytes (250 B) copied, 1 s, 250 B/s")

        self.assertIn(("configure", {"mode": "determinate", "maximum": 1000}), calls)
        self.assertIn(("configure", {"value": 250}), calls)
        self.assertTrue(app.progress_label.kwargs["text"].startswith("25% · "))

    def test_progress_fraction_is_ignored_when_total_is_unknown(self) -> None:
        app = TailsClonerApp.__new__(TailsClonerApp)
        app._write_total_bytes = 0
        app._write_in_progress = True
        app.progress_bar = None  # type: ignore[assignment]  # would raise if touched

        app._set_progress_fraction(250, "250 bytes copied")

    def test_gui_progress_events_carry_bytes_written_like_the_cli(self) -> None:
        app = TailsClonerApp.__new__(TailsClonerApp)
        app._ui_events = SimpleQueue()
        event_output = StringIO()
        app._operator_events = EventSink(
            command="gui", output_mode=OutputMode.JSONL, stdout=event_output, stderr=StringIO()
        )

        class Controller:
            state = type("State", (), {"status_message": ""})()

            @staticmethod
            def clone_selected_image(
                _image_path: str | None,
                _device_path: str,
                progress_callback: Callable[[str], None] | None = None,
            ) -> None:
                assert progress_callback is not None
                progress_callback("1048576 bytes (1.0 MB, 1.0 MiB) copied, 1 s, 1.0 MB/s")

        app.controller = cast(object, Controller())
        app._run_write_operation("/tmp/tails.img", "/dev/sdz", False)

        progress = next(event for event in _jsonl_events(event_output) if event["kind"] == "progress")
        self.assertEqual(progress["bytes_written"], 1048576)


if __name__ == "__main__":
    unittest.main()
