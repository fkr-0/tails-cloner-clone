from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_fixture_state_exists() -> None:
    state_path = Path('/opt/tails-cloner-fixtures/fixture-state.json')
    if not state_path.exists():
        pytest.skip('requires provisioned Vagrant lab fixtures')
    data = json.loads(state_path.read_text())
    assert 'target_upgrade_disk' in data


def test_tails_test_artifacts_exist() -> None:
    artifact_dir = Path('/opt/tails-cloner-fixtures/tails-images')
    if not artifact_dir.exists():
        pytest.skip('requires provisioned Vagrant lab artifacts')
    expected = [
        'tails-amd64-7.7.1.img',
        'tails-amd64-7.7.2.img',
    ]
    for name in expected:
        assert (artifact_dir / name).exists(), f'missing artifact: {name}'
