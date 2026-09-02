import hashlib
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
                sig_url="https://download.example/stable/6.12/tails-amd64-6.12.img.sig",
                sha256_url="https://download.example/stable/6.12/tails-amd64-6.12.img.sha256",
            ),
            VersionAssets(
                version="6.11",
                directory_url="https://download.example/stable/6.11/",
                iso_url="https://download.example/stable/6.11/tails-amd64-6.11.iso",
                img_url="https://download.example/stable/6.11/tails-amd64-6.11.img",
                sig_url="https://download.example/stable/6.11/tails-amd64-6.11.img.sig",
                sha256_url="https://download.example/stable/6.11/tails-amd64-6.11.img.sha256",
            ),
        ]


class FakeDeviceService:
    def list_devices(self):
        return [
            BlockDevice(
                path="/dev/sdb",
                size_bytes=16008609792,
                size_label="14.9 GiB",
                model="USB DISK",
                vendor="SanDisk",
                transport="usb",
                removable=True,
                has_tails=True,
            ),
            BlockDevice(
                path="/dev/sdc",
                size_bytes=32017219584,
                size_label="29.8 GiB",
                model="USB DISK",
                vendor="Kingston",
                transport="usb",
                removable=True,
                has_tails=True,
            )
        ]


    def list_removable_devices(self):
        return self.list_devices()


class ReplacedTargetDeviceService(FakeDeviceService):
    def list_devices(self):
        devices = super().list_devices()
        devices[0].serial = "replacement-device"
        return devices


class MissingTargetDeviceService(FakeDeviceService):
    def list_devices(self):
        return [device for device in super().list_devices() if device.path != "/dev/sdb"]


class StablePathDeviceService(FakeDeviceService):
    def list_devices(self):
        devices = super().list_devices()
        devices[0].stable_path = "/dev/disk/by-id/usb-target"
        return devices


class ReplacedSourceDeviceService(FakeDeviceService):
    def list_devices(self):
        devices = super().list_devices()
        devices[0].serial = "replacement-source"
        return devices


class StableUpgradeDeviceService(FakeDeviceService):
    def list_devices(self):
        devices = super().list_devices()
        devices[0].stable_path = "/dev/disk/by-id/usb-source"
        devices[1].stable_path = "/dev/disk/by-id/usb-target"
        return devices


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
        self.assertTrue(controller.state.selected_signature_url.endswith("tails-amd64-6.11.img.sig"))

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

    def test_write_rejects_reused_device_path_with_changed_hardware_identity(self) -> None:
        clone_service = FakeCloneService()
        state = AppState(
            devices=[
                BlockDevice(
                    path="/dev/sdb",
                    size_bytes=16008609792,
                    size_label="14.9 GiB",
                    model="USB DISK",
                    vendor="SanDisk",
                    transport="usb",
                    removable=True,
                    serial="original-device",
                    has_tails=True,
                )
            ]
        )
        controller = ApplicationController(
            state=state,
            version_service=FakeVersionService(),
            device_service=ReplacedTargetDeviceService(),
            clone_service=clone_service,
            executor=ThreadPoolExecutor(max_workers=1),
        )
        self.addCleanup(controller.shutdown)

        with self.assertRaisesRegex(RuntimeError, "Target device identity changed"):
            controller.clone_selected_image("/tmp/tails.img", "/dev/sdb")

        self.assertEqual(clone_service.calls, [])

    def test_write_uses_stable_by_id_target_alias_when_available(self) -> None:
        clone_service = FakeCloneService()
        device_service = StablePathDeviceService()
        state = AppState(devices=device_service.list_devices())
        controller = ApplicationController(
            state=state,
            version_service=FakeVersionService(),
            device_service=device_service,
            clone_service=clone_service,
            executor=ThreadPoolExecutor(max_workers=1),
        )
        self.addCleanup(controller.shutdown)

        controller.clone_selected_image("/tmp/tails.img", "/dev/sdb")

        self.assertEqual(clone_service.calls[0][1], "/dev/disk/by-id/usb-target")

    def test_write_rejects_target_that_disappeared_after_confirmation(self) -> None:
        clone_service = FakeCloneService()
        state = AppState(devices=FakeDeviceService().list_devices())
        controller = ApplicationController(
            state=state,
            version_service=FakeVersionService(),
            device_service=MissingTargetDeviceService(),
            clone_service=clone_service,
            executor=ThreadPoolExecutor(max_workers=1),
        )
        self.addCleanup(controller.shutdown)

        with self.assertRaisesRegex(RuntimeError, "Target device disappeared"):
            controller.clone_selected_image("/tmp/tails.img", "/dev/sdb")

        self.assertEqual(clone_service.calls, [])

    def test_verified_download_is_rechecked_immediately_before_install(self) -> None:
        clone_service = FakeCloneService()
        with TemporaryDirectory() as tmpdir:
            image = Path(tmpdir) / "tails.img"
            image.write_bytes(b"verified image")
            digest = hashlib.sha256(b"verified image").hexdigest()
            state = AppState(
                verified_image_path=str(image),
                verified_image_sha256=digest,
            )
            controller = ApplicationController(
                state=state,
                version_service=FakeVersionService(),
                device_service=FakeDeviceService(),
                clone_service=clone_service,
                executor=ThreadPoolExecutor(max_workers=1),
            )
            self.addCleanup(controller.shutdown)

            controller.clone_selected_image(str(image), "/dev/sdb")

        self.assertEqual(len(clone_service.calls), 1)

    def test_tampered_verified_download_is_rejected_before_install(self) -> None:
        clone_service = FakeCloneService()
        with TemporaryDirectory() as tmpdir:
            image = Path(tmpdir) / "tails.img"
            image.write_bytes(b"original image")
            digest = hashlib.sha256(b"original image").hexdigest()
            image.write_bytes(b"tampered image")
            state = AppState(
                verified_image_path=str(image),
                verified_image_sha256=digest,
            )
            controller = ApplicationController(
                state=state,
                version_service=FakeVersionService(),
                device_service=FakeDeviceService(),
                clone_service=clone_service,
                executor=ThreadPoolExecutor(max_workers=1),
            )
            self.addCleanup(controller.shutdown)

            with self.assertRaisesRegex(RuntimeError, "previously verified downloaded image changed"):
                controller.clone_selected_image(str(image), "/dev/sdb")

        self.assertEqual(clone_service.calls, [])
        self.assertIn("integrity verification failed", controller.state.status_message.lower())

    def test_running_tails_upgrade_uses_live_source_device_partition(self) -> None:
        clone_service = FakeCloneService()
        state = AppState(
            source_mode=SourceMode.RUNNING,
            running_tails_available=True,
            running_tails_device="/dev/sdb1",
            running_tails_version="7.7.2",
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
        self.assertIn("running Tails source", controller.state.status_message)

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
        self.assertEqual(
            controller.state.status_message,
            "Upgrade completed successfully. Existing Persistent Storage, if present, was preserved.",
        )
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

        self.assertEqual(controller.state.status_message, "Found 2 device(s).")


    def test_refresh_devices_marks_running_device_visible_but_not_selectable(self) -> None:
        state = AppState(running_tails_device="/dev/sdb1", running_tails_available=True, source_mode=SourceMode.RUNNING)
        controller = ApplicationController(
            state=state,
            version_service=FakeVersionService(),
            device_service=FakeDeviceService(),
            clone_service=FakeCloneService(),
            executor=ThreadPoolExecutor(max_workers=1),
        )
        self.addCleanup(controller.shutdown)

        controller.refresh_devices()

        self.assertEqual(controller.state.devices[0].path, "/dev/sdb")
        self.assertTrue(controller.state.devices[0].is_running_system_device)
        self.assertTrue(controller.state.devices[0].is_current_system_device)
        self.assertEqual(controller.state.devices[0].status_label, "Currently running Tails")
        self.assertFalse(controller.state.devices[0].selectable)
        self.assertIn("currently running Tails", controller.state.devices[0].disabled_reason)

    def test_annotation_preserves_current_os_disk_protection(self) -> None:
        system_disk = BlockDevice(
            path="/dev/nvme0n1",
            size_bytes=512 * 1024**3,
            size_label="512.0 GiB",
            model="System Disk",
            vendor="NVMe",
            transport="nvme",
            removable=False,
            is_host_system_device=True,
            disabled_reason="This device contains filesystems used by the currently running operating system.",
        )
        controller = ApplicationController(
            state=AppState(devices=[system_disk]),
            version_service=FakeVersionService(),
            device_service=FakeDeviceService(),
            clone_service=FakeCloneService(),
            executor=ThreadPoolExecutor(max_workers=1),
        )
        self.addCleanup(controller.shutdown)

        controller.annotate_device_selection_state()

        self.assertFalse(system_disk.selectable)
        self.assertIn("currently running operating system", system_disk.disabled_reason)

    def test_annotation_preserves_running_system_root_label(self) -> None:
        system_disk = BlockDevice(
            path="/dev/nvme0n1",
            size_bytes=512 * 1024**3,
            size_label="512.0 GiB",
            model="System Disk",
            vendor="NVMe",
            transport="nvme",
            removable=False,
            is_current_system_device=True,
            is_host_system_device=True,
            disabled_reason="This device backs the currently running system.",
        )
        controller = ApplicationController(
            state=AppState(devices=[system_disk]),
            version_service=FakeVersionService(),
            device_service=FakeDeviceService(),
            clone_service=FakeCloneService(),
            executor=ThreadPoolExecutor(max_workers=1),
        )
        self.addCleanup(controller.shutdown)

        controller.annotate_device_selection_state()

        self.assertEqual(system_disk.status_label, "Running system")
        self.assertFalse(system_disk.selectable)
        self.assertIn("currently running system", system_disk.disabled_reason)

    def test_install_rejects_running_device_target(self) -> None:
        clone_service = FakeCloneService()
        state = AppState(running_tails_device="/dev/sdb1", running_tails_available=True, source_mode=SourceMode.LOCAL)
        controller = ApplicationController(
            state=state,
            version_service=FakeVersionService(),
            device_service=FakeDeviceService(),
            clone_service=clone_service,
            executor=ThreadPoolExecutor(max_workers=1),
        )
        self.addCleanup(controller.shutdown)

        with self.assertRaisesRegex(RuntimeError, "currently running Tails"):
            controller.clone_selected_image("/tmp/tails.img", "/dev/sdb")
        self.assertEqual(clone_service.calls, [])

    def test_upgrade_rejects_running_device_target(self) -> None:
        clone_service = FakeCloneService()
        state = AppState(running_tails_device="/dev/sdb1", running_tails_available=True, source_mode=SourceMode.LOCAL)
        controller = ApplicationController(
            state=state,
            version_service=FakeVersionService(),
            device_service=FakeDeviceService(),
            clone_service=clone_service,
            executor=ThreadPoolExecutor(max_workers=1),
        )
        self.addCleanup(controller.shutdown)

        with self.assertRaisesRegex(RuntimeError, "currently running Tails"):
            controller.upgrade_selected_image("/tmp/tails.img", "/dev/sdb2")
        self.assertEqual(clone_service.upgrade_calls, [])

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
            "Upgrade completed successfully from attached live source. Existing Persistent Storage, if present, was preserved.",
        )

    def test_attached_upgrade_rejects_changed_source_identity(self) -> None:
        clone_service = FakeCloneService()
        devices = FakeDeviceService().list_devices()
        devices[0].serial = "original-source"
        state = AppState(
            devices=devices,
            source_mode=SourceMode.ATTACHED,
            attached_live_source_device="/dev/sdb1",
            attached_live_source_mount="/mnt/source",
            attached_live_source_version="7.7.2",
        )
        controller = ApplicationController(
            state=state,
            version_service=FakeVersionService(),
            device_service=ReplacedSourceDeviceService(),
            clone_service=clone_service,
            executor=ThreadPoolExecutor(max_workers=1),
        )
        self.addCleanup(controller.shutdown)

        with self.assertRaisesRegex(RuntimeError, "Source device identity changed"):
            controller.upgrade_selected_image(None, "/dev/sdc")

        self.assertEqual(clone_service.upgrade_from_device_calls, [])

    def test_attached_upgrade_uses_stable_source_and_target_aliases(self) -> None:
        clone_service = FakeCloneService()
        device_service = StableUpgradeDeviceService()
        state = AppState(
            devices=device_service.list_devices(),
            source_mode=SourceMode.ATTACHED,
            attached_live_source_device="/dev/sdb1",
            attached_live_source_mount="/mnt/source",
            attached_live_source_version="7.7.2",
        )
        controller = ApplicationController(
            state=state,
            version_service=FakeVersionService(),
            device_service=device_service,
            clone_service=clone_service,
            executor=ThreadPoolExecutor(max_workers=1),
        )
        self.addCleanup(controller.shutdown)

        controller.upgrade_selected_image(None, "/dev/sdc")

        self.assertEqual(
            clone_service.upgrade_from_device_calls,
            [("/dev/disk/by-id/usb-source", "/dev/disk/by-id/usb-target")],
        )

    def test_attached_upgrade_requires_source_in_confirmed_device_list(self) -> None:
        clone_service = FakeCloneService()
        state = AppState(
            devices=[FakeDeviceService().list_devices()[1]],
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

        with self.assertRaisesRegex(
            RuntimeError,
            "Source device was not present in the confirmed device list",
        ):
            controller.upgrade_selected_image(None, "/dev/sdc")

        self.assertEqual(clone_service.upgrade_from_device_calls, [])

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
