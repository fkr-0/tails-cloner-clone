"""Tk-level boot parse freshness regressions (run under xvfb-run on headless CI)."""

import tkinter as tk
import unittest
from unittest import mock

from tails_cloner.app import TailsClonerApp
from tails_cloner.models import AppState, SourceMode


class BootOrderGuiFreshnessTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            probe = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk display unavailable (use xvfb-run): {error}")
        probe.destroy()
        self.state = AppState()
        self.state.running_tails_available = True
        self.state.running_tails_device = "/dev/fake-running-source"
        self.controller = mock.Mock()
        self.controller.state = self.state
        self.controller.set_source_mode.side_effect = lambda mode: setattr(self.state, "source_mode", mode)
        self.app = TailsClonerApp(self.controller, "https://example.invalid/versions")
        self.addCleanup(self.app.destroy)
        self.app.experimental_enabled_var.set(True)
        self.app._sync_experimental_state()

    def _pending_parse(self, path: str = "old.img") -> tuple[int, str]:
        self.app.image_path_var.set(path)
        self.app._start_parse_boot_order_entries()
        task = self.controller.executor.submit.call_args.args
        return task[1], task[2]

    def _assert_stale(self, job: int, path: str) -> None:
        self.app._apply_parsed_boot_order_entries(job, path, ["stale entry"])
        self.app._apply_boot_parse_failure(job, path, "stale failure")
        self.assertEqual(self.app.boot_order_list.size(), 0)
        self.assertNotIn("stale", self.app.boot_loader_status_var.get())
        self.assertEqual(self.state.post_write_options.boot_loader_order.entries, [])

    def test_local_reply_rejected_after_attached_switch_even_same_image_path(self) -> None:
        job, path = self._pending_parse()
        self.app.source_mode_var.set("attached")
        self.app._on_source_mode_changed()
        self.assertEqual(self.state.source_mode, SourceMode.ATTACHED)
        self._assert_stale(job, path)

    def test_local_reply_rejected_after_running_and_remote_switches(self) -> None:
        for mode in ("running", "remote"):
            self.app.action_mode_var.set("install")
            self.app._on_action_mode_changed()
            self.app.source_mode_var.set("local")
            self.app._on_source_mode_changed()
            job, path = self._pending_parse()
            self.app.source_mode_var.set(mode)
            self.app._on_source_mode_changed()
            self._assert_stale(job, path)

    def test_image_reselection_invalidates_even_when_path_returns_to_original(self) -> None:
        job, path = self._pending_parse()
        self.app.image_path_var.set("other.img")
        self.app.image_path_var.set(path)
        self._assert_stale(job, path)

    def test_install_upgrade_toggle_invalidates_pending_reply(self) -> None:
        job, path = self._pending_parse()
        self.app.action_mode_var.set("upgrade")
        self.app._on_action_mode_changed()
        self.app.action_mode_var.set("install")
        self.app._on_action_mode_changed()
        self._assert_stale(job, path)

    def test_current_local_reply_is_applied(self) -> None:
        job, path = self._pending_parse()
        self.app._apply_parsed_boot_order_entries(job, path, ["Current entry"])
        self.assertEqual(self.app._boot_order_entries(), ["Current entry"])


if __name__ == "__main__":
    unittest.main()
