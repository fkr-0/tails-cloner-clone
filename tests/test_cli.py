from __future__ import annotations

import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Any
from unittest import mock

import tails_cloner.__main__ as package_entrypoint
from tails_cloner import __version__, cli
from tails_cloner.controller import ApplicationController
from tails_cloner.download import VerifiedImageResult
from tails_cloner.drive_inspector import DriveTailsFacts
from tails_cloner.models import AppState, BlockDevice, PostWriteOptions, VersionAssets


class FakeVersionService:
    def __init__(self, versions: list[VersionAssets] | None = None) -> None:
        self.versions = versions or []

    def fetch_versions(self) -> list[VersionAssets]:
        return list(self.versions)


class FakeDeviceService:
    def __init__(self, devices: list[BlockDevice]) -> None:
        self.devices = devices
        self.calls = 0

    def list_devices(self) -> list[BlockDevice]:
        self.calls += 1
        return list(self.devices)


class FakeCloneService:
    def __init__(self) -> None:
        self.clone_calls: list[tuple[str, str, PostWriteOptions | None]] = []
        self.upgrade_calls: list[tuple[str, str]] = []
        self.device_upgrade_calls: list[tuple[str, str]] = []

    def clone_image(
        self,
        image_path: str,
        device_path: str,
        progress_callback: Callable[[str], None] | None = None,
        post_write_options: PostWriteOptions | None = None,
    ) -> None:
        self.clone_calls.append((image_path, device_path, post_write_options))
        if progress_callback is not None:
            progress_callback("1048576 bytes copied")

    def upgrade_image(
        self,
        image_path: str,
        device_path: str,
        progress_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.upgrade_calls.append((image_path, device_path))
        if progress_callback is not None:
            progress_callback("system partition verified")

    def upgrade_from_device(
        self,
        source_device: str,
        device_path: str,
        progress_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.device_upgrade_calls.append((source_device, device_path))
        if progress_callback is not None:
            progress_callback("persistence partition preserved")


def version(version: str = "7.7.2") -> VersionAssets:
    base = f"https://example.invalid/tails/stable/tails-amd64-{version}/"
    image = f"tails-amd64-{version}.img"
    return VersionAssets(
        version=version,
        directory_url=base,
        iso_url=f"{base}tails-amd64-{version}.iso",
        img_url=f"{base}{image}",
        sig_url=f"{base}{image}.sig",
        sha256_url=f"{base}{image}.sha256",
    )


def source_device() -> BlockDevice:
    return BlockDevice(
        path="/dev/sdy",
        stable_path="/dev/disk/by-id/usb-Synthetic_Source",
        size_bytes=16_000_000_000,
        size_label="14.9 GiB",
        model="Synthetic Tails Source",
        vendor="Test",
        transport="usb",
        removable=True,
        serial="SOURCE123",
        has_tails=True,
        is_gpt=True,
        is_big_enough_for_installation=True,
        is_big_enough_for_upgrade=True,
    )


def target_device(*, current: bool = False, tails: bool = False) -> BlockDevice:
    reason = "This is the device backing the currently running operating system." if current else ""
    return BlockDevice(
        path="/dev/sdz",
        stable_path="/dev/disk/by-id/usb-Synthetic_Target",
        size_bytes=64_000_000_000,
        size_label="59.6 GiB",
        model="Synthetic USB",
        vendor="Test",
        transport="usb",
        removable=True,
        serial="TARGET123",
        has_tails=tails,
        is_gpt=tails,
        is_big_enough_for_installation=True,
        is_big_enough_for_upgrade=True,
        is_current_system_device=current,
        is_host_system_device=current,
        disabled_reason=reason,
    )


def make_controller(
    devices: list[BlockDevice],
    *,
    versions: list[VersionAssets] | None = None,
    clone_service: FakeCloneService | None = None,
    post_write_options: PostWriteOptions | None = None,
) -> tuple[ApplicationController, FakeCloneService]:
    clone = clone_service or FakeCloneService()
    controller = ApplicationController(
        state=AppState(post_write_options=post_write_options or PostWriteOptions()),
        version_service=FakeVersionService(versions),
        device_service=FakeDeviceService(devices),
        clone_service=clone,
        executor=ThreadPoolExecutor(max_workers=1),
    )
    return controller, clone


def run_json(argv: list[str]) -> tuple[int, dict[str, Any], str]:
    stdout = StringIO()
    stderr = StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = cli.main([*argv, "--output", "json"])
    return code, json.loads(stdout.getvalue()), stderr.getvalue()


def test_cli_help_exposes_complete_surface() -> None:
    help_text = cli.build_cli_parser().format_help()
    for command in ("status", "versions", "devices", "download", "plan", "install", "upgrade"):
        assert command in help_text
    assert "jsonl" in help_text
    assert "tails-cloner-cli/v1" in help_text


def test_package_entrypoint_no_argv_launches_gui_and_emits_stable_stdout() -> None:
    controller = mock.Mock()
    app = mock.Mock()
    stdout = StringIO()
    with (
        mock.patch("tails_cloner.__main__.build_application_controller", return_value=controller),
        mock.patch("tails_cloner.app.TailsClonerApp", return_value=app) as app_class,
        mock.patch("tails_cloner.__main__.cli_main", side_effect=AssertionError("CLI used for empty argv")),
        redirect_stdout(stdout),
    ):
        assert package_entrypoint.main([]) == 0

    app.mainloop.assert_called_once_with()
    controller.shutdown.assert_called_once_with()
    passed_sink = app_class.call_args.kwargs["event_sink"]
    assert passed_sink.command == "gui"
    events = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert [event["kind"] for event in events] == ["start", "completion"]
    assert events[0]["schema"] == "tails-cloner-cli/v1"
    assert events[0]["command"] == "gui"
    assert events[-1]["result"]["activity"] == "gui_session"


def test_package_entrypoint_every_nonempty_argv_routes_to_cli_unchanged() -> None:
    cases = [
        ["devices", "list", "--output", "json"],
        ["--help"],
        ["--version"],
        ["--output", "json", "status"],
        ["--remote-index-url", "https://example.invalid/"],
        ["--enable-post-write-customizations"],
        ["--gui"],
        ["--legacy-gui-looking-option"],
    ]
    for argv in cases:
        with (
            mock.patch("tails_cloner.__main__.cli_main", return_value=17) as forwarded,
            mock.patch("tails_cloner.__main__._run_gui", side_effect=AssertionError("GUI used for nonempty argv")),
        ):
            assert package_entrypoint.main(argv) == 17
        forwarded.assert_called_once_with(argv)


def test_source_running_json_uses_schema_v1(monkeypatch: mock.Mock) -> None:
    class FakeSource:
        exists = True
        version = "7.7.2"
        device = "/dev/sdb1"
        mount_point = Path("/lib/live/mount/medium")

        @staticmethod
        def get_iso_path() -> Path:
            return Path("/lib/live/mount/medium/live/Tails.iso")

    monkeypatch.setattr(cli, "RunningLiveSystemSource", FakeSource)
    monkeypatch.setattr(cli, "get_parent_disk_path", lambda _path: "/dev/sdb")
    code, envelope, stderr = run_json(["source", "running"])
    assert code == 0
    assert stderr == ""
    assert envelope["schema"] == "tails-cloner-cli/v1"
    assert envelope["schema_version"] == 1
    assert envelope["kind"] == "completion"
    result = envelope["result"]
    assert isinstance(result, dict)
    running = result["running_tails"]
    assert isinstance(running, dict)
    assert running["version"] == "7.7.2"
    assert running["parent_device"] == "/dev/sdb"


def test_json_alias_and_remote_index_work_after_nested_subcommand() -> None:
    controller, _ = make_controller([], versions=[version()])
    stdout = StringIO()
    with mock.patch("tails_cloner.cli._build_controller", return_value=controller), redirect_stdout(stdout):
        assert cli.main(["versions", "latest", "--json", "--remote-index-url", "https://example.invalid/"]) == 0
    envelope = json.loads(stdout.getvalue())
    assert envelope["result"]["version"]["version"] == "7.7.2"
    assert envelope["result"]["version"]["latest"] is True


def test_catalog_download_reports_verification_provenance_and_reuse(tmp_path: Path) -> None:
    selected = version()
    controller, _ = make_controller([], versions=[selected])
    destination = tmp_path / "tails.img"
    verified = VerifiedImageResult(
        path=destination,
        version=selected.version,
        source_url=selected.img_url,
        checksum_url=selected.sha256_url,
        signature_url=selected.sig_url,
        sha256="a" * 64,
        signing_fingerprint="0123456789ABCDEF0123456789ABCDEF01234567",
        pinned_primary_fingerprint="A490D0F4D311A4153E2BB7CADBB802B258ACD84F",
        reused_existing=True,
    )
    with (
        mock.patch("tails_cloner.cli._build_controller", return_value=controller),
        mock.patch("tails_cloner.cli.VerifiedImageDownloader") as downloader_class,
    ):
        downloader_class.return_value.download.return_value = verified
        code, envelope, _ = run_json(
            ["download", "latest", "--destination", str(destination)]
        )
    assert code == 0
    payload = envelope["result"]["download"]
    assert payload["reused_existing"] is True
    assert payload["verification"] == ["sha256", "openpgp"]
    assert payload["sha256"] == "a" * 64
    assert payload["signing_fingerprint"] == verified.signing_fingerprint
    assert payload["pinned_primary_fingerprint"] == verified.pinned_primary_fingerprint


def test_devices_expose_stable_identity_persistence_and_refusal_reason() -> None:
    device = target_device(current=True, tails=True)
    controller, _ = make_controller([device])
    facts = DriveTailsFacts(
        drive_path=device.path,
        tails_installed=True,
        tails_version="7.7.2",
        running_tails_on_this_drive=False,
        persistence_configured=True,
        persistence_partition_size_bytes=8_000_000_000,
    )
    with (
        mock.patch("tails_cloner.cli._build_controller", return_value=controller),
        mock.patch("tails_cloner.controller.is_running_tails", return_value=False),
        mock.patch("tails_cloner.cli.inspect_drive_tails_facts", return_value=facts),
    ):
        code, envelope, _ = run_json(["devices", "list"])
    assert code == 0
    payload = envelope["result"]["devices"][0]
    assert payload["stable_path"] == "/dev/disk/by-id/usb-Synthetic_Target"
    assert payload["confirmation_token"] == "serial:target123"
    assert payload["installed_tails_version"] == "7.7.2"
    assert payload["persistence_present"] is True
    assert payload["selectable"] is False
    assert "currently running system" in payload["disabled_reason"]


def test_plan_install_reports_whole_device_scope_and_confirmation_token(tmp_path: Path) -> None:
    image = tmp_path / "tails.img"
    image.write_bytes(b"synthetic image")
    controller, _ = make_controller([target_device()])
    with (
        mock.patch("tails_cloner.cli._build_controller", return_value=controller),
        mock.patch("tails_cloner.controller.is_running_tails", return_value=False),
    ):
        code, envelope, _ = run_json(
            ["plan", "install", "--image", str(image), "--target", "/dev/disk/by-id/usb-Synthetic_Target"]
        )
    assert code == 0
    plan = envelope["result"]["plan"]
    assert plan["would_write"] is True
    assert plan["destructive_scope"] == "whole_device"
    assert plan["persistence_behavior"] == "destroyed_if_present"
    assert plan["confirmation_token"] == "serial:target123"
    assert any("not been cryptographically verified" in warning for warning in plan["warnings"])


def test_plan_upgrade_reports_partition_only_persistence_preservation(tmp_path: Path) -> None:
    image = tmp_path / "tails.img"
    image.write_bytes(b"synthetic image")
    controller, _ = make_controller([target_device(tails=True)])
    with (
        mock.patch("tails_cloner.cli._build_controller", return_value=controller),
        mock.patch("tails_cloner.controller.is_running_tails", return_value=False),
    ):
        code, envelope, _ = run_json(
            ["plan", "upgrade", "--image", str(image), "--target", "/dev/sdz"]
        )
    assert code == 0
    plan = envelope["result"]["plan"]
    assert plan["destructive_scope"] == "tails_system_partition_only"
    assert plan["persistence_behavior"] == "preserved_if_present"


def test_plan_current_system_target_returns_safety_exit(tmp_path: Path) -> None:
    image = tmp_path / "tails.img"
    image.write_bytes(b"synthetic image")
    controller, _ = make_controller([target_device(current=True)])
    with (
        mock.patch("tails_cloner.cli._build_controller", return_value=controller),
        mock.patch("tails_cloner.controller.is_running_tails", return_value=False),
    ):
        code, envelope, _ = run_json(["plan", "install", "--image", str(image), "--target", "/dev/sdz"])
    assert code == 4
    assert envelope["exit_code"] == 4
    assert envelope["result"]["plan"]["would_write"] is False


def test_install_refuses_unverified_local_image_without_explicit_override(tmp_path: Path) -> None:
    image = tmp_path / "tails.img"
    image.write_bytes(b"synthetic image")
    controller, clone = make_controller([target_device()])
    with (
        mock.patch("tails_cloner.cli._build_controller", return_value=controller),
        mock.patch("tails_cloner.controller.is_running_tails", return_value=False),
    ):
        code, envelope, _ = run_json(
            [
                "install",
                "--image",
                str(image),
                "--target",
                "/dev/sdz",
                "--yes",
                "--target-id",
                "serial:target123",
            ]
        )
    assert code == 5
    assert envelope["error"]["category"] == "unverified_local_image"
    assert clone.clone_calls == []


def test_install_requires_yes_and_exact_stable_target_token(tmp_path: Path) -> None:
    image = tmp_path / "tails.img"
    image.write_bytes(b"synthetic image")

    controller, clone = make_controller([target_device()])
    with (
        mock.patch("tails_cloner.cli._build_controller", return_value=controller),
        mock.patch("tails_cloner.controller.is_running_tails", return_value=False),
    ):
        code, envelope, _ = run_json(
            [
                "install",
                "--image",
                str(image),
                "--allow-unverified-local-image",
                "--target",
                "/dev/sdz",
                "--target-id",
                "serial:target123",
            ]
        )
    assert code == 4
    assert envelope["error"]["category"] == "confirmation_required"
    assert clone.clone_calls == []

    controller, clone = make_controller([target_device()])
    with (
        mock.patch("tails_cloner.cli._build_controller", return_value=controller),
        mock.patch("tails_cloner.controller.is_running_tails", return_value=False),
    ):
        code, envelope, _ = run_json(
            [
                "install",
                "--image",
                str(image),
                "--allow-unverified-local-image",
                "--target",
                "/dev/sdz",
                "--yes",
                "--target-id",
                "serial:some-other-device",
            ]
        )
    assert code == 4
    assert envelope["error"]["category"] == "target_identity_mismatch"
    assert clone.clone_calls == []


def test_synthetic_install_executes_only_through_controller_and_stable_path(tmp_path: Path) -> None:
    image = tmp_path / "tails.img"
    image.write_bytes(b"synthetic image")
    controller, clone = make_controller(
        [target_device()],
        post_write_options=PostWriteOptions(enabled=True, settle_seconds=0),
    )
    with (
        mock.patch("tails_cloner.cli._build_controller", return_value=controller),
        mock.patch("tails_cloner.controller.is_running_tails", return_value=False),
    ):
        code, envelope, _ = run_json(
            [
                "install",
                "--image",
                str(image),
                "--allow-unverified-local-image",
                "--target",
                "/dev/sdz",
                "--yes",
                "--target-id",
                "serial:target123",
                "--enable-post-write-customizations",
                "--post-write-settle-seconds",
                "0",
            ]
        )
    assert code == 0
    assert len(clone.clone_calls) == 1
    clone_image, clone_target, options = clone.clone_calls[0]
    assert clone_image == str(image.resolve())
    assert clone_target == "/dev/disk/by-id/usb-Synthetic_Target"
    assert options is not None and options.enabled is True
    result = envelope["result"]["operation_result"]
    assert result["destructive_scope"] == "whole_device"
    assert result["confirmation_token"] == "serial:target123"


def test_synthetic_upgrade_calls_partition_scoped_upgrade_service(tmp_path: Path) -> None:
    image = tmp_path / "tails.img"
    image.write_bytes(b"synthetic image")
    controller, clone = make_controller([target_device(tails=True)])
    with (
        mock.patch("tails_cloner.cli._build_controller", return_value=controller),
        mock.patch("tails_cloner.controller.is_running_tails", return_value=False),
    ):
        code, envelope, _ = run_json(
            [
                "upgrade",
                "--image",
                str(image),
                "--allow-unverified-local-image",
                "--target",
                "/dev/sdz",
                "--yes",
                "--target-id",
                "serial:target123",
            ]
        )
    assert code == 0
    assert clone.clone_calls == []
    assert clone.upgrade_calls == [(str(image.resolve()), "/dev/disk/by-id/usb-Synthetic_Target")]
    result = envelope["result"]["operation_result"]
    assert result["destructive_scope"] == "tails_system_partition_only"
    assert result["persistence_behavior"] == "preserved_if_present"


def test_running_tails_upgrade_resolves_stable_source_and_target_aliases() -> None:
    source = source_device()
    target = target_device(tails=True)
    controller, clone = make_controller([source, target])
    with (
        mock.patch("tails_cloner.cli._build_controller", return_value=controller),
        mock.patch("tails_cloner.controller.is_running_tails", return_value=True),
        mock.patch("tails_cloner.controller.get_running_tails_version", return_value="7.7.2"),
        mock.patch("tails_cloner.controller.get_running_tails_device", return_value="/dev/sdy1"),
    ):
        code, envelope, _ = run_json(
            [
                "upgrade",
                "--running-source",
                "--target",
                "/dev/sdz",
                "--yes",
                "--target-id",
                "serial:target123",
            ]
        )
    assert code == 0
    assert clone.upgrade_calls == []
    assert clone.device_upgrade_calls == [
        ("/dev/disk/by-id/usb-Synthetic_Source", "/dev/disk/by-id/usb-Synthetic_Target")
    ]
    result = envelope["result"]["operation_result"]
    assert result["source"]["type"] == "running_source"
    assert result["source"]["version"] == "7.7.2"
    assert result["persistence_behavior"] == "preserved_if_present"


def test_attached_tails_upgrade_uses_validated_source_and_partition_scope(tmp_path: Path) -> None:
    live = tmp_path / "live"
    live.mkdir()
    (live / "Tails.version").write_text("7.7.1\n", encoding="utf-8")
    source = source_device()
    target = target_device(tails=True)
    controller, clone = make_controller([source, target])
    with (
        mock.patch("tails_cloner.cli._build_controller", return_value=controller),
        mock.patch("tails_cloner.controller.is_running_tails", return_value=False),
    ):
        code, envelope, _ = run_json(
            [
                "upgrade",
                "--attached-source",
                "/dev/sdy1",
                "--attached-mount",
                str(tmp_path),
                "--target",
                "/dev/sdz",
                "--yes",
                "--target-id",
                "serial:target123",
            ]
        )
    assert code == 0
    assert clone.upgrade_calls == []
    assert clone.device_upgrade_calls == [
        ("/dev/disk/by-id/usb-Synthetic_Source", "/dev/disk/by-id/usb-Synthetic_Target")
    ]
    result = envelope["result"]["operation_result"]
    assert result["source"]["type"] == "attached_source"
    assert result["source"]["version"] == "7.7.1"
    assert result["destructive_scope"] == "tails_system_partition_only"


def test_attached_source_validation_is_scriptable(tmp_path: Path) -> None:
    live = tmp_path / "live"
    live.mkdir()
    (live / "Tails.version").write_text("7.7.2\n", encoding="utf-8")
    code, envelope, _ = run_json(
        ["source", "validate-attached", "--device", "/dev/sdb1", "--mount-point", str(tmp_path)]
    )
    assert code == 0
    assert envelope["result"]["valid"] is True
    assert envelope["result"]["version"] == "7.7.2"


def test_cli_version_is_current_release() -> None:
    parser = cli.build_cli_parser()
    with mock.patch("sys.stdout", new=StringIO()) as output:
        try:
            parser.parse_args(["--version"])
        except SystemExit as error:
            assert error.code == 0
    assert f"tails-cloner-clone {__version__}" in output.getvalue()
