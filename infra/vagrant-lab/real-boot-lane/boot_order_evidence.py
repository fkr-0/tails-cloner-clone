#!/usr/bin/env python3
"""Offline-only boot-order validation; does not establish boot compatibility."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))
from tails_cloner.boot_loader import apply_boot_loader_order_to_directory  # noqa: E402


def inventory(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def validate(source: Path, desired: list[str], dry_run: bool) -> dict:
    if not source.is_dir() or source.is_symlink() or any(p.is_symlink() for p in source.rglob("*")):
        raise ValueError("source must be a directory tree without symlinks")
    if not desired:
        raise ValueError("an entry is required")
    if len(set(desired)) != len(desired) or any(not entry or entry != entry.strip() for entry in desired):
        raise ValueError("requested entries must be distinct, nonempty, unpadded titles")
    before = inventory(source)
    configs = [n for n in before if Path(n).name in
               {"grub.cfg", "loopback.cfg", "syslinux.cfg", "isolinux.cfg", "live.cfg"}]
    if not configs:
        raise ValueError("no supported boot config files")
    evidence = {
        "schema": "tails-cloner-boot-order-evidence/v1",
        "desired_order": desired,
        "source_sha256": before,
        "layers": {"directory_copy": "dry_run" if dry_run else "executed",
                   "fat_image": "not_verified", "qemu_boot": "not_verified",
                   "visible_menu_order": "not_verified", "real_device_boot": "not_verified"},
    }
    if dry_run:
        return {**evidence, "success": True, "candidate_configs": configs}
    if os.getenv("TAILS_CLONER_LAB_ALLOW_DESTRUCTIVE") != "1":
        raise PermissionError("requires TAILS_CLONER_LAB_ALLOW_DESTRUCTIVE=1")
    with tempfile.TemporaryDirectory(prefix="tails-cloner-boot-order-") as td:
        copied = Path(td) / "boot"
        shutil.copytree(source, copied)
        results = apply_boot_loader_order_to_directory(copied, desired)
        after = inventory(copied)
        changed = {p.relative_to(copied).as_posix() for p in results.changed_paths}
        preserved = inventory(source) == before
        unrelated = all(after.get(p) == sha for p, sha in before.items() if p not in changed)
        unchanged_set = set(before) <= set(after)
        # A partial rewrite is not positive evidence: every discovered config
        # must be parseable and contain the requested entries in that order.
        ordered = all(
            not r.unsupported_reason
            and all(entry in r.entries_after for entry in desired)
            and [entry for entry in r.entries_after if entry in desired] == desired
            for r in results.files
        )
        evaluated = {r.path.relative_to(copied).as_posix() for r in results.files}
        coverage = set(configs) == evaluated
        valid = bool(changed) and preserved and unrelated and unchanged_set and ordered and coverage
        return {**evidence, "success": valid, "source_preserved": preserved,
                "unrelated_bytes_preserved": unrelated, "all_configs_match_order": ordered,
                "all_configs_evaluated": coverage, "skipped_configs": sorted(set(configs) - evaluated),
                "changed_files": sorted(changed),
                "resulting_sha256": after,
                "rewrites": [{"path": r.path.relative_to(copied).as_posix(),
                              "changed": r.changed, "before": r.entries_before,
                              "after": r.entries_after, "unsupported": r.unsupported_reason}
                             for r in results.files]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source-dir", type=Path, required=True)
    ap.add_argument("--entry", action="append", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--evidence", type=Path)
    a = ap.parse_args()
    try:
        result = validate(a.source_dir, a.entry, a.dry_run)
    except (OSError, ValueError) as e:
        ap.error(str(e))
    if a.evidence is not None and not a.dry_run:
        a.evidence.parent.mkdir(parents=True, exist_ok=True)
        a.evidence.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
