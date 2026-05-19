from __future__ import annotations

import json
import subprocess
from pathlib import Path

REPO_ROOT = Path('/workspace/tails-cloner')
if not REPO_ROOT.exists():
    # Tests can also be run from the host checkout, where /vagrant is the lab dir
    # and the repository root is three levels above this file.
    REPO_ROOT = Path(__file__).resolve().parents[4]

PLAN_PATH = REPO_ROOT / 'tails_issue_fix' / 'upstream-operation.yml'
ROADMAP_PATH = REPO_ROOT / 'tails_issue_fix' / 'ROADMAP.md'
README_PATH = REPO_ROOT / 'tails_issue_fix' / 'README.md'
STATE_PATH = Path('/opt/tails-cloner-fixtures/fixture-state.json')

REQUIRED_PHASES = [
    '0-design',
    '1-boot-source',
    '2-shared-storage-policy',
    '3-cloner-targets',
    '4-persistence',
    '5-upgrader',
    '6-docs',
]

REQUIRED_TOPICS = [
    'tails-8422',
    'tails-6397',
    'tails-7475',
    'tails-cloner-mr',
    'tails-persistence-mr',
    'tails-upgrader-mr',
]

REQUIRED_ACCEPTANCE_WORDS = [
    'Internal targets are hidden by default',
    'Internal targets are never auto-selected',
    'FSUUID',
    'Persistence and Upgrader operate only on the Tails system disk',
    'USB/SDIO/removable-media workflows remain unchanged',
]


def read(path: Path) -> str:
    assert path.exists(), f'missing required strategy file: {path}'
    return path.read_text()


def test_upstream_operation_plan_exists_and_covers_all_phases() -> None:
    plan = read(PLAN_PATH)
    for phase in REQUIRED_PHASES:
        assert phase in plan, f'missing phase in upstream-operation.yml: {phase}'
    for topic in REQUIRED_TOPICS:
        assert topic in plan, f'missing upstream posting target: {topic}'


def test_strategy_explicitly_scopes_out_standalone_app() -> None:
    combined = '\n'.join([read(PLAN_PATH), read(ROADMAP_PATH), read(README_PATH)])
    assert 'standalone tails-cloner app' in combined
    assert 'not the upstream solution' in combined or 'not the upstream deliverable' in combined
    assert 'Cloner-only' in combined


def test_acceptance_gates_cover_safety_correctness_and_regression() -> None:
    plan = read(PLAN_PATH)
    for expected in REQUIRED_ACCEPTANCE_WORDS:
        assert expected in plan, f'missing acceptance gate text: {expected}'


def test_roadmap_tells_where_to_post_and_when() -> None:
    roadmap = read(ROADMAP_PATH)
    assert 'Where to post what' in roadmap
    assert '#8422' in roadmap
    assert '#6397' in roadmap
    assert '#7475' in roadmap
    assert 'Patch/commit sequence' in roadmap
    assert 'Acceptance gates' in roadmap


def test_vagrant_fixture_has_four_policy_relevant_disks() -> None:
    if not STATE_PATH.exists():
        # Host-only strategy runs do not have the provisioned VM fixture state.
        return
    data = json.loads(STATE_PATH.read_text())
    required = {
        'source_disk',
        'target_fresh_disk',
        'target_upgrade_disk',
        'target_extra_disk',
    }
    missing = required - set(data)
    assert not missing, f'fixture-state.json missing keys: {sorted(missing)}'
    assert len({data[key] for key in required}) == len(required)


def test_upgrade_fixture_has_persistence_partition_when_lab_is_provisioned() -> None:
    if not STATE_PATH.exists():
        # Host-only strategy runs do not have the provisioned VM fixture state.
        return
    data = json.loads(STATE_PATH.read_text())
    target = data['target_upgrade_disk']
    lsblk = json.loads(
        subprocess.check_output(
            ['lsblk', '-J', '-o', 'NAME,PATH,FSTYPE,LABEL', target],
            text=True,
        )
    )
    parts: list[dict[str, object]] = []
    for device in lsblk.get('blockdevices', []):
        parts.extend(device.get('children') or [])
    labels = {str(part.get('label') or '') for part in parts}
    fstypes = {str(part.get('fstype') or '') for part in parts}
    assert 'persistence' in labels
    assert 'ext4' in fstypes
