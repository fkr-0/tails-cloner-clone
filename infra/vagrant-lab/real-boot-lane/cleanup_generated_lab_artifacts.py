#!/usr/bin/env python3
from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TARGETS = [
    ROOT / "infra/vagrant-lab/real-boot-lane/out/debug-boot/tails-amd64-7.7.2-boot-8g-debug-extraarg.img",
    ROOT / "infra/vagrant-lab/real-boot-lane/out/debug-boot/tails-amd64-7.7.2-boot-8g-debug-root-login.img",
    ROOT / ".cache/appimage-e2e/v0.4.1/squashfs-root",
    ROOT / ".cache/appimage-e2e/local/tails-cloner-clone-dev-x86_64/squashfs-root",
    ROOT / "dist/squashfs-root",
]

for path in TARGETS:
    if path.is_dir():
        shutil.rmtree(path)
        print(f"removed dir {path}")
    elif path.exists():
        path.unlink()
        print(f"removed file {path}")
    else:
        print(f"absent {path}")
