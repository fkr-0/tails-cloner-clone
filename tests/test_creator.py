import tempfile
import unittest
from pathlib import Path

from tails_cloner.creator import build_clone_command, clone_image_to_device


class CloneCommandTests(unittest.TestCase):
    def test_build_clone_command_prefers_pkexec_and_progress(self) -> None:
        command = build_clone_command("/tmp/tails.iso", "/dev/sdb")

        self.assertEqual(
            command,
            [
                "pkexec",
                "dd",
                "if=/tmp/tails.iso",
                "of=/dev/sdb",
                "bs=4M",
                "status=progress",
                "oflag=direct",
                "conv=fsync",
            ],
        )

    def test_clone_image_to_device_invokes_runner(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            image_path = Path(tmpdir) / "tails.iso"
            image_path.write_bytes(b"test")
            seen = {}

            def fake_run(command, progress_callback):
                seen["command"] = command
                progress_callback("copied")
                return 0

            progress = []
            clone_image_to_device(
                image_path=image_path,
                device_path="/dev/sdb",
                run_command=fake_run,
                progress_callback=progress.append,
            )

        self.assertEqual(seen["command"][0], "pkexec")
        self.assertEqual(progress, ["copied"])


if __name__ == "__main__":
    unittest.main()
