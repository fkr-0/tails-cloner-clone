from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path('/workspace/tails-cloner')
if not REPO_ROOT.exists():
    REPO_ROOT = Path(__file__).resolve().parents[4]

ISSUE_FIX = REPO_ROOT / 'tails_issue_fix'
TAILS_TREE = ISSUE_FIX / 'tails'
PATCH_DIR = ISSUE_FIX / 'patches'
SERIES_DIR = PATCH_DIR / 'cloner-internal-targets-series'
COMBINED_SERIES = PATCH_DIR / 'tails-cloner-guarded-internal-targets.series.patch'
PLAIN_PATCH = PATCH_DIR / '0001-tails-cloner-guarded-internal-targets.patch'

TOUCHED_FILES = [
    'config/chroot_local-includes/usr/lib/python3/dist-packages/tails_installer/creator.py',
    'config/chroot_local-includes/usr/lib/python3/dist-packages/tails_installer/gui.py',
    'config/chroot_local-includes/usr/lib/python3/dist-packages/tails_installer/utils.py',
]


def run(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        check=True,
        text=True,
        capture_output=True,
    )


def test_cloner_patch_series_artifacts_exist() -> None:
    patches = sorted(SERIES_DIR.glob('*.patch'))
    assert [patch.name for patch in patches] == [
        '0001-tails-installer-expose-admin-password-state.patch',
        '0002-tails-installer-gate-internal-target-enumeration.patch',
        '0003-tails-installer-confirm-internal-storage-targets.patch',
        '0004-tails-installer-refuse-source-and-running-devices-as.patch',
    ]
    assert COMBINED_SERIES.exists()
    assert PLAIN_PATCH.exists()


def test_cloner_patch_series_applies_to_current_upstream_devel(tmp_path: Path) -> None:
    assert TAILS_TREE.exists(), f'missing nested Tails tree: {TAILS_TREE}'
    patches = sorted(SERIES_DIR.glob('*.patch'))
    assert patches, f'missing patch series in {SERIES_DIR}'

    worktree = tmp_path / 'tails-series-worktree'
    run(['git', 'worktree', 'add', '--detach', str(worktree), 'origin/devel'], TAILS_TREE)
    try:
        run(['git', 'am', '--3way', *[str(patch) for patch in patches]], worktree)
        run(['python3', '-m', 'py_compile', *TOUCHED_FILES], worktree)
        log = run(['git', 'log', '--format=%s', '--reverse', 'origin/devel..HEAD'], worktree)
        assert log.stdout.splitlines() == [
            'tails-installer: expose admin password state',
            'tails-installer: gate internal target enumeration',
            'tails-installer: confirm internal storage targets',
        ]
    finally:
        run(['git', 'worktree', 'remove', '--force', str(worktree)], TAILS_TREE)


def test_plain_cloner_patch_applies_to_current_upstream_devel(tmp_path: Path) -> None:
    worktree = tmp_path / 'tails-plain-worktree'
    run(['git', 'worktree', 'add', '--detach', str(worktree), 'origin/devel'], TAILS_TREE)
    try:
        run(['git', 'apply', '--check', str(PLAIN_PATCH)], worktree)
        run(['git', 'apply', str(PLAIN_PATCH)], worktree)
        run(['python3', '-m', 'py_compile', *TOUCHED_FILES], worktree)
    finally:
        run(['git', 'worktree', 'remove', '--force', str(worktree)], TAILS_TREE)
