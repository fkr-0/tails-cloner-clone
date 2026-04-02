import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

from tails_cloner.controller import ApplicationController
from tails_cloner.models import AppState, BlockDevice, VersionAssets


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

    def clone_image(self, image_path: str, device_path: str, progress_callback):
        self.calls.append((image_path, device_path))
        progress_callback("done")


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

        self.assertEqual(clone_service.calls, [("/tmp/tails.iso", "/dev/sdb")])
        self.assertEqual(controller.state.status_message, "Clone completed successfully.")
        self.assertEqual(controller.state.last_clone_progress, "done")


if __name__ == "__main__":
    unittest.main()
