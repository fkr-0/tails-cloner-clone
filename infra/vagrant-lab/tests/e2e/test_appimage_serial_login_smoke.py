from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "infra" / "vagrant-lab" / "real-boot-lane" / "run_appimage_smoke_via_serial_login.py"

spec = importlib.util.spec_from_file_location("run_appimage_smoke_via_serial_login", SCRIPT)
assert spec and spec.loader
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def test_guest_command_mounts_share_and_runs_smoke_script() -> None:
    command = runner.guest_command("tcapp", "/mnt/tcapp")

    assert "mount -t 9p" in command
    assert "tcapp" in command
    assert "/mnt/tcapp/run_appimage_guest_smoke.sh" in command


def test_build_command_uses_serial_socket_and_usb_boot(tmp_path) -> None:
    command, env = runner.build_command(
        image=tmp_path / "tails.img",
        qmp_socket=tmp_path / "qmp.sock",
        pidfile=tmp_path / "qemu.pid",
        serial_socket=tmp_path / "serial.sock",
        share_dir=tmp_path / "share",
        share_tag="tcapp",
        timeout=30,
        memory_mb=1024,
        cpus=1,
    )

    assert "--boot-usb" in command
    assert "--serial-socket" in command
    assert str(tmp_path / "serial.sock") in command
    assert env["TAILS_QEMU_MEMORY_MB"] == "1024"
    assert env["TAILS_QEMU_CPUS"] == "1"


def test_validate_transcript_rejects_missing_marker() -> None:
    result = runner.validate_transcript("no marker here")

    assert result["valid"] is False
    assert result["errors"]


def test_run_lock_refuses_existing_lock(tmp_path) -> None:
    lock = tmp_path / "lock"
    lock.write_text("pid=123\n", encoding="utf-8")

    try:
        with runner.RunLock(lock):
            raise AssertionError("lock should not be acquired")
    except RuntimeError as exc:
        assert "already running or stale lock" in str(exc)
        assert "pid=123" in str(exc)


def test_run_lock_removes_lock_on_exit(tmp_path) -> None:
    lock = tmp_path / "lock"

    with runner.RunLock(lock):
        assert lock.exists()
        assert "pid=" in lock.read_text(encoding="utf-8")

    assert not lock.exists()
