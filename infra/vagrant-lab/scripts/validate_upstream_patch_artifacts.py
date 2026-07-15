#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

HOST_ROOT = Path(__file__).resolve().parents[3]
VM_ROOT = Path('/workspace/tails-cloner')
REPO_ROOT = VM_ROOT if VM_ROOT.exists() else HOST_ROOT
ISSUE_FIX = REPO_ROOT / 'tails_issue_fix'
TAILS_TREE = ISSUE_FIX / 'tails'
PATCH_DIR = ISSUE_FIX / 'patches'
SERIES_DIR = PATCH_DIR / 'cloner-internal-targets-series'
COMBINED_SERIES = PATCH_DIR / 'tails-cloner-guarded-internal-targets.series.patch'
PLAIN_PATCH = PATCH_DIR / '0001-tails-cloner-guarded-internal-targets.patch'
EXPECTED_SERIES_FILES = [
    '0001-tails-installer-expose-admin-password-state.patch',
    '0002-tails-installer-gate-internal-target-enumeration.patch',
    '0003-tails-installer-confirm-internal-storage-targets.patch',
    '0004-tails-installer-refuse-source-and-running-devices-as.patch',
]
EXPECTED_SUBJECTS = [
    'tails-installer: expose admin password state',
    'tails-installer: gate internal target enumeration',
    'tails-installer: confirm internal storage targets',
    'tails-installer: refuse source and running devices as targets',
]
TOUCHED_FILES = [
    'config/chroot_local-includes/usr/lib/python3/dist-packages/tails_installer/creator.py',
    'config/chroot_local-includes/usr/lib/python3/dist-packages/tails_installer/gui.py',
    'config/chroot_local-includes/usr/lib/python3/dist-packages/tails_installer/utils.py',
]


def run(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(args, cwd=cwd, check=True, text=True, capture_output=True)
    except subprocess.CalledProcessError as exc:
        sys.stderr.write(f'command failed in {cwd}: {args}\n')
        sys.stderr.write(exc.stdout)
        sys.stderr.write(exc.stderr)
        raise


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def check_artifacts_exist() -> None:
    require(TAILS_TREE.exists(), f'missing nested Tails tree: {TAILS_TREE}')
    require((TAILS_TREE / '.git').exists(), f'missing nested Tails git metadata: {TAILS_TREE / ".git"}')
    patches = sorted(path.name for path in SERIES_DIR.glob('*.patch'))
    require(patches == EXPECTED_SERIES_FILES, f'unexpected patch series: {patches}')
    require(COMBINED_SERIES.exists(), f'missing combined series patch: {COMBINED_SERIES}')
    require(PLAIN_PATCH.exists(), f'missing plain patch: {PLAIN_PATCH}')
    print('artifact-existence: OK')


def check_plain_patch_applies() -> None:
    with TemporaryDirectory(prefix='tails-plain-patch-') as tmp:
        worktree = Path(tmp) / 'wt'
        run(['git', 'worktree', 'add', '--detach', str(worktree), 'origin/devel'], TAILS_TREE)
        try:
            run(['git', 'apply', '--check', str(PLAIN_PATCH)], worktree)
            run(['git', 'apply', str(PLAIN_PATCH)], worktree)
            run(['python3', '-m', 'py_compile', *TOUCHED_FILES], worktree)
        finally:
            run(['git', 'worktree', 'remove', '--force', str(worktree)], TAILS_TREE)
    print('plain-patch-apply-compile: OK')


def check_series_applies() -> None:
    patches = [str(SERIES_DIR / name) for name in EXPECTED_SERIES_FILES]
    with TemporaryDirectory(prefix='tails-series-patch-') as tmp:
        worktree = Path(tmp) / 'wt'
        run(['git', 'worktree', 'add', '--detach', str(worktree), 'origin/devel'], TAILS_TREE)
        try:
            run(['git', 'am', '--3way', *patches], worktree)
            run(['python3', '-m', 'py_compile', *TOUCHED_FILES], worktree)
            log = run(['git', 'log', '--format=%s', '--reverse', 'origin/devel..HEAD'], worktree)
            subjects = log.stdout.splitlines()
            require(subjects == EXPECTED_SUBJECTS, f'unexpected patch subjects: {subjects}')
        finally:
            run(['git', 'worktree', 'remove', '--force', str(worktree)], TAILS_TREE)
    print('series-apply-compile: OK')


def main() -> int:
    parser = argparse.ArgumentParser(description='Validate upstream Tails patch artifacts for the internal-target Cloner MR.')
    parser.add_argument('--existence-only', action='store_true', help='Only check that expected patch artifacts exist.')
    args = parser.parse_args()
    check_artifacts_exist()
    if not args.existence_only:
        check_plain_patch_applies()
        check_series_applies()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
