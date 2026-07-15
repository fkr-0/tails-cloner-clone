import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tails_cloner.source import (
    AttachedLiveSystemSource,
    RunningLiveSystemSource,
    SourceType,
    get_parent_disk_path,
    get_running_tails_version,
    is_running_tails,
)


class RunningTailsDetectionTests(unittest.TestCase):
    def test_not_running_tails_when_mount_missing(self) -> None:
        """When Tails mount point doesn't exist, we're not running Tails."""
        with patch("os.path.exists", return_value=False):
            self.assertFalse(is_running_tails())

    def test_not_running_tails_when_version_file_missing(self) -> None:
        """When version file doesn't exist, not a valid Tails system."""
        def mock_exists(path):
            return path == "/lib/live/mount/medium"  # Mount exists but not version file

        with patch("os.path.exists", side_effect=mock_exists):
            self.assertFalse(is_running_tails())

    def test_running_tails_when_all_present(self) -> None:
        """When mount and version file exist, we're running Tails."""
        def mock_exists(path):
            return path in [
                "/lib/live/mount/medium",
                "/lib/live/mount/medium/live/Tails.version",
            ]

        def mock_isdir(path):
            return path == "/lib/live/mount/medium"

        with (
            patch("os.path.exists", side_effect=mock_exists),
            patch("os.path.isdir", side_effect=mock_isdir),
        ):
            self.assertTrue(is_running_tails())


class RunningTailsVersionTests(unittest.TestCase):
    def test_get_version_from_file(self) -> None:
        """Read version from Tails.version file."""
        version_content = "6.12\n"
        mock_open = unittest.mock.mock_open(read_data=version_content)

        with patch("os.path.exists", return_value=True), patch("builtins.open", mock_open):
            version = get_running_tails_version()
            self.assertEqual(version, "6.12")

    def test_get_version_returns_none_when_missing(self) -> None:
        """Return None when version file doesn't exist."""
        with patch("os.path.exists", return_value=False):
            self.assertIsNone(get_running_tails_version())


class ParentDiskPathTests(unittest.TestCase):
    def test_get_parent_disk_for_standard_partition(self) -> None:
        self.assertEqual(get_parent_disk_path("/dev/sdb1"), "/dev/sdb")

    def test_get_parent_disk_for_nvme_partition(self) -> None:
        self.assertEqual(get_parent_disk_path("/dev/nvme0n1p2"), "/dev/nvme0n1")

    def test_get_parent_disk_for_loop_partition(self) -> None:
        self.assertEqual(get_parent_disk_path("/dev/loop0p1"), "/dev/loop0")

    def test_get_parent_disk_returns_input_for_disk(self) -> None:
        self.assertEqual(get_parent_disk_path("/dev/sdb"), "/dev/sdb")


class AttachedLiveSystemSourceTests(unittest.TestCase):
    def test_validate_succeeds_for_mounted_tails_like_live_medium(self) -> None:
        with TemporaryDirectory() as tmpdir:
            mount_point = Path(tmpdir)
            (mount_point / "live").mkdir(parents=True)
            (mount_point / "live" / "Tails.version").write_text("7.7.2\n", encoding="utf-8")
            source = AttachedLiveSystemSource(device_path="/dev/sdb1", mount_point=mount_point)

            source.validate()

            self.assertEqual(source.source_type, SourceType.ATTACHED_LIVE_SYSTEM)
            self.assertEqual(source.version, "7.7.2")
            self.assertEqual(source.parent_device, "/dev/sdb")

    def test_validate_fails_for_missing_tails_version(self) -> None:
        with TemporaryDirectory() as tmpdir:
            source = AttachedLiveSystemSource(device_path="/dev/sdb1", mount_point=Path(tmpdir))

            with self.assertRaises(RuntimeError):
                source.validate()

    def test_validate_fails_for_non_device_path(self) -> None:
        with TemporaryDirectory() as tmpdir:
            mount_point = Path(tmpdir)
            (mount_point / "live").mkdir(parents=True)
            (mount_point / "live" / "Tails.version").write_text("7.7.2\n", encoding="utf-8")
            source = AttachedLiveSystemSource(device_path="/tmp/source.img", mount_point=mount_point)

            with self.assertRaises(ValueError):
                source.validate()

    def test_target_is_source_device_compares_parent_disk(self) -> None:
        with TemporaryDirectory() as tmpdir:
            source = AttachedLiveSystemSource(device_path="/dev/sdb1", mount_point=Path(tmpdir))

            self.assertTrue(source.target_is_source_device("/dev/sdb"))
            self.assertTrue(source.target_is_source_device("/dev/sdb2"))
            self.assertFalse(source.target_is_source_device("/dev/sdc"))


class RunningLiveSystemSourceTests(unittest.TestCase):
    def test_validate_fails_when_not_running_tails(self) -> None:
        """Source validation fails when not running from Tails."""
        with patch("tails_cloner.source.is_running_tails", return_value=False):
            source = RunningLiveSystemSource()
            with self.assertRaises(RuntimeError):
                source.validate()

    def test_validate_succeeds_when_running_tails(self) -> None:
        """Source validation succeeds when running from Tails."""
        with TemporaryDirectory() as tmpdir:
            mount_point = Path(tmpdir)
            with patch("tails_cloner.source.is_running_tails", return_value=True):
                source = RunningLiveSystemSource(mount_point=mount_point)
                source.validate()  # Should not raise

    def test_get_iso_path_when_present(self) -> None:
        """Return ISO path when it exists."""
        with TemporaryDirectory() as tmpdir:
            mount_point = Path(tmpdir)
            (mount_point / "live").mkdir(parents=True)
            expected_iso = mount_point / "live" / "Tails.iso"
            expected_iso.touch()
            source = RunningLiveSystemSource(mount_point=mount_point)
            iso_path = source.get_iso_path()
            self.assertEqual(iso_path, expected_iso)

    def test_get_iso_path_when_missing(self) -> None:
        """Return None when ISO doesn't exist."""
        with TemporaryDirectory() as tmpdir:
            mount_point = Path(tmpdir)
            (mount_point / "live").mkdir(parents=True)
            source = RunningLiveSystemSource(mount_point=mount_point)
            iso_path = source.get_iso_path()
            self.assertIsNone(iso_path)


if __name__ == "__main__":
    unittest.main()
