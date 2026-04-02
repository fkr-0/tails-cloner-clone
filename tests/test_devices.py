import unittest

from tails_cloner.devices import parse_lsblk_json


class DeviceParsingTests(unittest.TestCase):
    def test_parse_lsblk_json_keeps_only_removable_disks(self) -> None:
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
                },
                {
                    "path": "/dev/sdb",
                    "size": "16008609792",
                    "model": "USB DISK",
                    "vendor": "SanDisk",
                    "rm": True,
                    "hotplug": True,
                    "tran": "usb",
                    "type": "disk",
                },
            ]
        }

        devices = parse_lsblk_json(payload)

        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].path, "/dev/sdb")
        self.assertEqual(devices[0].transport, "usb")
        self.assertEqual(devices[0].size_bytes, 16008609792)
        self.assertIn("14.9 GiB", devices[0].size_label)


if __name__ == "__main__":
    unittest.main()
