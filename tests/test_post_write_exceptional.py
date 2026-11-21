"""Host-only exceptional outcomes after successful whole-image writing."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from tails_cloner.models import PostWriteOptions
from tails_cloner.post_write import PostWriteFailure, apply_post_write_options


@pytest.mark.parametrize("interruption", [KeyboardInterrupt, SystemExit])
def test_interrupted_post_write_is_typed_partial_success(interruption: type[BaseException]) -> None:
    def interrupt() -> None:
        raise interruption()

    with pytest.raises(PostWriteFailure) as caught:
        apply_post_write_options(
            "/dev/sdz", PostWriteOptions(enabled=True, sync_device=True),
            sync_runner=interrupt,
        )
    assert caught.value.write_succeeded is True
    assert caught.value.customization_succeeded is False
    assert caught.value.device_state.startswith("unknown")


def test_audit_log_failure_preserves_image_write_success(tmp_path: Path) -> None:
    impossible_log = tmp_path / "directory"
    impossible_log.mkdir()
    with pytest.raises(PostWriteFailure) as caught:
        apply_post_write_options(
            "/dev/sdz",
            PostWriteOptions(enabled=True, sync_device=False, log_file_path=str(impossible_log)),
            timestamp_provider=lambda: datetime(2026, 11, 21, tzinfo=UTC),
        )
    assert caught.value.write_succeeded is True
    assert caught.value.customization_succeeded is False
    assert caught.value.device_state.startswith("unknown")
