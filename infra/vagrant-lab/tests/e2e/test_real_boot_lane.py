from __future__ import annotations

import json
import subprocess
from pathlib import Path

import yaml

REPO_ROOT = Path('/workspace/tails-cloner')
if not REPO_ROOT.exists():
    REPO_ROOT = Path(__file__).resolve().parents[4]

LANE_DIR = REPO_ROOT / 'infra/vagrant-lab/real-boot-lane'
BOOT_SCRIPT = LANE_DIR / 'boot_tails_qemu.sh'
PREFLIGHT = LANE_DIR / 'preflight_real_boot_lane.py'
BOOT_MATRIX = LANE_DIR / 'boot_matrix.yml'
GUEST_PROBE_CONTRACT = LANE_DIR / 'guest_probe_contract.yml'


def test_boot_script_has_safe_dry_run_headless_timeout_options(tmp_path: Path) -> None:
    image = tmp_path / 'tails-amd64-test.img'
    image.write_bytes(b'not a real image; dry-run only')
    extra = tmp_path / 'target.raw'
    extra.write_bytes(b'target placeholder')

    result = subprocess.run(
        [
            'bash',
            str(BOOT_SCRIPT),
            '--dry-run',
            '--headless',
            '--timeout',
            '1',
            '--extra-drive',
            str(extra),
            '--no-network',
            str(image),
        ],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )

    command = result.stdout
    assert 'timeout --foreground 1' in command
    assert 'qemu-system-x86_64' in command
    assert '-display none' in command
    assert '-serial mon:stdio' in command
    assert 'snapshot=on' in command
    assert '-net none' in command
    assert str(extra) in command
    assert str(image) in command


def test_real_boot_preflight_reports_three_non_destructive_variants() -> None:
    result = subprocess.run(
        ['python3', str(PREFLIGHT), '--json'],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    data = json.loads(result.stdout)

    assert data['status'] == 'preflight_passed'
    assert len(data['plans']) == 3
    assert all(plan['destructive_by_default'] is False for plan in data['plans'])
    assert all(plan['uses_snapshot_mode'] is True for plan in data['plans'])
    assert {plan['variant'] for plan in data['plans']} == {
        'running-live-install',
        'outdated-running-iso-upgrade',
        'outdated-running-source-device-upgrade',
    }


def test_boot_matrix_links_remaining_real_boot_cases() -> None:
    matrix = yaml.safe_load(BOOT_MATRIX.read_text(encoding='utf-8'))
    refs = {
        ref
        for variant in matrix['variants'].values()
        for ref in variant['scenario_refs']
    }
    assert 'install.running_live_iso_usb' in refs
    assert 'upgrade.outdated_running_tails.source_iso_on_disc' in refs
    assert 'upgrade.outdated_running_tails.source_not_running_live_iso_usb' in refs



def test_qmp_probe_script_compiles_and_uses_safe_launcher_options() -> None:
    probe = (LANE_DIR / 'probe_qemu_qmp.py').read_text(encoding='utf-8')
    assert "--qmp" in probe
    assert "--pidfile" in probe
    assert "--headless" in probe
    assert "--no-network" in probe
    assert "query-status" in probe
    assert "query-block" in probe
    assert "quit" in probe


def test_boot_script_dry_run_supports_qmp_and_pidfile(tmp_path: Path) -> None:
    image = tmp_path / 'tails-amd64-test.img'
    image.write_bytes(b'not a real image; dry-run only')
    qmp_socket = tmp_path / 'qmp.sock'
    pidfile = tmp_path / 'qemu.pid'

    result = subprocess.run(
        [
            'bash',
            str(BOOT_SCRIPT),
            '--dry-run',
            '--headless',
            '--qmp',
            str(qmp_socket),
            '--pidfile',
            str(pidfile),
            str(image),
        ],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )

    command = result.stdout
    assert '-qmp' in command
    assert 'unix:' in command
    assert str(qmp_socket) in command
    assert 'server=on' in command
    assert 'wait=off' in command
    assert '-pidfile' in command
    assert str(pidfile) in command



def test_guest_probe_contract_covers_every_real_boot_variant() -> None:
    matrix = yaml.safe_load(BOOT_MATRIX.read_text(encoding='utf-8'))
    contract = yaml.safe_load(GUEST_PROBE_CONTRACT.read_text(encoding='utf-8'))
    variants = set(matrix['variants'])
    requirement_variants = set(contract['variant_requirements'])

    assert contract['contract'] == 'tails_guest_readiness_probe'
    assert variants <= requirement_variants
    assert any('qmp_probe.success' in item for item in contract['completion_gate']['implemented_status_requires'])
    assert any('guest_probe_output' in item for item in contract['completion_gate']['implemented_status_requires'])


def test_real_boot_preflight_reports_guest_probe_contract() -> None:
    result = subprocess.run(
        ['python3', str(PREFLIGHT), '--json'],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    data = json.loads(result.stdout)

    assert data['guest_probe_contract'] == 'tails_guest_readiness_probe'
    assert data['guest_probe_status'] == 'planned'
    assert any(path.endswith('guest_probe_contract.yml') for path in data['metadata_files'])



def test_boot_script_dry_run_supports_serial_log_and_readonly_share(tmp_path: Path) -> None:
    image = tmp_path / 'tails-amd64-test.img'
    image.write_bytes(b'not a real image; dry-run only')
    serial_log = tmp_path / 'logs' / 'serial.log'
    share_dir = tmp_path / 'share'
    share_dir.mkdir()

    result = subprocess.run(
        [
            'bash',
            str(BOOT_SCRIPT),
            '--dry-run',
            '--headless',
            '--serial-log',
            str(serial_log),
            '--share-dir',
            f'{share_dir},tailscloner',
            str(image),
        ],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )

    command = result.stdout
    assert '-serial' in command
    assert f'file:{serial_log}' in command
    assert '-fsdev' in command
    assert str(share_dir) in command
    assert 'readonly=on' in command
    assert 'virtio-9p-pci' in command
    assert 'mount_tag=tailscloner' in command



def test_capture_guest_probe_runner_dry_run_builds_serial_share_qmp_command(tmp_path: Path) -> None:
    runner = LANE_DIR / 'capture_guest_probe_from_qemu.py'
    result = subprocess.run(
        [
            'python3',
            str(runner),
            '--version',
            '7.7.2',
            '--scenario-variant',
            'running-live-install',
            '--wait-timeout',
            '1',
            '--serial-log',
            str(tmp_path / 'serial.log'),
            '--share-dir',
            str(REPO_ROOT),
            '--prepare-share',
            '--interactive-display',
            '--dry-run',
        ],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )

    data = json.loads(result.stdout)
    command = data['command']
    assert data['success'] is True
    assert data['dry_run'] is True
    assert '--serial-log' in command
    assert '--share-dir' in command
    assert '--qmp' in command
    assert '--pidfile' in command
    assert data['share_tag'] == 'tailscloner'
    assert data['prepare_share'] is True
    assert data['headless'] is False
    assert 'run_guest_probe.sh' in data['guest_command']



def test_prepare_guest_probe_share_outputs_mount_and_run_bundle(tmp_path: Path) -> None:
    runner = LANE_DIR / 'prepare_guest_probe_share.py'
    output_dir = tmp_path / 'share'
    result = subprocess.run(
        [
            'python3',
            str(runner),
            '--output-dir',
            str(output_dir),
            '--scenario-variant',
            'running-live-install',
            '--tag',
            'tailscloner',
            '--mount-point',
            '/mnt/tailscloner',
        ],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )

    assert Path(result.stdout.strip()) == output_dir
    run_script = output_dir / 'run_guest_probe.sh'
    readme = output_dir / 'README.md'
    assert (output_dir / 'guest_probe.py').exists()
    assert run_script.exists()
    assert readme.exists()
    run_text = run_script.read_text(encoding='utf-8')
    assert 'mount -t 9p' in run_text
    assert '--transport serial_marker' in run_text
    assert '--prefix' in run_text
    assert 'tee "$SERIAL_DEVICE"' in run_text
    assert 'TAILS_CLONER_GUEST_PROBE=' in readme.read_text(encoding='utf-8')



def test_plan_capture_session_builds_variant_specific_commands() -> None:
    planner = LANE_DIR / 'plan_capture_session.py'
    result = subprocess.run(
        [
            'python3',
            str(planner),
            '--variant',
            'outdated-running-source-device-upgrade',
            '--role',
            'outdated_controller_img=7.6',
            '--role',
            'newer_attached_source_media=/tmp/source.img',
            '--role',
            'persistent_target_media=/tmp/target.img',
        ],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )

    data = json.loads(result.stdout)
    assert data['scenario_ref'] == 'upgrade.outdated_running_tails.source_not_running_live_iso_usb'
    assert data['todo_ref'] == 'E2E-005'
    assert data['capture_command'].count('--extra-drive') == 2
    assert '--interactive-display' in data['capture_command']
    assert '--prepare-share' in data['capture_command']
    assert '--mark-done' in data['record_command']



def test_resolve_capture_roles_emits_plan_for_running_variant() -> None:
    resolver = LANE_DIR / 'resolve_capture_roles.py'
    result = subprocess.run(
        [
            'python3',
            str(resolver),
            '--variant',
            'running-live-install',
            '--emit-plan',
        ],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )

    data = json.loads(result.stdout)
    assert data['ready'] is True
    assert 'current_tails_img' in data['roles']
    assert data['missing_roles'] == []
    assert data['plan']['todo_ref'] == 'E2E-001'
    assert '--scenario-variant' in data['plan']['capture_command']



def test_resolve_capture_roles_can_require_existing_media_paths(tmp_path: Path) -> None:
    resolver = LANE_DIR / 'resolve_capture_roles.py'
    missing_source = tmp_path / 'missing-source.img'
    missing_target = tmp_path / 'missing-target.img'
    result = subprocess.run(
        [
            'python3',
            str(resolver),
            '--variant',
            'outdated-running-source-device-upgrade',
            '--role',
            f'newer_attached_source_media={missing_source}',
            '--role',
            f'persistent_target_media={missing_target}',
            '--require-existing-media',
        ],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
    )

    data = json.loads(result.stdout)
    assert result.returncode == 1
    assert data['ready'] is False
    assert set(data['missing_media_paths']) == {
        'newer_attached_source_media',
        'persistent_target_media',
    }
    assert data['media_path_status']['newer_attached_source_media']['exists'] is False
    assert data['media_path_status']['persistent_target_media']['exists'] is False



def test_report_capture_readiness_summarizes_all_variants() -> None:
    reporter = LANE_DIR / 'report_capture_readiness.py'
    result = subprocess.run(
        [
            'python3',
            str(reporter),
            '--require-existing-media',
            '--json',
        ],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
    )

    data = json.loads(result.stdout)
    assert data['total_count'] == 3
    assert {row['variant'] for row in data['variants']} == {
        'running-live-install',
        'outdated-running-iso-upgrade',
        'outdated-running-source-device-upgrade',
    }
    assert any(row['ready'] for row in data['variants'])
    assert any(row['missing_roles'] or row['missing_media_paths'] for row in data['variants'])



def test_emit_capture_runbook_writes_running_variant_runbook(tmp_path: Path) -> None:
    emitter = LANE_DIR / 'emit_capture_runbook.py'
    result = subprocess.run(
        [
            'python3',
            str(emitter),
            '--variant',
            'running-live-install',
            '--require-existing-media',
            '--output-dir',
            str(tmp_path),
        ],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )

    data = json.loads(result.stdout)
    runbook = Path(data['runbook'])
    assert data['success'] is True
    assert runbook.exists()
    text = runbook.read_text(encoding='utf-8')
    assert 'running-live-install' in text
    assert 'Capture command:' in text
    assert 'Record command:' in text
    assert 'capture_guest_probe_from_qemu.py' in text
    assert 'record_guest_probe_evidence.py' in text
    assert 'Guest command to run inside Tails:' in text
    assert 'sudo mkdir -p /mnt/tailscloner' in text
    assert '/mnt/tailscloner/run_guest_probe.sh' in text



def test_prepare_capture_media_smoke_creates_role_paths(tmp_path: Path) -> None:
    preparer = LANE_DIR / 'prepare_capture_media.py'
    result = subprocess.run(
        [
            'python3',
            str(preparer),
            '--output-dir',
            str(tmp_path),
            '--persistent-size-mib',
            '64',
            '--source-size-mib',
            '64',
            '--force',
        ],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )

    data = json.loads(result.stdout)
    assert Path(data['newer_img_on_disc']).exists()
    assert Path(data['persistent_target_media']).exists()
    assert Path(data['newer_attached_source_media']).exists()
    assert set(data['roles']) == {
        'newer_img_on_disc',
        'persistent_target_media',
        'newer_attached_source_media',
    }



def test_prepare_all_capture_runbooks_emits_all_variants(tmp_path: Path) -> None:
    preparer = LANE_DIR / 'prepare_all_capture_runbooks.py'
    result = subprocess.run(
        [
            'python3',
            str(preparer),
            '--media-dir',
            str(tmp_path / 'media'),
            '--runbook-dir',
            str(tmp_path / 'runbooks'),
            '--force',
        ],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )

    data = json.loads(result.stdout)
    assert data['success'] is True
    assert data['readiness']['ready_count'] == 3
    assert set(data['runbooks']) == {
        'running-live-install',
        'outdated-running-iso-upgrade',
        'outdated-running-source-device-upgrade',
    }
    for runbook in data['runbooks'].values():
        path = Path(runbook['runbook'])
        assert path.exists()
        assert 'Guest command to run inside Tails:' in path.read_text(encoding='utf-8')
    readme = Path(data['operator_readme'])
    assert readme.exists()
    serial_log_dir = Path(data['serial_log_dir'])
    assert serial_log_dir.exists()
    assert serial_log_dir.is_dir()
    readme_text = readme.read_text(encoding='utf-8')
    assert 'Runtime sequence' in readme_text
    assert 'running-live-install.sh capture' in readme_text
    assert 'outdated-running-iso-upgrade.sh record' in readme_text
    assert 'outdated-running-source-device-upgrade.sh print' in readme_text



def test_report_capture_evidence_accepts_valid_log_override(tmp_path: Path) -> None:
    reporter = LANE_DIR / 'report_capture_evidence.py'
    probe = {
        'transport': 'unit',
        'timestamp_utc': '2026-05-20T00:00:00+00:00',
        'scenario_variant': 'running-live-install',
        'live_version_path': {
            'path': '/lib/live/mount/medium/live/Tails.version',
            'exists': True,
            'content': '7.7.2-test',
        },
        'running_tails_detection': {
            'is_running_tails': True,
            'running_tails_version': '7.7.2-test',
            'running_tails_device': '/dev/sdb1',
            'running_tails_size_bytes': 8589934592,
        },
        'block_devices': {
            'source_parent_disk': '/dev/sdb',
            'target_candidates': [
                {'path': '/dev/sdb', 'has_tails': True, 'excluded_because_source': True},
                {'path': '/dev/sdc', 'has_tails': False, 'excluded_because_source': False},
            ],
        },
        'fsuuid_boot': {
            'proc_cmdline': 'boot=live live-media=removable',
            'cmdline_options': {'boot': 'live', 'live-media': 'removable'},
            'fsuuid': '',
            'fsuuid_resolution': {},
            'live_medium': {
                'medium_path': '/lib/live/mount/medium',
                'mount_source': '/dev/sdb1',
                'mount_source_parent_disk': '/dev/sdb',
                'blkid': {'UUID': 'A690-20D2'},
            },
            'tails_media_devices': [
                {
                    'path': '/dev/sdb1',
                    'parent_disk': '/dev/sdb',
                    'fstype': 'vfat',
                    'label': 'TAILS',
                    'uuid': 'A690-20D2',
                    'size': 8589934592,
                },
            ],
            'live_medium_matches_fsuuid': False,
        },
        'project_access': {
            'checkout_visible': True,
            'python_import_tails_cloner': True,
        },
    }
    log_file = tmp_path / 'running.log'
    log_file.write_text('noise\nTAILS_CLONER_GUEST_PROBE=' + json.dumps(probe) + '\n', encoding='utf-8')

    result = subprocess.run(
        [
            'python3',
            str(reporter),
            '--log',
            f'running-live-install={log_file}',
            '--json',
        ],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )

    data = json.loads(result.stdout)
    row = next(row for row in data['variants'] if row['variant'] == 'running-live-install')
    assert row['status'] == 'valid'
    assert row['validation']['success'] is True



def test_report_prepared_capture_readiness_reports_ready_after_prepare_all() -> None:
    preparer = LANE_DIR / 'prepare_all_capture_runbooks.py'
    reporter = LANE_DIR / 'report_prepared_capture_readiness.py'
    subprocess.run(['python3', str(preparer), '--force'], check=True, text=True, stdout=subprocess.PIPE)

    result = subprocess.run(
        ['python3', str(reporter), '--json', '--require-all-ready'],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )

    data = json.loads(result.stdout)
    assert data['ready_count'] == 3
    assert data['total_count'] == 3
    assert all(data['prepared_role_exists'].values())



def test_report_real_boot_state_summarizes_ready_runbooks_after_prepare_all() -> None:
    preparer = LANE_DIR / 'prepare_all_capture_runbooks.py'
    reporter = LANE_DIR / 'report_real_boot_state.py'
    subprocess.run(['python3', str(preparer), '--force'], check=True, text=True, stdout=subprocess.PIPE)

    result = subprocess.run(
        ['python3', str(reporter), '--json'],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )

    data = json.loads(result.stdout)
    assert data['total_count'] == 3
    assert data['ready_count'] == 3
    assert data['all_ready'] is True
    assert data['operator_readme_exists'] is True
    assert data['valid_evidence_count'] <= 3
    for row in data['variants']:
        assert row['ready'] is True
        assert row['runbook_exists'] is True
        assert row['next_action']
        assert row['evidence_status'] in {
            'missing_log',
            'missing_marker',
            'invalid_marker',
            'valid',
            'unknown',
        }



def test_report_real_boot_preflight_reports_prepared_assets() -> None:
    preparer = LANE_DIR / 'prepare_all_capture_runbooks.py'
    reporter = LANE_DIR / 'report_real_boot_preflight.py'
    subprocess.run(['python3', str(preparer), '--force'], check=True, text=True, stdout=subprocess.PIPE)

    result = subprocess.run(
        ['python3', str(reporter), '--json'],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )

    data = json.loads(result.stdout)
    assert data['checks']['cached_images_ready'] is True
    assert data['checks']['media_ready'] is True
    assert data['checks']['runbooks_ready'] is True
    assert data['checks']['readme_ready'] is True
    assert set(data['runbooks']) == {
        'running-live-install',
        'outdated-running-iso-upgrade',
        'outdated-running-source-device-upgrade',
    }
    assert 'qemu-system-x86_64' in data['tools']
    assert 'kvm' in data
    assert 'display' in data



def test_next_real_boot_action_selects_first_missing_evidence_variant() -> None:
    preparer = LANE_DIR / 'prepare_all_capture_runbooks.py'
    helper = LANE_DIR / 'next_real_boot_action.py'
    subprocess.run(['python3', str(preparer), '--force'], check=True, text=True, stdout=subprocess.PIPE)

    result = subprocess.run(
        ['python3', str(helper), '--json'],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )

    data = json.loads(result.stdout)
    assert data['variant'] == 'running-live-install'
    assert data['todo_ref'] == 'E2E-001'
    assert data['status'] == 'ready_for_runtime_capture'
    assert any('running-live-install.sh capture' in command for command in data['commands'])
    assert any('running-live-install.sh record' in command for command in data['commands'])


def test_next_real_boot_action_accepts_specific_variant() -> None:
    preparer = LANE_DIR / 'prepare_all_capture_runbooks.py'
    helper = LANE_DIR / 'next_real_boot_action.py'
    subprocess.run(['python3', str(preparer), '--force'], check=True, text=True, stdout=subprocess.PIPE)

    result = subprocess.run(
        ['python3', str(helper), '--variant', 'outdated-running-iso-upgrade', '--json'],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )

    data = json.loads(result.stdout)
    assert data['variant'] == 'outdated-running-iso-upgrade'
    assert data['todo_ref'] == 'E2E-004'
    assert data['status'] == 'ready_for_runtime_capture'
    assert any('outdated-running-iso-upgrade.sh capture' in command for command in data['commands'])



def test_report_real_boot_artifacts_lists_persistent_capture_assets() -> None:
    preparer = LANE_DIR / 'prepare_all_capture_runbooks.py'
    reporter = LANE_DIR / 'report_real_boot_artifacts.py'
    subprocess.run(['python3', str(preparer), '--force'], check=True, text=True, stdout=subprocess.PIPE)

    result = subprocess.run(
        ['python3', str(reporter), '--json', '--require-capture-ready'],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )

    data = json.loads(result.stdout)
    assert data['ready_for_capture_attempt'] is True
    assert data['checks']['tails_images_present'] is True
    assert data['checks']['capture_media_present'] is True
    assert data['checks']['runbooks_present'] is True
    assert data['checks']['operator_readme_present'] is True
    assert data['checks']['serial_log_dir_present'] is True
    assert len(data['expected_runbooks']) == 3
    assert len(data['expected_serial_logs']) == 3
    assert all('out/serial-logs' in path for path in data['expected_serial_logs'])



def test_real_boot_operator_doc_lists_bridge_workflow_and_persistent_logs() -> None:
    doc = LANE_DIR / 'OPERATOR.md'
    text = doc.read_text(encoding='utf-8')
    for command in [
        'real-boot-validate-local',
        'real-boot-prepare-runbooks',
        'real-boot-preflight',
        'real-boot-artifacts',
        'real-boot-state',
        'real-boot-next-install',
        'real-boot-next-upgrade-iso',
        'real-boot-next-upgrade-source-device',
        'real-boot-evidence-strict',
        'real-boot-snapshot',
        'real-boot-bundle',
        'real-boot-bundle-strict',
    ]:
        assert command in text
    for path in [
        'infra/vagrant-lab/real-boot-lane/out/serial-logs/',
        'infra/vagrant-lab/real-boot-lane/out/status-snapshots/latest.json',
        'infra/vagrant-lab/real-boot-lane/out/status-snapshots/latest.md',
        'infra/vagrant-lab/real-boot-lane/out/evidence-bundles/latest.tar.gz',
        'infra/vagrant-lab/real-boot-lane/out/evidence-bundles/latest.manifest.json',
    ]:
        assert path in text
    assert '/tmp/tails-guest-probe' not in text



def test_snapshot_real_boot_status_writes_json_and_markdown(tmp_path: Path) -> None:
    preparer = LANE_DIR / 'prepare_all_capture_runbooks.py'
    snapshotter = LANE_DIR / 'snapshot_real_boot_status.py'
    subprocess.run(['python3', str(preparer), '--force'], check=True, text=True, stdout=subprocess.PIPE)

    result = subprocess.run(
        ['python3', str(snapshotter), '--output-dir', str(tmp_path)],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )

    data = json.loads(result.stdout)
    json_path = Path(data['json_path'])
    markdown_path = Path(data['markdown_path'])
    assert json_path.exists()
    assert markdown_path.exists()
    snapshot = json.loads(json_path.read_text(encoding='utf-8'))
    assert snapshot['summary']['all_ready'] is True
    assert snapshot['summary']['ready_count'] == 3
    assert snapshot['summary']['total_count'] == 3
    assert set(snapshot['next_actions']) == {
        'running-live-install',
        'outdated-running-iso-upgrade',
        'outdated-running-source-device-upgrade',
    }
    markdown = markdown_path.read_text(encoding='utf-8')
    assert 'Real-boot status snapshot' in markdown
    assert 'real-boot-evidence-strict' in markdown



def test_bundle_real_boot_evidence_writes_archive_without_runtime_logs(tmp_path: Path) -> None:
    preparer = LANE_DIR / 'prepare_all_capture_runbooks.py'
    snapshotter = LANE_DIR / 'snapshot_real_boot_status.py'
    bundler = LANE_DIR / 'bundle_real_boot_evidence.py'
    subprocess.run(['python3', str(preparer), '--force'], check=True, text=True, stdout=subprocess.PIPE)
    subprocess.run(['python3', str(snapshotter)], check=True, text=True, stdout=subprocess.PIPE)

    result = subprocess.run(
        ['python3', str(bundler), '--output-dir', str(tmp_path)],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )

    data = json.loads(result.stdout)
    assert data['success'] is True
    assert data['all_serial_logs_present'] is False
    assert Path(data['archive']).exists()
    assert Path(data['latest_archive']).exists()
    assert Path(data['manifest']).exists()


def test_bundle_real_boot_evidence_strict_requires_runtime_logs(tmp_path: Path) -> None:
    bundler = LANE_DIR / 'bundle_real_boot_evidence.py'
    result = subprocess.run(
        ['python3', str(bundler), '--output-dir', str(tmp_path), '--require-serial-logs'],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
    )

    data = json.loads(result.stdout)
    assert result.returncode == 1
    assert data['success'] is False
    assert len(data['missing_serial_logs']) == 3
    assert all('out/serial-logs' in path for path in data['missing_serial_logs'])


def test_fsuuid_direct_kernel_smoke_dry_run_declares_two_media_topology() -> None:
    runner = LANE_DIR / 'run_fsuuid_direct_kernel_smoke.py'
    result = subprocess.run(
        ['python3', str(runner), '--dry-run', '--json'],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    data = json.loads(result.stdout)

    assert data['status'] == 'dry-run'
    assert data['variant'] == 'fsuuid-two-valid-media-direct-kernel-smoke'
    assert 'FSUUID=A690-20D2' in data['append']
    assert 'live-media=removable' in data['append']
    assert data['noautologin'] is False
    assert 'noautologin' not in data['append']
    assert data['boot_image'].endswith('tails-amd64-7.7.2-boot-8g.img')
    assert data['extra_image'].endswith('tails-amd64-7.7.1-extra.img')
    assert '-kernel' in data['command']
    assert '-initrd' in data['command']
    assert '-serial' in data['command']
    assert '-net' in data['command']
    assert 'none' in data['command']


def test_fsuuid_direct_kernel_smoke_runner_checks_expected_patterns() -> None:
    runner = (LANE_DIR / 'run_fsuuid_direct_kernel_smoke.py').read_text(encoding='utf-8')

    assert "FSUUID = \'A690-20D2\'" in runner
    assert "EXTRA_UUID = 'BEEF-1234'" in runner
    assert 'selected_system_partition' in runner
    assert 'early_fsuuid_evidence_complete' in runner
    assert 'SYSTEM_PARTITION=/dev/vda1' in runner
    assert 'SYSTEM_PARTITION=/dev/vdb1' in runner

