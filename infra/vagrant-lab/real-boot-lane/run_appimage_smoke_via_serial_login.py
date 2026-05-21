#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import select
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

LANE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LANE_DIR))

from capture_guest_probe_from_qemu import QmpClient, quit_qemu  # noqa: E402
from validate_appimage_guest_smoke_output import extract_marker, validate_payload  # noqa: E402

BOOT_SCRIPT = LANE_DIR / "boot_tails_qemu.sh"
DEFAULT_IMAGE = LANE_DIR / "out" / "debug-boot" / "tails-amd64-7.7.2-boot-8g-debug-serial.img"
DEFAULT_SHARE_DIR = LANE_DIR / "out" / "appimage-guest-smoke-share"
DEFAULT_SHARE_TAG = "tailsclonerappimage"
DEFAULT_MOUNT_POINT = "/mnt/tailscloner-appimage"
DEFAULT_OUT_DIR = LANE_DIR / "out" / "serial-login-appimage-smoke"
DEFAULT_LOCK_FILE = LANE_DIR / "out" / "serial-login-appimage-smoke.lock"
MARKER = "TAILS_CLONER_APPIMAGE_SMOKE="


class RunLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.acquired = False

    def __enter__(self) -> "RunLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            existing = self.path.read_text(errors="replace") if self.path.exists() else ""
            raise RuntimeError(
                f"AppImage serial-login smoke already running or stale lock exists: {self.path} {existing!r}"
            ) from exc
        with os.fdopen(fd, "w") as handle:
            handle.write(f"pid={os.getpid()}\n")
        self.acquired = True
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        if self.acquired:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass


def wait_for_socket(path: Path, timeout: int) -> socket.socket:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(str(path))
            sock.setblocking(False)
            return sock
        except (FileNotFoundError, ConnectionRefusedError, OSError) as exc:
            last_error = exc
            sock.close()
            time.sleep(0.2)
    raise TimeoutError(f"timed out waiting for serial socket {path}: {last_error}")


def read_until(sock: socket.socket, transcript: list[str], pattern: str, timeout: int) -> bool:
    deadline = time.monotonic() + timeout
    buffer = "".join(transcript)
    while time.monotonic() < deadline:
        if pattern in buffer:
            return True
        ready, _, _ = select.select([sock], [], [], 0.5)
        if not ready:
            continue
        try:
            data = sock.recv(4096)
        except BlockingIOError:
            continue
        if not data:
            time.sleep(0.2)
            continue
        text = data.decode("utf-8", errors="replace")
        transcript.append(text)
        buffer += text
        if pattern in buffer:
            return True
    return pattern in buffer


def read_until_any(sock: socket.socket, transcript: list[str], patterns: list[str], timeout: int) -> str | None:
    deadline = time.monotonic() + timeout
    buffer = "".join(transcript)
    while time.monotonic() < deadline:
        for pattern in patterns:
            if pattern in buffer:
                return pattern
        ready, _, _ = select.select([sock], [], [], 0.5)
        if not ready:
            continue
        try:
            data = sock.recv(4096)
        except BlockingIOError:
            continue
        if not data:
            time.sleep(0.2)
            continue
        text = data.decode("utf-8", errors="replace")
        transcript.append(text)
        buffer += text
        for pattern in patterns:
            if pattern in buffer:
                return pattern
    return None


def send_line(sock: socket.socket, line: str) -> None:
    sock.sendall(line.encode("utf-8") + b"\n")


def guest_command(share_tag: str, mount_point: str) -> str:
    return (
        f"sudo mkdir -p {mount_point} && "
        f"sudo mount -t 9p -o trans=virtio,version=9p2000.L,ro {share_tag} {mount_point} && "
        f"{mount_point}/run_appimage_guest_smoke.sh"
    )


def build_command(
    *,
    image: Path,
    qmp_socket: Path,
    pidfile: Path,
    serial_socket: Path,
    share_dir: Path,
    share_tag: str,
    timeout: int,
    memory_mb: int,
    cpus: int,
) -> tuple[list[str], dict[str, str]]:
    env = os.environ.copy()
    env.setdefault("TAILS_QEMU_MEMORY_MB", str(memory_mb))
    env.setdefault("TAILS_QEMU_CPUS", str(cpus))
    command = [
        "bash",
        str(BOOT_SCRIPT),
        "--headless",
        "--boot-usb",
        "--timeout",
        str(timeout + 30),
        "--no-network",
        "--qmp",
        str(qmp_socket),
        "--pidfile",
        str(pidfile),
        "--serial-socket",
        str(serial_socket),
        "--share-dir",
        f"{share_dir},{share_tag}",
        str(image),
    ]
    return command, env


def validate_transcript(text: str) -> dict[str, Any]:
    marker_payload: dict[str, Any] | None = None
    errors: list[str] = []
    try:
        marker_payload = extract_marker(text)
        errors = validate_payload(marker_payload)
    except Exception as exc:  # noqa: BLE001 - surfaced in evidence
        errors = [str(exc)]
    return {"valid": not errors, "errors": errors, "payload": marker_payload}


def run_smoke(
    *,
    image: Path,
    out_dir: Path,
    share_dir: Path,
    share_tag: str,
    mount_point: str,
    timeout: int,
    login_timeout: int,
    marker_timeout: int,
    memory_mb: int,
    cpus: int,
    dry_run: bool,
    lock_file: Path,
    login_user: str,
    login_password: str | None,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    transcript_path = out_dir / "serial-transcript.log"
    with tempfile.TemporaryDirectory(prefix="tails-appimage-serial-login-") as tmpdir:
        tmp = Path(tmpdir)
        qmp_socket = tmp / "qmp.sock"
        pidfile = tmp / "qemu.pid"
        serial_socket = tmp / "serial.sock"
        command, env = build_command(
            image=image,
            qmp_socket=qmp_socket,
            pidfile=pidfile,
            serial_socket=serial_socket,
            share_dir=share_dir,
            share_tag=share_tag,
            timeout=timeout,
            memory_mb=memory_mb,
            cpus=cpus,
        )
        base: dict[str, Any] = {
            "image": str(image),
            "out_dir": str(out_dir),
            "transcript": str(transcript_path),
            "share_dir": str(share_dir),
            "share_tag": share_tag,
            "mount_point": mount_point,
            "guest_command": guest_command(share_tag, mount_point),
            "command": command,
            "login_timeout_seconds": login_timeout,
            "marker_timeout_seconds": marker_timeout,
            "login_user": login_user,
            "login_password_configured": login_password is not None,
        }
        if dry_run:
            return {**base, "success": True, "dry_run": True, "result_status": "dry-run"}
        with RunLock(lock_file):
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
            transcript: list[str] = []
            qmp_status: dict[str, Any] | None = None
            qmp_quit: dict[str, Any] | None = None
            marker_seen = False
            login_seen = False
            shell_prompt_seen = False
            password_prompt_seen = False
            login_failed = False
            command_sent = False
            validation: dict[str, Any] | None = None
            try:
                client = QmpClient(qmp_socket, timeout=min(login_timeout, 30))
                try:
                    client.connect()
                    qmp_status = client.command("query-status")
                finally:
                    client.close()
                sock = wait_for_socket(serial_socket, timeout=30)
                try:
                    login_seen = read_until(sock, transcript, "amnesia login:", login_timeout)
                    shell_prompt_seen = False
                    password_prompt_seen = False
                    login_failed = False
                    if login_seen:
                        send_line(sock, login_user)
                        prompt = read_until_any(sock, transcript, ["Password:", "$", "#", "Login incorrect", "amnesia login:"], 30)
                        if prompt == "Password:":
                            password_prompt_seen = True
                            send_line(sock, login_password or "")
                            prompt = read_until_any(sock, transcript, ["$", "#", "Login incorrect", "amnesia login:"], 30)
                        if prompt in {"$", "#"}:
                            shell_prompt_seen = True
                            send_line(sock, guest_command(share_tag, mount_point))
                            command_sent = True
                            marker_seen = read_until(sock, transcript, MARKER, marker_timeout)
                        elif prompt in {"Login incorrect", "amnesia login:"}:
                            login_failed = True
                finally:
                    sock.close()
                transcript_text = "".join(transcript)
                transcript_path.write_text(transcript_text, encoding="utf-8", errors="replace")
                if marker_seen:
                    validation = validate_transcript(transcript_text)
                qmp_quit = quit_qemu(qmp_socket)
            finally:
                try:
                    stdout, stderr = process.communicate(timeout=20)
                except subprocess.TimeoutExpired:
                    process.kill()
                    stdout, stderr = process.communicate(timeout=20)
            if validation and validation.get("valid"):
                result_status = "passed"
            elif marker_seen:
                result_status = "marker-invalid"
            elif command_sent:
                result_status = "command-sent-no-marker"
            elif login_failed:
                result_status = "login-failed"
            elif password_prompt_seen and not shell_prompt_seen:
                result_status = "password-required-no-shell"
            elif login_seen:
                result_status = "login-seen-command-not-sent"
            else:
                result_status = "login-not-seen"
            evidence = {
                **base,
                "success": result_status == "passed",
                "dry_run": False,
                "result_status": result_status,
                "login_seen": login_seen,
                "password_prompt_seen": password_prompt_seen,
                "shell_prompt_seen": shell_prompt_seen,
                "login_failed": login_failed,
                "command_sent": command_sent,
                "marker_seen": marker_seen,
                "validation": validation,
                "qmp_status": qmp_status,
                "qmp_quit": qmp_quit,
                "returncode": process.returncode,
                "stdout_tail": stdout[-4000:],
                "stderr_tail": stderr[-4000:],
                "transcript_tail": transcript_path.read_text(errors="replace")[-12000:] if transcript_path.exists() else "",
            }
            (out_dir / "serial-login-appimage-smoke-evidence.json").write_text(
                json.dumps(evidence, indent=2, sort_keys=True), encoding="utf-8"
            )
            return evidence


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run Tails AppImage guest smoke by logging in over serial and executing the 9p share script."
    )
    parser.add_argument("--image", type=Path, default=DEFAULT_IMAGE)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--share-dir", type=Path, default=DEFAULT_SHARE_DIR)
    parser.add_argument("--share-tag", default=DEFAULT_SHARE_TAG)
    parser.add_argument("--mount-point", default=DEFAULT_MOUNT_POINT)
    parser.add_argument("--timeout", type=int, default=520)
    parser.add_argument("--login-timeout", type=int, default=420)
    parser.add_argument("--marker-timeout", type=int, default=180)
    parser.add_argument("--memory-mb", type=int, default=4096)
    parser.add_argument("--cpus", type=int, default=2)
    parser.add_argument("--lock-file", type=Path, default=DEFAULT_LOCK_FILE)
    parser.add_argument("--login-user", default="amnesia")
    parser.add_argument("--login-password")
    parser.add_argument("--login-password-file", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not args.image.exists():
        raise SystemExit(f"image not found: {args.image}")
    if not args.share_dir.exists():
        raise SystemExit(f"share dir not found: {args.share_dir}")
    result = run_smoke(
        image=args.image,
        out_dir=args.out_dir,
        share_dir=args.share_dir,
        share_tag=args.share_tag,
        mount_point=args.mount_point,
        timeout=args.timeout,
        login_timeout=args.login_timeout,
        marker_timeout=args.marker_timeout,
        memory_mb=args.memory_mb,
        cpus=args.cpus,
        dry_run=args.dry_run,
        lock_file=args.lock_file,
        login_user=args.login_user,
        login_password=args.login_password,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
