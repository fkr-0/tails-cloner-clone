import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory

from tails_cloner.controller import ApplicationController
from tails_cloner.models import AppState, BlockDevice, SourceMode, VersionAssets


def wait_for(description: str, condition, timeout: float = 2.0, interval: float = 0.01):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = condition()
        if result:
            return result
        time.sleep(interval)
    raise AssertionError(f"Timed out waiting for {description}")


class FakeVersionService:
    def fetch_versions(self):
        return [
            VersionAssets(
                version="6.12",
                directory_url="https://download.example/stable/6.12/",
                iso_url="https://download.example/stable/6.12/tails-amd64-6.12.iso",
                img_url="https://download.example/stable/6.12/tails-amd64-6.12.img",
                sig_url="https://download.example/stable/6.12/tails-amd64-6.12.iso.sig",
                sha256_url="https://download.example/stable/6.12/tails-amd64-6.12.img.sha256",
            ),
            VersionAssets(
                version="6.11",
                directory_url="https://download.example/stable/6.11/",
                iso_url="https://download.example/stable/6.11/tails-amd64-6.11.iso",
                img_url="https://download.example/stable/6.11/tails-amd64-6.11.img",
                sig_url="https://download.example/stable/6.11/tails-amd64-6.11.iso.sig",
                sha256_url="https://download.example/stable/6.11/tails-amd64-6.11.img.sha256",
            ),
        ]


class FakeDeviceService:
    def list_removable_devices(self):
        return [
            BlockDevice(
                path="/dev/sdb",
                size_bytes=16008609792,
                size_label="14.9 GiB",
                model="USB DISK",
                vendor="SanDisk",
                transport="usb",
                removable=True,
            )
        ]


class FakeCloneService:
    def __init__(self):
        self.calls = []
        self.upgrade_calls = []
        self.upgrade_from_device_calls = []

    def clone_image(
        self,
        image_path: str,
        device_path: str,
        progress_callback=None,
        post_write_options=None,
    ):
        self.calls.append((image_path, device_path, post_write_options))
        if progress_callback:
            progress_callback("done")

    def upgrade_image(
        self,
        image_path: str,
        device_path: str,
        progress_callback=None,
    ):
        self.upgrade_calls.append((image_path, device_path))
        if progress_callback:
            progress_callback("partition upgrade done")

    def upgrade_from_device(
        self,
        source_device: str,
        device_path: str,
        progress_callback=None,
    ):
        self.upgrade_from_device_calls.append((source_device, device_path))
        if progress_callback:
            progress_callback("source-device partition upgrade done")


class ControllerTests(unittest.TestCase):
    def test_startup_populates_versions_and_devices_async(self) -> None:
        controller = ApplicationController(
            state=AppState(),
            version_service=FakeVersionService(),
            device_service=FakeDeviceService(),
            clone_service=FakeCloneService(),
            executor=ThreadPoolExecutor(max_workers=2),
        )
        self.addCleanup(controller.shutdown)

        controller.startup()

        wait_for("version refresh", lambda: controller.state.available_versions)
        wait_for("device refresh", lambda: controller.state.devices)

        self.assertEqual(controller.state.available_versions[0].version, "6.12")
        self.assertEqual(controller.state.selected_version, "6.12")
        self.assertTrue(controller.state.selected_iso_url.endswith("tails-amd64-6.12.iso"))
        self.assertEqual(controller.state.devices[0].path, "/dev/sdb")

    def test_select_version_updates_derived_urls(self) -> None:
        state = AppState()
        state.available_versions = FakeVersionService().fetch_versions()
        controller = ApplicationController(
            state=state,
            version_service=FakeVersionService(),
            device_service=FakeDeviceService(),
            clone_service=FakeCloneService(),
            executor=ThreadPoolExecutor(max_workers=1),
        )
        self.addCleanup(controller.shutdown)

        controller.select_version("6.11")

        self.assertEqual(controller.state.selected_version, "6.11")
        self.assertTrue(controller.state.selected_iso_url.endswith("tails-amd64-6.11.iso"))
        self.assertTrue(controller.state.selected_signature_url.endswith("tails-amd64-6.11.iso.sig"))

    def test_clone_selected_image_updates_status(self) -> None:
        clone_service = FakeCloneService()
        controller = ApplicationController(
            state=AppState(),
            version_service=FakeVersionService(),
            device_service=FakeDeviceService(),
            clone_service=clone_service,
            executor=ThreadPoolExecutor(max_workers=1),
        )
        self.addCleanup(controller.shutdown)

        controller.clone_selected_image("/tmp/tails.iso", "/dev/sdb")

        self.assertEqual(len(clone_service.calls), 1)
        self.assertEqual(clone_service.calls[0][0], "/tmp/tails.iso")
        self.assertEqual(clone_service.calls[0][1], "/dev/sdb")
        self.assertIsNotNone(clone_service.calls[0][2])
        self.assertEqual(controller.state.status_message, "Installation completed successfully.")
        self.assertEqual(controller.state.last_clone_progress, "done")

    def test_upgrade_selected_image_uses_partition_scoped_upgrade_service(self) -> None:
        clone_service = FakeCloneService()
        controller = ApplicationController(
            state=AppState(),
            version_service=FakeVersionService(),
            device_service=FakeDeviceService(),
            clone_service=clone_service,
            executor=ThreadPoolExecutor(max_workers=1),
        )
        self.addCleanup(controller.shutdown)

        controller.upgrade_selected_image("/tmp/tails.img", "/dev/sdb")

        self.assertEqual(clone_service.calls, [])
        self.assertEqual(clone_service.upgrade_calls, [("/tmp/tails.img", "/dev/sdb")])
        self.assertEqual(controller.state.status_message, "Upgrade completed successfully. Persistent Storage preserved.")
        self.assertEqual(controller.state.last_clone_progress, "partition upgrade done")

    def test_refresh_devices_uses_generic_device_wording(self) -> None:
        controller = ApplicationController(
            state=AppState(),
            version_service=FakeVersionService(),
            device_service=FakeDeviceService(),
            clone_service=FakeCloneService(),
            executor=ThreadPoolExecutor(max_workers=1),
        )
        self.addCleanup(controller.shutdown)

        controller.refresh_devices()

        self.assertEqual(controller.state.status_message, "Found 1 device(s).")

    def test_set_attached_live_source_records_validated_source(self) -> None:
        controller = ApplicationController(
            state=AppState(),
            version_service=FakeVersionService(),
            device_service=FakeDeviceService(),
            clone_service=FakeCloneService(),
            executor=ThreadPoolExecutor(max_workers=1),
        )
        self.addCleanup(controller.shutdown)
        with TemporaryDirectory() as tmpdir:
            mount_point = Path(tmpdir)
            (mount_point / "live").mkdir(parents=True)
            (mount_point / "live" / "Tails.version").write_text("7.7.2\n", encoding="utf-8")

            source = controller.set_attached_live_source("/dev/sdb1", mount_point)
            self.assertEqual(source.version, "7.7.2")

        self.assertEqual(controller.state.source_mode.value, "attached")
        self.assertEqual(controller.state.attached_live_source_device, "/dev/sdb1")
        self.assertEqual(controller.state.attached_live_source_version, "7.7.2")
        self.assertIn("Using attached Tails live source 7.7.2", controller.state.status_message)

    def test_attached_live_upgrade_uses_source_device_upgrade_service(self) -> None:
        clone_service = FakeCloneService()
        state = AppState(
            source_mode=SourceMode.ATTACHED,
            attached_live_source_device="/dev/sdb1",
            attached_live_source_mount="/mnt/source",
            attached_live_source_version="7.7.2",
        )
        controller = ApplicationController(
            state=state,
            version_service=FakeVersionService(),
            device_service=FakeDeviceService(),
            clone_service=clone_service,
            executor=ThreadPoolExecutor(max_workers=1),
        )
        self.addCleanup(controller.shutdown)

        controller.upgrade_selected_image(None, "/dev/sdc")

        self.assertEqual(clone_service.upgrade_calls, [])
        self.assertEqual(clone_service.upgrade_from_device_calls, [("/dev/sdb", "/dev/sdc")])
        self.assertEqual(controller.state.last_clone_progress, "source-device partition upgrade done")
        self.assertEqual(
            controller.state.status_message,
            "Upgrade completed successfully from attached live source. Persistent Storage preserved.",
        )

    def test_attached_live_upgrade_rejects_source_as_target(self) -> None:
        clone_service = FakeCloneService()
        state = AppState(
            source_mode=SourceMode.ATTACHED,
            attached_live_source_device="/dev/sdb1",
        )
        controller = ApplicationController(
            state=state,
            version_service=FakeVersionService(),
            device_service=FakeDeviceService(),
            clone_service=clone_service,
            executor=ThreadPoolExecutor(max_workers=1),
        )
        self.addCleanup(controller.shutdown)

        with self.assertRaises(RuntimeError):
            controller.upgrade_selected_image(None, "/dev/sdb2")
        self.assertEqual(clone_service.upgrade_from_device_calls, [])

    def test_attached_live_install_is_rejected(self) -> None:
        controller = ApplicationController(
            state=AppState(source_mode=SourceMode.ATTACHED),
            version_service=FakeVersionService(),
            device_service=FakeDeviceService(),
            clone_service=FakeCloneService(),
            executor=ThreadPoolExecutor(max_workers=1),
        )
        self.addCleanup(controller.shutdown)

        with self.assertRaises(RuntimeError):
            controller.clone_selected_image(None, "/dev/sdc")

    def test_attached_live_source_target_exclusion_uses_parent_disk(self) -> None:
        controller = ApplicationController(
            state=AppState(),
            version_service=FakeVersionService(),
            device_service=FakeDeviceService(),
            clone_service=FakeCloneService(),
            executor=ThreadPoolExecutor(max_workers=1),
        )
        self.addCleanup(controller.shutdown)
        controller.state.attached_live_source_device = "/dev/sdb1"

        self.assertTrue(controller.target_is_attached_live_source("/dev/sdb"))
        self.assertTrue(controller.target_is_attached_live_source("/dev/sdb2"))
        self.assertFalse(controller.target_is_attached_live_source("/dev/sdc"))

    def test_clear_attached_live_source_resets_state(self) -> None:
        state = AppState(
            attached_live_source_device="/dev/sdb1",
            attached_live_source_mount="/mnt/source",
            attached_live_source_version="7.7.2",
        )
        controller = ApplicationController(
            state=state,
            version_service=FakeVersionService(),
            device_service=FakeDeviceService(),
            clone_service=FakeCloneService(),
            executor=ThreadPoolExecutor(max_workers=1),
        )
        self.addCleanup(controller.shutdown)

        controller.clear_attached_live_source()

        self.assertEqual(controller.state.attached_live_source_device, "")
        self.assertEqual(controller.state.attached_live_source_mount, "")
        self.assertEqual(controller.state.attached_live_source_version, "")
        self.assertEqual(controller.state.status_message, "Attached live source cleared.")


if __name__ == "__main__":
    unittest.main()
