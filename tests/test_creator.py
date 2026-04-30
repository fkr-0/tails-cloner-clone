import tempfile
import unittest
from pathlib import Path

from tails_cloner.creator import build_clone_command, clone_image_to_device
from tails_cloner.models import PostWriteOptions


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
            seen: dict[str, object] = {}

            def fake_run(command: list[str], progress_callback):
                seen["command"] = command
                progress_callback("copied")
                return 0

            progress: list[str] = []
            clone_image_to_device(
                image_path=image_path,
                device_path="/dev/sdb",
                run_command=fake_run,
                progress_callback=progress.append,
            )

        self.assertEqual(seen["command"][0], "pkexec")
        self.assertEqual(progress, ["copied"])

    def test_clone_image_to_device_runs_post_write_hook_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            image_path = Path(tmpdir) / "tails.iso"
            image_path.write_bytes(b"test")
            seen: dict[str, object] = {}

            def fake_run(command: list[str], progress_callback):
                seen["command"] = command
                progress_callback("copied")
                return 0

            def fake_post_write(device_path: str, options: PostWriteOptions, progress_callback) -> None:
                seen["post_write_device"] = device_path
                seen["post_write_enabled"] = options.enabled
                if progress_callback is not None:
                    progress_callback("post-write done")

            progress: list[str] = []
            clone_image_to_device(
                image_path=image_path,
                device_path="/dev/sdb",
                run_command=fake_run,
                progress_callback=progress.append,
                post_write_options=PostWriteOptions(enabled=True),
                post_write_runner=fake_post_write,
            )

        self.assertEqual(seen["post_write_device"], "/dev/sdb")
        self.assertEqual(seen["post_write_enabled"], True)
        self.assertEqual(progress, ["copied", "post-write done"])

    def test_clone_image_to_device_still_calls_post_write_runner_with_disabled_options(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            image_path = Path(tmpdir) / "tails.iso"
            image_path.write_bytes(b"test")
            seen: dict[str, object] = {}

            def fake_run(command: list[str], progress_callback):
                seen["command"] = command
                return 0

            def fake_post_write(device_path: str, options: PostWriteOptions, progress_callback) -> None:
                seen["post_write_device"] = device_path
                seen["post_write_enabled"] = options.enabled

            clone_image_to_device(
                image_path=image_path,
                device_path="/dev/sdb",
                run_command=fake_run,
                post_write_runner=fake_post_write,
            )

        self.assertEqual(seen["post_write_device"], "/dev/sdb")
        self.assertEqual(seen["post_write_enabled"], False)


if __name__ == "__main__":
    unittest.main()
