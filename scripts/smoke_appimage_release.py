#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_REPO = "fkr-0/tails-cloner-clone"
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CACHE = ROOT / ".cache" / "appimage-e2e"
DEFAULT_ASSET = "tails-cloner-clone-{tag}-x86_64.AppImage"


def download(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.stat().st_size > 0:
        return
    with urllib.request.urlopen(url, timeout=60) as response:
        destination.write_bytes(response.read())


def latest_release(repo: str) -> dict[str, Any]:
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    with urllib.request.urlopen(url, timeout=60) as response:
        return json.load(response)


def release_by_tag(repo: str, tag: str) -> dict[str, Any]:
    url = f"https://api.github.com/repos/{repo}/releases/tags/{tag}"
    with urllib.request.urlopen(url, timeout=60) as response:
        return json.load(response)


def find_asset(release: dict[str, Any], name: str) -> dict[str, Any]:
    for asset in release.get("assets", []):
        if asset.get("name") == name:
            return asset
    raise SystemExit(f"release asset not found: {name}")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_sha256_file(path: Path) -> tuple[str, str]:
    first_line = path.read_text(encoding="utf-8").splitlines()[0]
    parts = first_line.split()
    if len(parts) < 2:
        raise SystemExit(f"invalid sha256 file: {path}")
    return parts[0], parts[1]


def run(command: list[str], *, timeout: int = 60, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def smoke_xvfb(appimage: Path, log_path: Path) -> dict[str, Any]:
    xvfb_run = shutil.which("xvfb-run")
    xdotool = shutil.which("xdotool")
    if not xvfb_run or not xdotool:
        return {
            "status": "skipped",
            "reason": "xvfb-run or xdotool is missing",
            "xvfb_run": xvfb_run or "",
            "xdotool": xdotool or "",
        }

    script = """#!/usr/bin/env bash
set -euo pipefail
APP="$1"
LOG="$2"
"$APP" --remote-index-url file:///nonexistent >"$LOG" 2>&1 &
pid=$!
trap 'kill "$pid" 2>/dev/null || true' EXIT
for _ in $(seq 1 30); do
  if xdotool search --name "Tails Cloner Clone" >/tmp/tails-cloner-wids 2>/dev/null; then
    echo window-detected
    kill "$pid" 2>/dev/null || true
    wait "$pid" || true
    exit 0
  fi
  if ! kill -0 "$pid" 2>/dev/null; then
    echo process-exited
    wait "$pid" || true
    exit 1
  fi
  sleep 1
done
echo window-not-detected
exit 1
"""
    with tempfile.TemporaryDirectory() as tmp:
        script_path = Path(tmp) / "smoke-appimage-xvfb.sh"
        script_path.write_text(script, encoding="utf-8")
        script_path.chmod(0o755)
        result = run([xvfb_run, "-a", str(script_path), str(appimage), str(log_path)], timeout=90)
    return {
        "status": "passed" if result.returncode == 0 and "window-detected" in result.stdout else "failed",
        "returncode": result.returncode,
        "stdout": result.stdout[-4000:],
        "stderr": result.stderr[-4000:],
        "log_path": str(log_path),
    }


def smoke_appimage(
    *,
    appimage: Path,
    sha_file: Path,
    work_dir: Path,
    no_gui: bool,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    work_dir.mkdir(parents=True, exist_ok=True)
    appimage.chmod(appimage.stat().st_mode | 0o111)

    expected_hash, checksum_name = parse_sha256_file(sha_file)
    actual_hash = sha256(appimage)
    relative_checksum_filename = checksum_name == appimage.name
    checksum_matches = expected_hash == actual_hash

    help_result = run([str(appimage), "--help"], timeout=30)
    extract_dir = work_dir / "squashfs-root"
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    extract_result = run([str(appimage), "--appimage-extract"], timeout=120, cwd=work_dir)
    gui = {"status": "skipped", "reason": "--no-gui"}
    if not no_gui:
        gui = smoke_xvfb(appimage, work_dir / "xvfb-smoke.log")

    passed = bool(
        checksum_matches
        and relative_checksum_filename
        and help_result.returncode == 0
        and extract_result.returncode == 0
        and gui["status"] in {"passed", "skipped"}
    )
    payload = {
        **metadata,
        "status": "passed" if passed else "failed",
        "appimage": str(appimage),
        "sha256_file": str(sha_file),
        "checksum": {
            "expected": expected_hash,
            "actual": actual_hash,
            "matches": checksum_matches,
            "filename_in_sha256": checksum_name,
            "uses_relative_asset_filename": relative_checksum_filename,
        },
        "help": {
            "returncode": help_result.returncode,
            "stdout": help_result.stdout[-4000:],
            "stderr": help_result.stderr[-4000:],
        },
        "extract": {
            "returncode": extract_result.returncode,
            "extracted": extract_dir.exists(),
            "stdout": extract_result.stdout[-4000:],
            "stderr": extract_result.stderr[-4000:],
        },
        "gui": gui,
    }
    evidence = work_dir / "appimage-smoke-evidence.json"
    evidence.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    payload["evidence"] = str(evidence)
    return payload


def prepare_release_inputs(repo: str, tag_arg: str, cache_dir: Path) -> tuple[Path, Path, Path, dict[str, Any]]:
    if tag_arg == "latest":
        release = latest_release(repo)
        tag = release["tag_name"]
    else:
        tag = tag_arg
        release = release_by_tag(repo, tag)

    asset_name = DEFAULT_ASSET.format(tag=tag)
    sha_name = f"{asset_name}.sha256"
    asset = find_asset(release, asset_name)
    sha_asset = find_asset(release, sha_name)

    cache = cache_dir / tag
    appimage = cache / asset_name
    sha_file = cache / sha_name
    download(asset["browser_download_url"], appimage)
    download(sha_asset["browser_download_url"], sha_file)
    metadata = {
        "mode": "release",
        "repo": repo,
        "tag": tag,
        "asset_url": asset["browser_download_url"],
        "sha256_url": sha_asset["browser_download_url"],
    }
    return appimage, sha_file, cache, metadata


def prepare_file_inputs(appimage_arg: str, sha_file_arg: str | None, work_dir_arg: str | None) -> tuple[Path, Path, Path, dict[str, Any]]:
    appimage = Path(appimage_arg).resolve()
    if not appimage.exists():
        raise SystemExit(f"AppImage does not exist: {appimage}")
    sha_file = Path(sha_file_arg).resolve() if sha_file_arg else appimage.with_name(f"{appimage.name}.sha256")
    if not sha_file.exists():
        raise SystemExit(f"sha256 file does not exist: {sha_file}")
    work_dir = Path(work_dir_arg).resolve() if work_dir_arg else DEFAULT_CACHE / "local" / appimage.stem
    metadata = {
        "mode": "file",
        "tag": "local",
        "repo": "",
    }
    return appimage, sha_file, work_dir, metadata


def print_summary(payload: dict[str, Any]) -> None:
    label = payload.get("tag") or payload.get("mode")
    print(f"AppImage smoke {payload['status']}: {label}")
    print(f"mode={payload.get('mode')}")
    print(f"checksum_matches={payload['checksum']['matches']}")
    print(f"checksum_relative_filename={payload['checksum']['uses_relative_asset_filename']}")
    print(f"help_returncode={payload['help']['returncode']}")
    print(f"extract_returncode={payload['extract']['returncode']}")
    print(f"gui_status={payload['gui']['status']}")
    print(f"evidence={payload['evidence']}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Smoke-test released or locally built AppImage artifacts.")
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--tag", default="latest", help="GitHub release tag, or latest. Used in release mode.")
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE))
    parser.add_argument("--appimage", help="Local AppImage path. Enables file mode.")
    parser.add_argument("--sha256-file", help="Local .sha256 path for --appimage. Defaults to <AppImage>.sha256.")
    parser.add_argument("--work-dir", help="Work/evidence directory for local file mode.")
    parser.add_argument("--no-gui", action="store_true", help="Skip Xvfb GUI smoke.")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.appimage:
        appimage, sha_file, work_dir, metadata = prepare_file_inputs(args.appimage, args.sha256_file, args.work_dir)
    else:
        appimage, sha_file, work_dir, metadata = prepare_release_inputs(
            repo=args.repo,
            tag_arg=args.tag,
            cache_dir=Path(args.cache_dir),
        )
    payload = smoke_appimage(
        appimage=appimage,
        sha_file=sha_file,
        work_dir=work_dir,
        no_gui=args.no_gui,
        metadata=metadata,
    )
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print_summary(payload)
    return 0 if payload["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
