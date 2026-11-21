"""Offline tests: contradictory Tails identity must never authorize writes."""

import json
import subprocess
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from tails_cloner.controller import ApplicationController
from tails_cloner.devices import DeviceService
from tails_cloner.models import AppState, SourceMode


class RunningSignalDisagreementTests(unittest.TestCase):
    @staticmethod
    def fixture_devices(mount_signal, boot_signal, live_device="/dev/sdb1", root="overlay\n"):
        payload = {"blockdevices": [
            {"path": name, "type": "disk", "size": str(32 * 1024**3), "rm": True,
             "children": [{"path": f"{name}1", "type": "part", "maj:min": mm}]}
            for name, mm in (("/dev/sdb", "8:17"), ("/dev/sdc", "8:33"))
        ]}

        def run(command, **_kwargs):
            if command[0] == "lsblk":
                value = json.dumps(payload)
            elif command[:1] == ["findmnt"]:
                value = root
            else:
                raise AssertionError(f"Unexpected host command: {command}")
            return subprocess.CompletedProcess(command, 0, stdout=value, stderr="")

        with (patch("tails_cloner.devices.is_running_tails", return_value=mount_signal),
              patch("tails_cloner.devices.has_running_tails_boot_signals", return_value=boot_signal),
              patch("tails_cloner.devices.get_running_tails_device", return_value=live_device),
              patch("tails_cloner.devices.find_stable_device_path", return_value="")):
            return DeviceService(run=run).list_devices()

    def test_device_matrix_with_stale_marker_and_missing_medium(self):
        for mount, boot in ((True, False), (False, True), (True, True), (False, False)):
            with self.subTest(mount=mount, boot=boot):
                devices = self.fixture_devices(mount, boot)
                if mount != boot:
                    self.assertTrue(all(not d.selectable for d in devices))
                elif mount:
                    self.assertEqual([d.selectable for d in devices], [False, True])
                else:
                    self.assertEqual([d.selectable for d in devices], [True, True])

    def test_mismatched_physical_source_fails_closed(self):
        devices = self.fixture_devices(True, True, live_device="/dev/sdz1")
        self.assertTrue(all(not d.selectable for d in devices))

    def test_root_findmnt_source_remains_protected_in_local_mode(self):
        devices = self.fixture_devices(False, False, root="/dev/sdc1\n")
        self.assertEqual([d.selectable for d in devices], [True, False])

    def test_controller_running_attached_local_modes(self):
        for mount, boot in ((True, False), (False, True), (True, True), (False, False)):
            with self.subTest(mount=mount, boot=boot):
                state = AppState()
                with ThreadPoolExecutor(max_workers=1) as executor:
                    controller = ApplicationController(
                        state=state, version_service=None, device_service=None,
                        clone_service=None, executor=executor,
                    )
                    with (patch("tails_cloner.controller.is_running_tails", return_value=mount),
                          patch("tails_cloner.controller.has_running_tails_boot_signals", return_value=boot),
                          patch("tails_cloner.controller.get_running_tails_device", return_value="/dev/sdb1"),
                          patch("tails_cloner.controller.get_running_tails_version", return_value="6.0")):
                        controller.detect_running_tails()
                    self.assertEqual(state.running_tails_available, mount and boot)
                    if mount and boot:
                        self.assertEqual(state.source_mode, SourceMode.RUNNING)
                    else:
                        self.assertEqual(state.source_mode, SourceMode.LOCAL)
                        self.assertEqual(state.running_tails_device, "")
                        with self.assertRaisesRegex(ValueError, "not running from Tails"):
                            controller.set_source_mode(SourceMode.RUNNING)
                    controller.set_source_mode(SourceMode.ATTACHED)
                    self.assertEqual(state.source_mode, SourceMode.ATTACHED)
                    controller.set_source_mode(SourceMode.LOCAL)
                    self.assertEqual(state.source_mode, SourceMode.LOCAL)


if __name__ == "__main__":
    unittest.main()
