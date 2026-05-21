from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "infra" / "vagrant-lab" / "real-boot-lane" / "capture_appimage_guest_smoke_from_qemu.py"

spec = importlib.util.spec_from_file_location("capture_appimage_guest_smoke_from_qemu", SCRIPT)
assert spec and spec.loader
capture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture)


def test_dry_run_capture_uses_usb_boot_and_is_not_e2e_pass(tmp_path, monkeypatch) -> None:
    image = tmp_path / "tails.img"
    image.write_bytes(b"fake")
    share = tmp_path / "share"
    share.mkdir()
    serial_log = tmp_path / "serial.log"

    def fake_build_boot_command(**_kwargs):
        return (["bash", "boot_tails_qemu.sh", "--headless", "--timeout", "300", "image.img"], {})

    monkeypatch.setattr(capture, "build_boot_command", fake_build_boot_command)
    monkeypatch.setattr(capture, "image_version", lambda _image: "test")

    result = capture.capture_appimage_smoke(
        image=image,
        wait_timeout=1,
        memory_mb=1024,
        cpus=1,
        share_dir=share,
        share_tag="tcapp",
        mount_point="/mnt/tcapp",
        serial_log=serial_log,
        dry_run=True,
        headless=True,
        appimage=None,
        prepare=False,
        extra_drives=[],
    )

    assert result["success"] is True
    assert result["dry_run"] is True
    assert result["result"] == "command constructed but not executed"
    assert "--boot-usb" in result["command"]
    assert result["command"].index("--boot-usb") < result["command"].index("--timeout")
    assert "TAILS_CLONER_APPIMAGE_SMOKE" not in result


def test_capture_lock_refuses_parallel_or_stale_lock(tmp_path) -> None:
    lock = tmp_path / "capture.lock"
    lock.write_text("pid=123\n", encoding="utf-8")

    try:
        with capture.CaptureLock(lock):
            raise AssertionError("lock should not be acquired")
    except RuntimeError as exc:
        assert "already running or stale lock" in str(exc)
        assert "pid=123" in str(exc)


def test_capture_lock_removes_lock_on_exit(tmp_path) -> None:
    lock = tmp_path / "capture.lock"

    with capture.CaptureLock(lock):
        assert lock.exists()
        assert "pid=" in lock.read_text(encoding="utf-8")

    assert not lock.exists()


def test_no_marker_result_is_pending_not_passed(tmp_path, monkeypatch) -> None:
    image = tmp_path / "tails.img"
    image.write_bytes(b"fake")
    share = tmp_path / "share"
    share.mkdir()
    serial_log = tmp_path / "serial.log"
    lock = tmp_path / "capture.lock"

    def fake_build_boot_command(**_kwargs):
        return (["bash", "boot_tails_qemu.sh", "--headless", "--timeout", "1", "image.img"], {})

    class FakeClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def connect(self):
            return None

        def command(self, _command):
            return {"status": "running"}

        def close(self):
            return None

    class FakeProcess:
        returncode = 0

        def communicate(self, timeout=None):
            return "", ""

        def kill(self):
            return None

    monkeypatch.setattr(capture, "build_boot_command", fake_build_boot_command)
    monkeypatch.setattr(capture, "image_version", lambda _image: "test")
    monkeypatch.setattr(capture, "QmpClient", FakeClient)
    monkeypatch.setattr(capture, "quit_qemu", lambda _socket: {"return": {}})
    monkeypatch.setattr(capture.subprocess, "Popen", lambda *_args, **_kwargs: FakeProcess())
    monkeypatch.setattr(capture, "wait_for_marker", lambda _log, _timeout: None)

    result = capture.capture_appimage_smoke(
        image=image,
        wait_timeout=1,
        memory_mb=1024,
        cpus=1,
        share_dir=share,
        share_tag="tcapp",
        mount_point="/mnt/tcapp",
        serial_log=serial_log,
        dry_run=False,
        headless=True,
        appimage=None,
        prepare=False,
        extra_drives=[],
        lock_file=lock,
    )

    assert result["success"] is False
    assert result["marker_found"] is False
    assert result["result_status"] == "pending-no-marker"
    assert not lock.exists()
