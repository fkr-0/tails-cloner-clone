from __future__ import annotations

import json
import subprocess
from pathlib import Path


def test_unresolved_todos_are_exactly_real_boot_runtime_gaps() -> None:
    reporter = Path('infra/vagrant-lab/scripts/report_unresolved_todos.py')
    result = subprocess.run(
        [
            'python3',
            str(reporter),
            '--json',
            '--require-only-real-boot',
            '--require-count',
            '3',
        ],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )

    data = json.loads(result.stdout)
    assert data['unresolved_count'] == 3
    assert data['all_unresolved_are_real_boot_lane'] is True
    items = {item['id']: item for item in data['items']}
    assert set(items) == {'E2E-001', 'E2E-004', 'E2E-005'}
    assert items['E2E-001']['next_bridge_command'] == 'real-boot-next-install'
    assert items['E2E-004']['next_bridge_command'] == 'real-boot-next-upgrade-iso'
    assert items['E2E-005']['next_bridge_command'] == 'real-boot-next-upgrade-source-device'
    for item in items.values():
        assert item['latest_remaining_work']
        assert 'remaining work:' in item['latest_remaining_work']
        assert 'real-boot-state' in item['operator_commands']
        assert 'real-boot-bundle-strict' in item['operator_commands']
