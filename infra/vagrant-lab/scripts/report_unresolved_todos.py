#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
TODOS_PATH = REPO_ROOT / 'infra/vagrant-lab/todos.yml'
SCENARIOS_PATH = REPO_ROOT / 'infra/vagrant-lab/scenarios.yml'
OPEN_STATUSES = {'planned', 'partial', 'in_progress', 'blocked'}
REAL_BOOT_NEXT_BY_TODO = {
    'E2E-001': 'real-boot-next-install',
    'E2E-004': 'real-boot-next-upgrade-iso',
    'E2E-005': 'real-boot-next-upgrade-source-device',
}


def load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding='utf-8'))


def latest_remaining_note(notes: list[str]) -> str | None:
    remaining = [note for note in notes if 'remaining work:' in str(note)]
    if not remaining:
        return None
    return str(remaining[-1])


def scenario_summaries(refs: list[str], scenarios: dict[str, Any]) -> list[dict[str, Any]]:
    cases = scenarios.get('cases', {})
    result = []
    for ref in refs:
        case = cases.get(ref, {})
        result.append(
            {
                'ref': ref,
                'status': case.get('current_status') or case.get('status'),
                'group': case.get('group'),
                'next_step': case.get('next_step'),
                'bridge_next': case.get('bridge_real_boot_next_variant'),
                'persistent_serial_log': case.get('persistent_serial_log'),
                'status_snapshot': case.get('status_snapshot'),
            }
        )
    return result


def unresolved_items() -> list[dict[str, Any]]:
    todos = load_yaml(TODOS_PATH)
    scenarios = load_yaml(SCENARIOS_PATH)
    items = []
    for todo_id, item in sorted(todos.get('items', {}).items()):
        status = item.get('status')
        if status not in OPEN_STATUSES:
            continue
        refs = list(item.get('scenario_refs') or [])
        notes = [str(note) for note in item.get('implementation_notes', [])]
        bridge_next = REAL_BOOT_NEXT_BY_TODO.get(todo_id)
        if not bridge_next:
            for summary in scenario_summaries(refs, scenarios):
                if summary.get('bridge_next'):
                    bridge_next = summary['bridge_next']
                    break
        items.append(
            {
                'id': todo_id,
                'title': item.get('title'),
                'status': status,
                'priority': item.get('priority'),
                'kind': item.get('kind'),
                'scenario_refs': refs,
                'scenario_summaries': scenario_summaries(refs, scenarios),
                'latest_remaining_work': latest_remaining_note(notes),
                'next_bridge_command': bridge_next,
                'operator_commands': [
                    'real-boot-operator',
                    'real-boot-state',
                    bridge_next,
                    'real-boot-snapshot',
                    'real-boot-bundle-strict',
                ]
                if bridge_next
                else [],
            }
        )
    return items


def build_report() -> dict[str, Any]:
    items = unresolved_items()
    by_status: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    for item in items:
        by_status[item['status']] = by_status.get(item['status'], 0) + 1
        by_kind[item['kind']] = by_kind.get(item['kind'], 0) + 1
    return {
        'unresolved_count': len(items),
        'by_status': by_status,
        'by_kind': by_kind,
        'items': items,
        'all_unresolved_are_real_boot_lane': all(item.get('kind') == 'real_boot_lane' for item in items),
    }


def print_human(report: dict[str, Any]) -> None:
    print('unresolved lab TODOs')
    print(f"unresolved_count: {report['unresolved_count']}")
    print(f"by_status: {report['by_status']}")
    print(f"all_unresolved_are_real_boot_lane: {report['all_unresolved_are_real_boot_lane']}")
    for item in report['items']:
        print(f"- {item['id']} [{item['status']}]: {item['title']}")
        print(f"  kind: {item['kind']} priority={item['priority']}")
        if item.get('latest_remaining_work'):
            print(f"  latest: {item['latest_remaining_work']}")
        if item.get('next_bridge_command'):
            print(f"  next bridge: {item['next_bridge_command']}")
        for scenario in item.get('scenario_summaries', []):
            print(f"  scenario {scenario['ref']}: status={scenario['status']} group={scenario['group']}")
            if scenario.get('persistent_serial_log'):
                print(f"    serial log: {scenario['persistent_serial_log']}")


def main() -> int:
    parser = argparse.ArgumentParser(description='Report unresolved partial/planned/in-progress lab TODOs and next commands.')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--require-only-real-boot', action='store_true')
    parser.add_argument('--require-count', type=int)
    args = parser.parse_args()
    report = build_report()
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_human(report)
    if args.require_only_real_boot and not report['all_unresolved_are_real_boot_lane']:
        return 1
    if args.require_count is not None and report['unresolved_count'] != args.require_count:
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
