from __future__ import annotations

import json
import subprocess
from pathlib import Path


def test_scenario_coverage_reporter_tracks_guarded_destructive_flows() -> None:
    reporter = Path('infra/vagrant-lab/scripts/report_scenario_coverage.py')
    result = subprocess.run(
        ['python3', str(reporter), '--json', '--require-no-unguarded-destructive'],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )

    data = json.loads(result.stdout)
    assert data['case_count'] == 7
    assert data['runtime_real_boot_gap_count'] == 3
    assert not data['unguarded_destructive_cases']
    assert data['destructive_flag'] == 'TAILS_CLONER_LAB_ALLOW_DESTRUCTIVE'
    for capability, present in data['runner_capabilities'].items():
        assert present, capability
    cases = {case['case']: case for case in data['cases']}
    assert cases['upgrade.any_linux.source_running_live_iso_usb']['has_preflight_command'] is True
    assert cases['upgrade.any_linux.source_running_live_iso_usb']['has_verified_evidence'] is True
    assert cases['upgrade.outdated_running_tails.source_iso_on_disc']['status'] == 'partial'
    assert cases['upgrade.outdated_running_tails.source_not_running_live_iso_usb']['status'] == 'planned'
