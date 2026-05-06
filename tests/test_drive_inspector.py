from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tails_cloner.drive_inspector import (
    has_tails_installation,
    inspect_drive_tails_facts,
    read_tails_version_from_unmounted_partition,
)


class _RunResult:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout


def test_has_tails_installation_true_for_gpt_vfat_tails() -> None:
    disk = {"fstype": "", "pttype": "gpt"}
    parts = [{"fstype": "vfat", "label": "Tails"}]
    assert has_tails_installation(disk, parts)


def test_has_tails_installation_false_for_iso9660() -> None:
    disk = {"fstype": "iso9660", "pttype": "gpt"}
    parts = [{"fstype": "vfat", "label": "Tails"}]
    assert not has_tails_installation(disk, parts)


def test_inspect_drive_detects_version_and_persistence() -> None:
    with TemporaryDirectory() as tmpdir:
        mountpoint = Path(tmpdir)
        (mountpoint / "live").mkdir(parents=True)
        (mountpoint / "live" / "Tails.version").write_text("7.7.2\n", encoding="utf-8")

        payload = {
            "blockdevices": [
                {
                    "path": "/dev/sdb",
                    "type": "disk",
                    "pttype": "gpt",
                    "fstype": "",
                    "children": [
                        {
                            "path": "/dev/sdb1",
                            "type": "part",
                            "fstype": "vfat",
                            "label": "Tails",
                            "mountpoints": [str(mountpoint)],
                            "size": "1073741824",
                        },
                        {
                            "path": "/dev/sdb2",
                            "type": "part",
                            "fstype": "ext4",
                            "label": "persistence",
                            "mountpoints": [None],
                            "size": "7516192768",
                        },
                    ],
                }
            ]
        }

        def fake_run(*args, **kwargs):
            del args, kwargs
            import json

            return _RunResult(json.dumps(payload))

        with patch("tails_cloner.drive_inspector.is_running_tails", return_value=False):
            facts = inspect_drive_tails_facts("/dev/sdb", run=fake_run)

        assert facts.tails_installed is True
        assert facts.tails_version == "7.7.2"
        assert facts.persistence_configured is True
        assert facts.persistence_partition_size_bytes == 7516192768
        assert facts.running_tails_on_this_drive is False


def test_inspect_drive_detects_running_tails_booted_from_same_disk() -> None:
    payload = {
        "blockdevices": [
            {
                "path": "/dev/sdb",
                "type": "disk",
                "pttype": "gpt",
                "fstype": "",
                "children": [],
            }
        ]
    }

    def fake_run(*args, **kwargs):
        del args, kwargs
        import json

        return _RunResult(json.dumps(payload))

    with (
        patch("tails_cloner.drive_inspector.is_running_tails", return_value=True),
        patch("tails_cloner.drive_inspector.get_running_tails_device", return_value="/dev/sdb1"),
    ):
        facts = inspect_drive_tails_facts("/dev/sdb", run=fake_run)

    assert facts.running_tails_on_this_drive is True


def test_inspect_drive_handles_missing_block_device() -> None:
    payload = {"blockdevices": []}

    def fake_run(*args, **kwargs):
        del args, kwargs
        import json

        return _RunResult(json.dumps(payload))

    with patch("tails_cloner.drive_inspector.is_running_tails", return_value=False):
        facts = inspect_drive_tails_facts("/dev/sdz", run=fake_run)

    assert facts.tails_installed is False
    assert facts.tails_version is None
    assert facts.persistence_configured is False
    assert facts.persistence_partition_size_bytes is None


def test_unmounted_version_detection_reports_privilege_required_by_default() -> None:
    version, error, requires_privilege = read_tails_version_from_unmounted_partition("/dev/sdb1")

    assert version is None
    assert error is not None
    assert "privileged" in error
    assert requires_privilege is True


def test_inspect_drive_marks_unmounted_version_as_privileged() -> None:
    payload = {
        "blockdevices": [
            {
                "path": "/dev/sdb",
                "type": "disk",
                "pttype": "gpt",
                "fstype": "",
                "children": [
                    {
                        "path": "/dev/sdb1",
                        "type": "part",
                        "fstype": "vfat",
                        "label": "Tails",
                        "mountpoints": [None],
                        "size": "1073741824",
                    }
                ],
            }
        ]
    }

    def fake_run(*args, **kwargs):
        del args, kwargs
        import json

        return _RunResult(json.dumps(payload))

    with patch("tails_cloner.drive_inspector.is_running_tails", return_value=False):
        facts = inspect_drive_tails_facts("/dev/sdb", run=fake_run)

    assert facts.tails_installed is True
    assert facts.tails_version is None
    assert facts.version_detection_requires_privilege is True
    assert facts.version_detection_error is not None
