from dataclasses import replace
from typing import Any

from tails_cloner.models import BlockDevice
from tails_cloner.planner import OperationKind, OperationSource, plan_operation


def device(**overrides: Any) -> BlockDevice:
    base = BlockDevice(
        path="/dev/sdb",
        size_bytes=16_000_000_000,
        size_label="14.9 GiB",
        model="USB Drive",
        vendor="SanDisk",
        transport="usb",
        removable=True,
    )
    return replace(base, **overrides)


def source() -> OperationSource:
    return OperationSource(type="image", path="/tmp/tails.img")


def test_install_plan_allows_plain_selectable_target() -> None:
    plan = plan_operation(OperationKind.INSTALL, source(), device())

    assert plan.would_write is True
    assert plan.action_label == "Install"
    assert plan.blocking_errors == []
    assert plan.warnings == []


def test_install_plan_rejects_image_larger_than_target() -> None:
    plan = plan_operation(
        OperationKind.INSTALL,
        OperationSource(type="image", path="/tmp/tails.img", size_bytes=17_000_000_000),
        device(size_bytes=16_000_000_000),
    )

    assert plan.would_write is False
    assert any("larger than the target device" in error for error in plan.blocking_errors)


def test_install_plan_requires_a_selected_local_image() -> None:
    plan = plan_operation(OperationKind.INSTALL, OperationSource(type="image"), device())

    assert plan.would_write is False
    assert plan.blocking_errors == ["Choose a local ISO or IMG file before starting a write operation."]


def test_remote_source_must_be_downloaded_before_write() -> None:
    plan = plan_operation(
        OperationKind.INSTALL,
        OperationSource(type="remote_image", path="https://example.invalid/tails.img"),
        device(),
    )

    assert plan.would_write is False
    assert plan.blocking_errors == ["Download the selected remote IMG before starting a write operation."]


def test_attached_source_only_supports_upgrade() -> None:
    attached = OperationSource(type="attached_source", device="/dev/sdc", version="7.7.2")

    install_plan = plan_operation(OperationKind.INSTALL, attached, device())
    upgrade_plan = plan_operation(OperationKind.UPGRADE, attached, device(has_tails=True))

    assert install_plan.would_write is False
    assert install_plan.blocking_errors == [
        "Attached live sources are only supported for persistence-preserving upgrades."
    ]
    assert upgrade_plan.would_write is True


def test_install_plan_refuses_disabled_running_source_target() -> None:
    plan = plan_operation(
        OperationKind.INSTALL,
        source(),
        device(
            is_running_system_device=True,
            disabled_reason="This is the device currently running Tails.",
        ),
    )

    assert plan.would_write is False
    assert plan.action_label == "Not selectable"
    assert plan.blocking_errors == ["This is the device currently running Tails."]


def test_install_plan_warns_for_existing_tails_reinstall() -> None:
    plan = plan_operation(OperationKind.INSTALL, source(), device(has_tails=True))

    assert plan.would_write is True
    assert plan.action_label == "Reinstall (delete all data)"
    assert plan.warnings == ["target already contains Tails; install would reinstall and may remove Persistent Storage"]


def test_upgrade_plan_requires_existing_tails() -> None:
    plan = plan_operation(OperationKind.UPGRADE, source(), device(has_tails=False))

    assert plan.would_write is False
    assert plan.action_label == "Not selectable"
    assert plan.blocking_errors == ["Upgrade requires an existing Tails installation on the selected target."]


def test_upgrade_plan_preserves_existing_tails_target() -> None:
    plan = plan_operation(OperationKind.UPGRADE, source(), device(has_tails=True))

    assert plan.would_write is True
    assert plan.action_label == "Upgrade"
    assert plan.status_message == (
        "Existing Tails installation detected. Upgrade preserves existing Persistent Storage if present."
    )


def test_plan_warns_for_non_removable_target() -> None:
    plan = plan_operation(OperationKind.INSTALL, source(), device(removable=False))

    assert plan.would_write is True
    assert plan.warnings == ["target is not reported as removable; verify that this is intentional"]


def test_install_confirmation_copy_is_device_neutral() -> None:
    plan = plan_operation(OperationKind.INSTALL, source(), device())

    assert plan.confirmation_title == "Confirm installation"
    assert "Install Tails to /dev/sdb from tails.img?" in plan.confirmation_message
    assert "All data on the selected device will be lost." in plan.confirmation_message
    assert "USB stick" not in plan.confirmation_message


def test_reinstall_confirmation_mentions_persistent_storage_without_usb_wording() -> None:
    plan = plan_operation(OperationKind.INSTALL, source(), device(has_tails=True))

    assert plan.confirmation_title == "Confirm reinstallation"
    assert "Reinstall Tails on /dev/sdb from tails.img?" in plan.confirmation_message
    assert "Persistent Storage" in plan.confirmation_message
    assert "selected device" in plan.confirmation_message
    assert "USB stick" not in plan.confirmation_message


def test_upgrade_confirmation_preserves_persistent_storage() -> None:
    plan = plan_operation(OperationKind.UPGRADE, source(), device(has_tails=True))

    assert plan.confirmation_title == "Confirm upgrade"
    assert "Upgrade /dev/sdb from tails.img?" in plan.confirmation_message
    assert "Upgrade while preserving existing Persistent Storage if present" in plan.confirmation_message
    assert "preserving existing Persistent Storage if present" in plan.data_impact_summary


def test_internal_target_confirmation_includes_internal_label_and_warning() -> None:
    plan = plan_operation(OperationKind.INSTALL, source(), device(removable=False))

    assert "internal/non-removable" in plan.target_label
    assert "Warnings:" in plan.confirmation_message
    assert "not reported as removable" in plan.confirmation_message
