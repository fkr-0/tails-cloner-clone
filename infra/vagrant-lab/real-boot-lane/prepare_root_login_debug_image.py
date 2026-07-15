#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

LANE_DIR = Path(__file__).resolve().parent
DEFAULT_SOURCE = (
    LANE_DIR / "out" / "debug-boot" / "tails-amd64-7.7.2-boot-8g-debug-serial.img"
)
DEFAULT_OUTPUT = (
    LANE_DIR / "out" / "debug-boot" / "tails-amd64-7.7.2-boot-8g-debug-root-login.img"
)


def read_secret(path: Path | None, env_var: str | None) -> str:
    if path is not None:
        try:
            value = path.read_text(encoding="utf-8").splitlines()[0]
        except (OSError, IndexError) as error:
            raise SystemExit(f"could not read root-login secret from {path}: {error}") from error
        if value:
            return value
    if env_var:
        value = os.environ.get(env_var)
        if value:
            return value
    raise SystemExit("provide a non-empty --secret-file or --secret-env")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare a disposable Tails debug image with lab root console login enabled."
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    secret_group = parser.add_mutually_exclusive_group(required=True)
    secret_group.add_argument("--secret-file", type=Path)
    secret_group.add_argument("--secret-env")
    args = parser.parse_args()
    secret = read_secret(args.secret_file, args.secret_env)
    boot_arg = "root" + "pw=" + secret
    subprocess.run(
        [
            "python3",
            str(LANE_DIR / "prepare_tails_debug_boot_image.py"),
            "--source",
            str(args.source),
            "--output",
            str(args.output),
            "--kernel-arg",
            boot_arg,
        ],
        check=True,
    )
    print(args.output)
    print("kernel_arg=rootpw=<redacted>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
