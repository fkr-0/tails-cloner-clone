"""Host-only contracts for failures after a successful whole-image write."""

from __future__ import annotations

import json
from collections.abc import Callable
from io import StringIO
from pathlib import Path
from queue import SimpleQueue
from typing import Any, cast

import pytest

from tails_cloner.app import TailsClonerApp
from tails_cloner.cli_events import EventSink, OutputMode
from tails_cloner.controller import ApplicationController
from tails_cloner.creator import clone_image_to_device
from tails_cloner.post_write import MountState, PostWriteFailure


@pytest.mark.parametrize("failure_step", ["reread", "settle"])
@pytest.mark.parametrize("failure_kind", ["exit", "exception"])
def test_refresh_failure_after_dd_is_typed(tmp_path: Path, failure_step: str, failure_kind: str) -> None:
    image = tmp_path / "tails.iso"
    image.write_bytes(b"synthetic image")
    calls: list[str] = []

    def run(command: list[str], _progress: Callable[[str], None]) -> int:
        step = ("dd" if "dd" in command else
                "reread" if "--rereadpt" in command else "settle")
        calls.append(step)
        if step == failure_step:
            if failure_kind == "exception":
                raise OSError("synthetic tool failure")
            return 3
        return 0

    with pytest.raises(PostWriteFailure) as captured:
        clone_image_to_device(
            image, "/dev/sdz", run_command=run,
            target_preparer=lambda *_args: None,
            post_write_runner=lambda *_args: None,
        )
    error = captured.value
    assert calls[:2] == ["dd", "reread"]
    assert error.write_succeeded is True
    assert error.customization_succeeded is False
    assert error.device_state.startswith("unknown")
    assert error.mount_point == "none"
    assert error.unmount_succeeded is None


@pytest.mark.parametrize("is_upgrade", [False, True])
def test_gui_partial_success_preserves_typed_failure_event(is_upgrade: bool) -> None:
    app = TailsClonerApp.__new__(TailsClonerApp)
    app._ui_events = SimpleQueue()
    output = StringIO()
    app._operator_events = EventSink(
        command="gui", output_mode=OutputMode.JSONL, stdout=output, stderr=StringIO(),
    )

    class Controller:
        state = type("State", (), {"status_message": ""})()

        @staticmethod
        def clone_selected_image(*_args: Any, **_kwargs: Any) -> None:
            raise PostWriteFailure(
                "simulated unmount", MountState(
                    mount_point="/tmp/synthetic-mount",
                    device_state="unknown; mount may remain active",
                    unmount_succeeded=False,
                ),
            )

        upgrade_selected_image = clone_selected_image

    app.controller = cast(ApplicationController, Controller())
    app._run_write_operation("/tmp/tails.img", "/dev/sdz", is_upgrade)
    failure = next(json.loads(line) for line in output.getvalue().splitlines()
                   if json.loads(line)["kind"] == "failure")
    error = failure["error"]
    assert error["exit_code"] == 10
    assert error["category"] == "post_write_failed_after_write"
    assert error["details"]["write_succeeded"] is True
    assert error["details"]["customization_succeeded"] is False
    assert error["details"]["unmount_succeeded"] is False
    assert error["details"]["mount_point"] == "/tmp/synthetic-mount"
    assert error["details"]["operation"] == ("upgrade" if is_upgrade else "install")
