from __future__ import annotations

import re
from pathlib import Path

import yaml


def test_tails_artifact_checksums_are_pinned_and_required() -> None:
    vars_path = Path("infra/vagrant-lab/ansible/group_vars/all.yml")
    data = yaml.safe_load(vars_path.read_text(encoding="utf-8"))
    assert data["tails_artifact_checksum_policy"] == "require"
    artifacts = data["tails_test_artifacts"]
    assert {artifact["filename"] for artifact in artifacts} == {
        "tails-amd64-7.7.1.img",
        "tails-amd64-7.7.2.img",
    }
    for artifact in artifacts:
        assert re.fullmatch(r"[0-9a-f]{64}", artifact["sha256"]), artifact
