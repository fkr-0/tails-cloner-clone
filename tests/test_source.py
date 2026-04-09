import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from tails_cloner.source import (
    is_running_tails,
    get_running_tails_version,
    get_running_tails_device,
    get_running_tails_size_bytes,
    RunningLiveSystemSource,
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

        with patch("os.path.exists", side_effect=mock_exists):
            with patch("os.path.isdir", side_effect=mock_isdir):
                self.assertTrue(is_running_tails())


class RunningTailsVersionTests(unittest.TestCase):
    def test_get_version_from_file(self) -> None:
        """Read version from Tails.version file."""
        version_content = "6.12\n"
        mock_open = unittest.mock.mock_open(read_data=version_content)

        with patch("os.path.exists", return_value=True):
            with patch("builtins.open", mock_open):
                version = get_running_tails_version()
                self.assertEqual(version, "6.12")

    def test_get_version_returns_none_when_missing(self) -> None:
        """Return None when version file doesn't exist."""
        with patch("os.path.exists", return_value=False):
            self.assertIsNone(get_running_tails_version())


class RunningLiveSystemSourceTests(unittest.TestCase):
    def test_validate_fails_when_not_running_tails(self) -> None:
        """Source validation fails when not running from Tails."""
        with patch("tails_cloner.source.is_running_tails", return_value=False):
            source = RunningLiveSystemSource()
            with self.assertRaises(RuntimeError):
                source.validate()

    def test_validate_succeeds_when_running_tails(self) -> None:
        """Source validation succeeds when running from Tails."""
        with patch("tails_cloner.source.is_running_tails", return_value=True):
            with patch("os.path.exists", return_value=True):
                source = RunningLiveSystemSource()
                source.validate()  # Should not raise

    def test_get_iso_path_when_present(self) -> None:
        """Return ISO path when it exists."""
        with patch("tails_cloner.source.is_running_tails", return_value=True):
            with patch("os.path.exists", return_value=True):
                source = RunningLiveSystemSource()
                iso_path = source.get_iso_path()
                self.assertEqual(iso_path, Path("/lib/live/mount/medium/live/Tails.iso"))

    def test_get_iso_path_when_missing(self) -> None:
        """Return None when ISO doesn't exist."""
        with patch("tails_cloner.source.is_running_tails", return_value=True):
            with patch("os.path.exists", return_value=False):
                source = RunningLiveSystemSource()
                iso_path = source.get_iso_path()
                self.assertIsNone(iso_path)


if __name__ == "__main__":
    unittest.main()
