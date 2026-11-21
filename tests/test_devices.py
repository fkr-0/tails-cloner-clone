import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tails_cloner.devices import (
    MIN_INSTALLATION_SIZE_GB,
    DeviceService,
    find_stable_device_path,
    format_bytes_as_gib,
    parse_lsblk_json,
)


class DeviceParsingTests(unittest.TestCase):
    def test_live_partition_missing_from_lsblk_fails_closed_despite_plausible_name(self) -> None:
        # /dev/sdb1 alone does not prove an ancestry edge to /dev/sdb.
        payload = {"blockdevices": [
            {"path": "/dev/sdb", "type": "disk", "size": str(32 * 1024**3)},
            {"path": "/dev/sdc", "type": "disk", "size": str(32 * 1024**3)},
        ]}
        disks = parse_lsblk_json(payload, running_live_source="/dev/sdb1")
        self.assertTrue(all(not disk.selectable for disk in disks))

    def test_root_by_path_alias_resolves_to_disk_partition(self) -> None:
        payload = {"blockdevices": [
            {"path": "/dev/sdb", "type": "disk", "size": str(32 * 1024**3),
             "children": [{"path": "/dev/sdb1", "type": "part", "maj:min": "8:17"}]},
            {"path": "/dev/sdc", "type": "disk", "size": str(32 * 1024**3)},
        ]}
        alias = "/dev/disk/by-path/pci-root-part1"
        original = Path.resolve

        def resolve(path: Path, *, strict: bool = False) -> Path:
            return Path("/dev/sdb1") if str(path) == alias else original(path, strict=strict)

        with patch.object(Path, "resolve", resolve):
            self.assertEqual([disk.selectable for disk in parse_lsblk_json(
                payload, running_root_source=alias)], [False, True])

    def test_multi_member_md_root_protects_every_member_disk(self) -> None:
        payload = {"blockdevices": [
            {"path": disk, "type": "disk", "size": str(32 * 1024**3),
             "children": [{"path": disk + "1", "type": "part", "children": [
                 {"path": "/dev/md0", "type": "raid1", "maj:min": "9:0"}]}]}
            for disk in ("/dev/sdb", "/dev/sdc")
        ] + [{"path": "/dev/sdd", "type": "disk", "size": str(32 * 1024**3)}]}
        self.assertEqual([disk.selectable for disk in parse_lsblk_json(
            payload, running_root_source="/dev/md0")], [False, False, True])

    def test_root_dm_on_loop_backing_fails_closed_instead_of_selective_protection(self) -> None:
        payload = {"blockdevices": [
            {"path": "/dev/sdb", "type": "disk", "size": str(32 * 1024**3),
             "children": [{"path": "/dev/loop0", "type": "loop", "maj:min": "7:0",
                           "children": [{"path": "/dev/mapper/root", "type": "crypt", "maj:min": "253:0"}]}]},
            {"path": "/dev/sdc", "type": "disk", "size": str(32 * 1024**3)},
        ]}
        # The loop's file can reside on sdc even if lsblk displays it under sdb.
        self.assertTrue(all(not disk.selectable for disk in parse_lsblk_json(
            payload, running_root_source="/dev/mapper/root")))

    def test_direct_loop_live_source_fails_closed(self) -> None:
        payload = {"blockdevices": [
            {"path": "/dev/sdb", "type": "disk", "size": str(32 * 1024**3),
             "children": [{"path": "/dev/loop0", "type": "loop", "maj:min": "7:0"}]},
            {"path": "/dev/sdc", "type": "disk", "size": str(32 * 1024**3)},
        ]}
        self.assertTrue(all(not disk.selectable for disk in parse_lsblk_json(
            payload, running_live_source="/dev/loop0")))

    def test_multi_pv_mapper_protects_both_backing_disks_not_third(self) -> None:
        payload = {"blockdevices": [
            {"path": disk, "type": "disk", "size": str(32 * 1024**3),
             "children": [{"path": disk + "1", "type": "part", "children": [
                 {"path": "/dev/mapper/root", "type": "lvm", "maj:min": "253:2"}]}]}
            for disk in ("/dev/sdb", "/dev/sdc")
        ] + [{"path": "/dev/sdd", "type": "disk", "size": str(32 * 1024**3)}]}
        disks = parse_lsblk_json(payload, running_root_source="/dev/mapper/root")
        self.assertEqual([disk.selectable for disk in disks], [False, False, True])

    def test_missing_mapper_major_minor_fails_closed(self) -> None:
        payload = {"blockdevices": [
            {"path": "/dev/sdb", "type": "disk", "size": str(32 * 1024**3),
             "children": [{"path": "/dev/mapper/root", "type": "crypt"}]},
            {"path": "/dev/sdc", "type": "disk", "size": str(32 * 1024**3)},
        ]}
        self.assertTrue(all(not disk.selectable for disk in parse_lsblk_json(
            payload, running_root_source="/dev/mapper/root")))

    def test_malformed_mapper_major_minor_fails_closed(self) -> None:
        payload = {"blockdevices": [
            {"path": "/dev/sdb", "type": "disk", "size": str(32 * 1024**3),
             "children": [{"path": "/dev/mapper/root", "type": "crypt", "maj:min": "bad:id"}]},
            {"path": "/dev/sdc", "type": "disk", "size": str(32 * 1024**3)},
        ]}
        self.assertTrue(all(not disk.selectable for disk in parse_lsblk_json(
            payload, running_root_source="/dev/mapper/root")))

    def test_conflicting_mapper_major_minor_fails_closed(self) -> None:
        payload = {"blockdevices": [
            {"path": disk, "type": "disk", "size": str(32 * 1024**3),
             "children": [{"path": "/dev/mapper/root", "type": "lvm", "maj:min": mm}]}
            for disk, mm in (("/dev/sdb", "253:2"), ("/dev/sdc", "253:3"))
        ] + [{"path": "/dev/sdd", "type": "disk", "size": str(32 * 1024**3)}]}
        self.assertTrue(all(not disk.selectable for disk in parse_lsblk_json(
            payload, running_root_source="/dev/mapper/root")))

    def test_root_by_id_alias_resolves_to_partition_and_protects_ancestor(self) -> None:
        payload = {"blockdevices": [
            {"path": "/dev/sdb", "type": "disk", "size": str(32 * 1024**3),
             "children": [{"path": "/dev/sdb1", "type": "part", "maj:min": "8:17"}]},
            {"path": "/dev/sdc", "type": "disk", "size": str(32 * 1024**3)},
        ]}
        alias = "/dev/disk/by-id/usb-root-part1"
        actual_resolve = Path.resolve

        def resolve(path: Path, *, strict: bool = False) -> Path:
            return Path("/dev/sdb1") if str(path) == alias else actual_resolve(path, strict=strict)

        with patch.object(Path, "resolve", resolve):
            disks = parse_lsblk_json(payload, running_root_source=alias)
        self.assertEqual([disk.selectable for disk in disks], [False, True])

    def test_missing_root_by_id_alias_fails_closed(self) -> None:
        payload = {"blockdevices": [
            {"path": disk, "type": "disk", "size": str(32 * 1024**3)}
            for disk in ("/dev/sdb", "/dev/sdc")
        ]}
        disks = parse_lsblk_json(payload, running_root_source="/dev/disk/by-id/nonexistent-root-part1")
        self.assertTrue(all(not disk.selectable for disk in disks))

    def test_attached_mapper_source_protects_only_its_backing_disk(self) -> None:
        payload = {"blockdevices": [
            {"path": "/dev/sdb", "type": "disk", "size": str(32 * 1024**3),
             "children": [{"path": "/dev/sdb1", "type": "part", "children": [
                 {"path": "/dev/mapper/usb-live", "type": "crypt", "maj:min": "253:0"}]}]},
            {"path": "/dev/sdc", "type": "disk", "size": str(32 * 1024**3)},
        ]}
        devices = parse_lsblk_json(payload, running_live_source="/dev/mapper/usb-live")
        self.assertFalse(devices[0].selectable)
        self.assertTrue(devices[1].selectable)

    def test_unresolved_mapper_source_fails_closed_for_all_disks(self) -> None:
        payload = {"blockdevices": [
            {"path": name, "type": "disk", "size": str(32 * 1024**3)}
            for name in ("/dev/sdb", "/dev/sdc")
        ]}
        self.assertTrue(all(not dev.selectable for dev in parse_lsblk_json(
            payload, running_live_source="/dev/mapper/usb-live")))

    def test_root_by_uuid_alias_protects_only_resolved_ancestor(self) -> None:
        payload = {"blockdevices": [
            {"path": "/dev/sdb", "type": "disk", "size": str(32 * 1024**3),
             "children": [{"path": "/dev/sdb1", "type": "part", "uuid": "root-uuid", "maj:min": "8:17"}]},
            {"path": "/dev/sdc", "type": "disk", "size": str(32 * 1024**3)},
        ]}
        devices = parse_lsblk_json(payload, running_root_source="/dev/disk/by-uuid/root-uuid")
        self.assertFalse(devices[0].selectable)
        self.assertTrue(devices[1].selectable)

    def test_unresolved_root_alias_fails_closed_for_all_disks(self) -> None:
        payload = {"blockdevices": [
            {"path": name, "type": "disk", "size": str(32 * 1024**3)}
            for name in ("/dev/sdb", "/dev/sdc")
        ]}
        self.assertTrue(all(not dev.selectable for dev in parse_lsblk_json(
            payload, running_root_source="/dev/disk/by-uuid/root")))

    def test_mount_and_boot_marker_disagreement_fails_closed(self) -> None:
        payload = {"blockdevices": [
            {"path": name, "type": "disk", "size": str(32 * 1024**3)}
            for name in ("/dev/sdb", "/dev/sdc")
        ]}

        def run(command, **_kwargs):
            return subprocess.CompletedProcess(command, 0,
                                               stdout=json.dumps(payload) if command[0] == "lsblk" else "overlay\n",
                                               stderr="")

        with (patch("tails_cloner.devices.is_running_tails", return_value=True),
              patch("tails_cloner.devices.has_running_tails_boot_signals", return_value=False),
              patch("tails_cloner.devices.get_running_tails_device", return_value="/dev/sdb1"),
              patch("tails_cloner.devices.find_stable_device_path", return_value="")):
            self.assertTrue(all(not dev.selectable for dev in DeviceService(run=run).list_devices()))

    def test_unresolved_running_live_source_protects_every_candidate(self) -> None:
        payload = {"blockdevices": [{"path": "/dev/sdb", "type": "disk", "size": str(32 * 1024**3), "rm": True}]}
        device = parse_lsblk_json(payload, live_source_ambiguous=True)[0]
        self.assertFalse(device.selectable)

    def test_service_refuses_all_when_running_medium_was_removed(self) -> None:
        payload = {"blockdevices": [{"path": "/dev/sdc", "type": "disk", "size": str(32 * 1024**3), "rm": True}]}

        def run(command, **_kwargs):
            output = json.dumps(payload) if command[0] == "lsblk" else "overlay\n"
            return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")

        with (
            patch("tails_cloner.devices.is_running_tails", return_value=True),
            patch("tails_cloner.devices.has_running_tails_boot_signals", return_value=True),
            patch("tails_cloner.devices.get_running_tails_device", return_value="/dev/sdb1"),
            patch("tails_cloner.devices.find_stable_device_path", return_value=""),
        ):
            device = DeviceService(run=run).list_devices()[0]
        self.assertFalse(device.selectable)

    def test_service_refuses_all_when_live_medium_is_loop_backed(self) -> None:
        payload = {"blockdevices": [{"path": "/dev/sdc", "type": "disk", "size": str(32 * 1024**3), "rm": True}]}

        def run(command, **_kwargs):
            output = json.dumps(payload) if command[0] == "lsblk" else "overlay\n"
            return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")

        with (
            patch("tails_cloner.devices.is_running_tails", return_value=True),
            patch("tails_cloner.devices.has_running_tails_boot_signals", return_value=True),
            patch("tails_cloner.devices.get_running_tails_device", return_value="/dev/loop0"),
            patch("tails_cloner.devices.find_stable_device_path", return_value=""),
        ):
            device = DeviceService(run=run).list_devices()[0]
        self.assertFalse(device.selectable)
        self.assertTrue(device.is_current_system_device)

    def test_service_refuses_targets_when_tails_boot_signals_disagree_with_mount(self) -> None:
        payload = {"blockdevices": [{"path": "/dev/sdb", "type": "disk", "size": str(32 * 1024**3), "rm": True}]}

        def run(command, **_kwargs):
            output = json.dumps(payload) if command[0] == "lsblk" else "overlay\n"
            return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")

        with (
            patch("tails_cloner.devices.is_running_tails", return_value=False),
            patch("tails_cloner.devices.has_running_tails_boot_signals", return_value=True),
            patch("tails_cloner.devices.find_stable_device_path", return_value=""),
        ):
            device = DeviceService(run=run).list_devices()[0]
        self.assertFalse(device.selectable)

    def test_resolved_running_live_source_protects_alias_disk_without_mountpoints(self) -> None:
        payload = {"blockdevices": [
            {"path": "/dev/sdb", "type": "disk", "size": str(32 * 1024**3), "rm": True,
             "children": [{"path": "/dev/sdb1", "type": "part", "maj:min": "8:17"}]},
            {"path": "/dev/sdc", "type": "disk", "size": str(32 * 1024**3), "rm": True},
        ]}
        devices = parse_lsblk_json(payload, running_live_source="/dev/sdb1")
        self.assertFalse(devices[0].selectable)
        self.assertTrue(devices[1].selectable)

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
        self.assertTrue(device.is_current_system_device)
        self.assertFalse(device.selectable)
        self.assertIn("currently running system", device.disabled_reason)
        self.assertEqual(device.status_label, "Running system")
        self.assertIn("[Running system]", device.pretty_name)

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
        self.assertFalse(device.is_current_system_device)
        self.assertEqual(device.status_label, "Current OS storage")
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
        self.assertTrue(device.is_current_system_device)
        self.assertEqual(device.status_label, "Running system")
        self.assertFalse(device.selectable)

    def test_findmnt_root_source_marks_backing_disk_when_lsblk_mountpoint_is_not_reported(self) -> None:
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
                    "children": [
                        {
                            "path": "/dev/nvme0n1p2",
                            "type": "part",
                            "mountpoints": [None],
                            "children": [
                                {
                                    "path": "/dev/mapper/root",
                                    "type": "crypt",
                                    "fstype": "btrfs",
                                    "mountpoints": [None],
                                }
                            ],
                        }
                    ],
                }
            ]
        }

        device = parse_lsblk_json(payload, running_root_source="/dev/mapper/root[/@]")[0]

        self.assertTrue(device.is_current_system_device)
        self.assertFalse(device.selectable)
        self.assertEqual(device.status_label, "Running system")

    def test_whole_disk_root_filesystem_is_protected(self) -> None:
        payload = {
            "blockdevices": [
                {
                    "path": "/dev/vda",
                    "size": str(64 * 1024**3),
                    "model": "VM Disk",
                    "vendor": "Virtio",
                    "rm": False,
                    "hotplug": False,
                    "tran": "virtio",
                    "type": "disk",
                    "ro": False,
                    "fstype": "ext4",
                    "mountpoints": ["/"],
                }
            ]
        }

        device = parse_lsblk_json(payload)[0]

        self.assertTrue(device.is_current_system_device)
        self.assertTrue(device.is_host_system_device)
        self.assertFalse(device.selectable)

    def test_device_service_uses_findmnt_root_source_for_running_system_label(self) -> None:
        payload = {
            "blockdevices": [
                {
                    "path": "/dev/sda",
                    "size": str(64 * 1024**3),
                    "model": "Disk",
                    "vendor": "ATA",
                    "rm": False,
                    "hotplug": False,
                    "tran": "sata",
                    "type": "disk",
                    "ro": False,
                    "children": [{"path": "/dev/sda2", "type": "part", "mountpoints": [None]}],
                }
            ]
        }
        commands: list[list[str]] = []

        def run(command, **_kwargs):
            commands.append(command)
            if command[0] == "findmnt":
                return subprocess.CompletedProcess(command, 0, stdout="/dev/sda2\n", stderr="")
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

        device = DeviceService(run=run).list_devices()[0]

        self.assertTrue(device.is_current_system_device)
        self.assertEqual(device.status_label, "Running system")
        self.assertEqual(commands[0], ["findmnt", "-n", "-o", "SOURCE", "--target", "/"])

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
