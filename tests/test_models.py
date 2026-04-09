import unittest

from tails_cloner.models import BlockDevice, SourceMode, VersionAssets, AppState


class BlockDeviceTests(unittest.TestCase):
    def test_pretty_name_includes_removable_indicator(self) -> None:
        """Pretty name shows (removable) for removable devices."""
        device = BlockDevice(
            path="/dev/sdb",
            size_bytes=16000000000,
            size_label="14.9 GiB",
            model="USB Drive",
            vendor="SanDisk",
            transport="usb",
            removable=True,
        )
        self.assertIn("(removable)", device.pretty_name)

    def test_pretty_name_includes_read_only_indicator(self) -> None:
        """Pretty name shows (read-only) for read-only devices."""
        device = BlockDevice(
            path="/dev/sr0",
            size_bytes=16000000000,
            size_label="14.9 GiB",
            model="DVD-ROM",
            vendor="PIONEER",
            transport="ata",
            removable=False,
            read_only=True,
        )
        self.assertIn("(read-only)", device.pretty_name)

    def test_pretty_name_includes_tails_indicator(self) -> None:
        """Pretty name shows [Tails installed] for devices with Tails."""
        device = BlockDevice(
            path="/dev/sdb",
            size_bytes=16000000000,
            size_label="14.9 GiB",
            model="USB Drive",
            vendor="SanDisk",
            transport="usb",
            removable=True,
            has_tails=True,
        )
        self.assertIn("[Tails installed]", device.pretty_name)


class SourceModeTests(unittest.TestCase):
    def test_source_mode_values(self) -> None:
        """SourceMode enum has expected values."""
        self.assertEqual(SourceMode.RUNNING.value, "running")
        self.assertEqual(SourceMode.LOCAL.value, "local")
        self.assertEqual(SourceMode.REMOTE.value, "remote")


class VersionAssetsTests(unittest.TestCase):
    def test_version_assets_contains_urls(self) -> None:
        """VersionAssets contains all required URL fields."""
        assets = VersionAssets(
            version="6.12",
            directory_url="https://download.tails.net/stable/6.12/",
            iso_url="https://download.tails.net/stable/6.12/tails-amd64-6.12.iso",
            img_url="https://download.tails.net/stable/6.12/tails-amd64-6.12.img",
            sig_url="https://download.tails.net/stable/6.12/tails-amd64-6.12.iso.sig",
            sha256_url="https://download.tails.net/stable/6.12/tails-amd64-6.12.img.sha256",
        )
        self.assertEqual(assets.version, "6.12")
        self.assertIn(".iso", assets.iso_url)
        self.assertIn(".img", assets.img_url)
        self.assertIn(".sig", assets.sig_url)


class AppStateTests(unittest.TestCase):
    def test_default_state_values(self) -> None:
        """AppState has sensible defaults."""
        state = AppState()
        self.assertEqual(state.status_message, "Ready.")
        self.assertEqual(state.source_mode, SourceMode.LOCAL)
        self.assertFalse(state.versions_loading)
        self.assertFalse(state.devices_loading)

    def test_state_tracks_running_tails_info(self) -> None:
        """AppState can track running Tails information."""
        state = AppState(
            running_tails_available=True,
            running_tails_version="6.12",
            running_tails_device="/dev/sdb",
            source_mode=SourceMode.RUNNING,
        )
        self.assertTrue(state.running_tails_available)
        self.assertEqual(state.running_tails_version, "6.12")
        self.assertEqual(state.running_tails_device, "/dev/sdb")


if __name__ == "__main__":
    unittest.main()
