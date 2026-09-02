from __future__ import annotations

import json
from contextlib import redirect_stdout
from io import StringIO
from unittest import mock

from tails_cloner import cli
from tails_cloner.__main__ import main as package_main
from tails_cloner.models import BlockDevice


def capture_json(callable_, *args):
    buffer = StringIO()
    with redirect_stdout(buffer):
        result = callable_(*args)
    return result, json.loads(buffer.getvalue())


def test_cli_help_is_available() -> None:
    parser = cli.build_cli_parser()
    assert "versions" in parser.format_help()
    assert "devices" in parser.format_help()
    assert "plan" in parser.format_help()


def test_package_main_routes_cli_subcommands() -> None:
    with (
        mock.patch("tails_cloner.__main__.cli_main", return_value=0) as cli_main,
        mock.patch("tails_cloner.app.TailsClonerApp", side_effect=AssertionError("GUI imported on CLI path")),
    ):
        assert package_main(["devices", "list"]) == 0
    cli_main.assert_called_once_with(["devices", "list"])


def test_package_main_source_does_not_import_gui_module_on_cli_path() -> None:
    main_source = __import__("pathlib").Path("src/tails_cloner/__main__.py").read_text(encoding="utf-8")
    lines_before_cli_dispatch = main_source.split("if looks_like_cli_invocation", 1)[0]

    assert "from tails_cloner.app import TailsClonerApp" not in lines_before_cli_dispatch
    assert "from tails_cloner.app import TailsClonerApp" in main_source


def test_cli_accepts_json_after_subcommand() -> None:
    with mock.patch("tails_cloner.cli.handle_devices", return_value=0) as handle_devices:
        assert cli.main(["devices", "list", "--json"]) == 0
    assert handle_devices.call_args.args[0].json is True


def test_cli_accepts_remote_index_url_after_subcommand() -> None:
    with mock.patch("tails_cloner.cli.handle_versions", return_value=0) as handle_versions:
        assert cli.main(["versions", "list", "--remote-index-url", "file:///tmp/index/"]) == 0
    assert handle_versions.call_args.args[0].remote_index_url == "file:///tmp/index/"


def test_devices_list_json_marks_running_device_not_selectable() -> None:
    running = BlockDevice(
        path="/dev/sdb",
        size_bytes=16_000_000_000,
        size_label="14.9 GiB",
        model="USB Drive",
        vendor="SanDisk",
        transport="usb",
        removable=True,
        is_running_system_device=True,
        disabled_reason="This is the device currently running Tails.",
    )
    target = BlockDevice(
        path="/dev/sdc",
        size_bytes=32_000_000_000,
        size_label="29.8 GiB",
        model="USB Drive",
        vendor="Kingston",
        transport="usb",
        removable=True,
    )

    class FakeController:
        def __init__(self):
            self.state = type("State", (), {"devices": [running, target]})()

        def _detect_running_tails(self):
            return None

        def refresh_devices(self):
            return None

        def shutdown(self):
            return None

    args = type(
        "Args",
        (),
        {
            "remote_index_url": "file:///unused",
            "devices_command": "list",
            "json": True,
        },
    )()

    with mock.patch("tails_cloner.cli._build_controller", return_value=FakeController()):
        result, payload = capture_json(cli.handle_devices, args)

    assert result == 0
    assert payload["devices"][0]["path"] == "/dev/sdb"
    assert payload["devices"][0]["is_running_system_device"] is True
    assert payload["devices"][0]["status_label"] == "Currently running Tails"
    assert payload["devices"][0]["selectable"] is False
    assert payload["devices"][1]["selectable"] is True


def test_plan_refuses_running_source_target() -> None:
    running = BlockDevice(
        path="/dev/sdb",
        size_bytes=16_000_000_000,
        size_label="14.9 GiB",
        model="USB Drive",
        vendor="SanDisk",
        transport="usb",
        removable=True,
        is_running_system_device=True,
        disabled_reason="This is the device currently running Tails.",
    )

    class FakeController:
        def __init__(self):
            self.state = type("State", (), {"devices": [running]})()

        def _detect_running_tails(self):
            return None

        def refresh_devices(self):
            return None

        def shutdown(self):
            return None

    args = type(
        "Args",
        (),
        {
            "remote_index_url": "file:///unused",
            "plan_command": "install",
            "target": "/dev/sdb",
            "image": "/tmp/tails.img",
            "running_source": False,
            "json": True,
        },
    )()

    with mock.patch("tails_cloner.cli._build_controller", return_value=FakeController()):
        result, payload = capture_json(cli.handle_plan, args)

    assert result == 1
    assert payload["would_write"] is False
    assert payload["target"]["selectable"] is False
    assert payload["blocking_errors"] == ["This is the device currently running Tails."]


def test_source_running_json(monkeypatch) -> None:
    class FakeSource:
        exists = True
        version = "7.7.2"
        device = "/dev/sdb1"
        mount_point = "/lib/live/mount/medium"

        def get_iso_path(self):
            return "/lib/live/mount/medium/live/Tails.iso"

    args = type("Args", (), {"source_command": "running", "json": True})()
    monkeypatch.setattr(cli, "RunningLiveSystemSource", FakeSource)

    result, payload = capture_json(cli.handle_source, args)

    assert result == 0
    assert payload["running_tails_available"] is True
    assert payload["version"] == "7.7.2"
    assert payload["device"] == "/dev/sdb1"
    assert payload["parent_device"] == "/dev/sdb"


def test_source_validate_attached_json_for_valid_tails_mount(tmp_path) -> None:
    live = tmp_path / "live"
    live.mkdir()
    (live / "Tails.version").write_text("7.7.2\n", encoding="utf-8")
    (live / "Tails.iso").write_text("fake iso placeholder", encoding="utf-8")

    args = type(
        "Args",
        (),
        {
            "source_command": "validate-attached",
            "device": "/dev/sdb1",
            "mount_point": str(tmp_path),
            "json": True,
        },
    )()

    result, payload = capture_json(cli.handle_source, args)

    assert result == 0
    assert payload["valid"] is True
    assert payload["error"] == ""
    assert payload["device"] == "/dev/sdb1"
    assert payload["parent_device"] == "/dev/sdb"
    assert payload["version"] == "7.7.2"
    assert payload["live_path"] == str(live)
    assert payload["iso_path"] == str(live / "Tails.iso")


def test_source_validate_attached_json_for_invalid_mount(tmp_path) -> None:
    args = type(
        "Args",
        (),
        {
            "source_command": "validate-attached",
            "device": "/dev/sdb1",
            "mount_point": str(tmp_path),
            "json": True,
        },
    )()

    result, payload = capture_json(cli.handle_source, args)

    assert result == 1
    assert payload["valid"] is False
    assert "missing" in payload["error"]
    assert payload["version"] == ""


def test_cli_parser_accepts_source_validate_attached_after_subcommand_flags(tmp_path) -> None:
    live = tmp_path / "live"
    live.mkdir()
    (live / "Tails.version").write_text("7.7.2\n", encoding="utf-8")

    result, payload = capture_json(
        cli.main,
        [
            "source",
            "validate-attached",
            "--device",
            "/dev/sdb1",
            "--mount-point",
            str(tmp_path),
            "--json",
        ],
    )

    assert result == 0
    assert payload["valid"] is True
    assert payload["version"] == "7.7.2"
