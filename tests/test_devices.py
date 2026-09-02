import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tails_cloner.devices import (
    MIN_INSTALLATION_SIZE_GB,
    find_stable_device_path,
    format_bytes_as_gib,
    parse_lsblk_json,
)


class DeviceParsingTests(unittest.TestCase):
    def test_stable_device_path_prefers_wwn_alias_and_ignores_partition_aliases(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            device = root / "dev/sdb"
            by_id = root / "dev/disk/by-id"
            device.parent.mkdir(parents=True)
            by_id.mkdir(parents=True)
            device.touch()
            (by_id / "usb-example-serial").symlink_to(device)
            (by_id / "wwn-0x1234").symlink_to(device)
            (by_id / "wwn-0x1234-part1").symlink_to(device)

            stable_path = find_stable_device_path(str(device), by_id)

        self.assertEqual(stable_path, str(by_id / "wwn-0x1234"))

    def test_by_id_alias_is_used_as_identity_when_wwn_and_serial_are_missing(self) -> None:
        device = parse_lsblk_json(
            {
                "blockdevices": [
                    {
                        "path": "/dev/sdb",
                        "size": str(32 * 1024**3),
                        "model": "Disk",
                        "vendor": "Vendor",
                        "rm": True,
                        "hotplug": True,
                        "tran": "usb",
                        "type": "disk",
                        "ro": False,
                    }
                ]
            }
        )[0]
        device.stable_path = "/dev/disk/by-id/usb-stable-example"

        self.assertEqual(device.identity_key, ("by-id", "/dev/disk/by-id/usb-stable-example"))

    def test_parse_lsblk_json_includes_all_disks_now(self) -> None:
        """After change, we include non-removable devices too."""
        payload = {
            "blockdevices": [
                {
                    "path": "/dev/sda",
                    "size": "512110190592",
                    "model": "System Disk",
                    "vendor": "ATA",
                    "rm": False,
                    "hotplug": False,
                    "tran": "sata",
                    "type": "disk",
                    "ro": False,
                    "fstype": "",
                    "label": "",
                    "parttype": "",
                    "pttype": "",
                },
                {
                    "path": "/dev/sdb",
                    "size": "16008609792",
                    "model": "USB DISK",
                    "vendor": "SanDisk",
                    "serial": "USB-SERIAL-123",
                    "wwn": "0x500123456789abcd",
                    "maj:min": "8:16",
                    "rm": True,
                    "hotplug": True,
                    "tran": "usb",
                    "type": "disk",
                    "ro": False,
                    "fstype": "",
                    "label": "",
                    "parttype": "",
                    "pttype": "",
                },
            ]
        }

        devices = parse_lsblk_json(payload)

        self.assertEqual(len(devices), 2)
        self.assertEqual(devices[0].path, "/dev/sda")
        self.assertEqual(devices[0].removable, False)
        self.assertEqual(devices[1].path, "/dev/sdb")
        self.assertEqual(devices[1].transport, "usb")
        self.assertEqual(devices[1].removable, True)
        self.assertEqual(devices[1].size_bytes, 16008609792)
        self.assertEqual(devices[1].serial, "USB-SERIAL-123")
        self.assertEqual(devices[1].wwn, "0x500123456789abcd")
        self.assertEqual(devices[1].major_minor, "8:16")
        self.assertEqual(devices[1].identity_key, ("wwn", "0x500123456789abcd"))
        self.assertIn("USB-SERIAL-123", devices[1].pretty_name)
        self.assertIn("14.9 GiB", devices[1].size_label)

    def test_parse_lsblk_json_excludes_memory_backed_pseudo_disks(self) -> None:
        payload = {
            "blockdevices": [
                {
                    "path": "/dev/zram0",
                    "size": str(12 * 1024**3),
                    "model": "",
                    "vendor": "",
                    "rm": False,
                    "hotplug": False,
                    "tran": "",
                    "type": "disk",
                    "ro": False,
                    "fstype": "swap",
                    "label": "zram0",
                    "mountpoints": ["[SWAP]"],
                }
            ]
        }

        self.assertEqual(parse_lsblk_json(payload), [])

    def test_parse_lsblk_json_with_partitions_detects_tails(self) -> None:
        """Test that Tails detection works with partition data."""
        payload = {
            "blockdevices": [
                {
                    "path": "/dev/sdb",
                    "size": "32000000000",  # ~32GB
                    "model": "USB DISK",
                    "vendor": "SanDisk",
                    "rm": True,
                    "hotplug": True,
                    "tran": "usb",
                    "type": "disk",
                    "ro": False,
                    "fstype": "",
                    "label": "",
                    "parttype": "",
                    "pttype": "gpt",
                    "children": [
                        {
                            "name": "sdb1",
                            "path": "/dev/sdb1",
                            "type": "part",
                            "fstype": "vfat",
                            "label": "Tails",
                        }
                    ],
                }
            ]
        }

        devices = parse_lsblk_json(payload)

        self.assertEqual(len(devices), 1)
        self.assertTrue(devices[0].has_tails)
        self.assertTrue(devices[0].is_gpt)
        self.assertEqual(devices[0].label, "Tails")
        self.assertEqual(devices[0].fstype, "vfat")

    def test_current_os_disk_is_visible_but_not_selectable(self) -> None:
        payload = {
            "blockdevices": [
                {
                    "path": "/dev/nvme0n1",
                    "size": str(512 * 1024**3),
                    "model": "System Disk",
                    "vendor": "NVMe",
                    "rm": False,
                    "hotplug": False,
                    "tran": "nvme",
                    "type": "disk",
                    "ro": False,
                    "pttype": "gpt",
                    "children": [
                        {
                            "path": "/dev/nvme0n1p2",
                            "type": "part",
                            "fstype": "crypto_LUKS",
                            "mountpoints": [None],
                            "children": [
                                {
                                    "path": "/dev/mapper/root",
                                    "type": "crypt",
                                    "fstype": "ext4",
                                    "mountpoints": ["/"],
                                }
                            ],
                        }
                    ],
                }
            ]
        }

        device = parse_lsblk_json(payload)[0]

        self.assertTrue(device.is_host_system_device)
        self.assertFalse(device.selectable)
        self.assertIn("currently running operating system", device.disabled_reason)
        self.assertIn("current OS disk", device.pretty_name)

    def test_home_only_disk_is_protected_as_current_os_storage(self) -> None:
        payload = {
            "blockdevices": [
                {
                    "path": "/dev/sdb",
                    "size": str(64 * 1024**3),
                    "model": "Home Disk",
                    "vendor": "ATA",
                    "rm": False,
                    "hotplug": False,
                    "tran": "sata",
                    "type": "disk",
                    "ro": False,
                    "children": [
                        {
                            "path": "/dev/sdb1",
                            "type": "part",
                            "fstype": "ext4",
                            "mountpoints": ["/home"],
                        }
                    ],
                }
            ]
        }

        device = parse_lsblk_json(payload)[0]

        self.assertTrue(device.is_host_system_device)
        self.assertFalse(device.selectable)

    def test_running_live_medium_mount_is_protected_even_without_controller_annotation(self) -> None:
        payload = {
            "blockdevices": [
                {
                    "path": "/dev/sdb",
                    "size": str(32 * 1024**3),
                    "model": "Live USB",
                    "vendor": "USB",
                    "rm": True,
                    "hotplug": True,
                    "tran": "usb",
                    "type": "disk",
                    "ro": False,
                    "children": [
                        {
                            "path": "/dev/sdb1",
                            "type": "part",
                            "fstype": "vfat",
                            "label": "Tails",
                            "mountpoints": ["/lib/live/mount/medium"],
                        }
                    ],
                }
            ]
        }

        device = parse_lsblk_json(payload)[0]

        self.assertTrue(device.is_host_system_device)
        self.assertFalse(device.selectable)

    def test_device_size_thresholds(self) -> None:
        """Test minimum size requirements for install vs upgrade."""
        payload = {
            "blockdevices": [
                {
                    "path": "/dev/sdb",
                    "size": str(MIN_INSTALLATION_SIZE_GB * 1024**3 - 1),  # Just under min
                    "model": "Small USB",
                    "vendor": "Generic",
                    "rm": True,
                    "hotplug": True,
                    "tran": "usb",
                    "type": "disk",
                    "ro": False,
                    "fstype": "",
                    "label": "",
                    "parttype": "",
                    "pttype": "",
                }
            ]
        }

        devices = parse_lsblk_json(payload)
        self.assertFalse(devices[0].is_big_enough_for_installation)

    def test_format_bytes_as_gib(self) -> None:
        """Test byte formatting."""
        self.assertEqual(format_bytes_as_gib(0), "0.0 GiB")
        self.assertEqual(format_bytes_as_gib(1024**3), "1.0 GiB")
        self.assertEqual(format_bytes_as_gib(16 * 1024**3), "16.0 GiB")
        self.assertIn("14.9", format_bytes_as_gib(16008609792))


if __name__ == "__main__":
    unittest.main()
