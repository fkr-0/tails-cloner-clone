from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

from tails_cloner.cli_events import (
    SCHEMA,
    CliError,
    CommandResult,
    EventSink,
    ExitCode,
    OutputMode,
)


def test_json_mode_emits_one_deterministic_completion_envelope() -> None:
    stdout = StringIO()
    stderr = StringIO()
    sink = EventSink(command="status", output_mode=OutputMode.JSON, stdout=stdout, stderr=stderr)
    sink.start(application_version="0.5.4")
    sink.discovery(resource="devices", count=2)
    sink.complete(CommandResult(payload={"ok": True}))
    payload = json.loads(stdout.getvalue())
    assert payload == {
        "command": "status",
        "exit_code": 0,
        "kind": "completion",
        "result": {"ok": True},
        "schema": SCHEMA,
        "schema_version": 1,
        "sequence": 3,
    }
    assert stderr.getvalue() == ""


def test_jsonl_mode_streams_stable_event_kinds_and_bounded_messages() -> None:
    stdout = StringIO()
    sink = EventSink(command="install", output_mode=OutputMode.JSONL, stdout=stdout, stderr=StringIO())
    sink.start()
    sink.warning("x" * 2_000)
    sink.write_boundary("ready", target="/dev/sdz")
    sink.complete(CommandResult(payload={"done": True}))
    events = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert [event["kind"] for event in events] == ["start", "warning", "write_boundary", "completion"]
    assert [event["sequence"] for event in events] == [1, 2, 3, 4]
    assert all(event["schema"] == SCHEMA for event in events)
    assert len(events[1]["message"]) <= 512


def test_log_file_keeps_same_jsonl_schema_in_human_mode(tmp_path: Path) -> None:
    log = tmp_path / "audit.jsonl"
    sink = EventSink(
        command="download",
        output_mode=OutputMode.HUMAN,
        log_file=log,
        stdout=StringIO(),
        stderr=StringIO(),
    )
    sink.start()
    sink.verification("verified", sha256="a" * 64)
    sink.complete(CommandResult(payload={"path": "/tmp/tails.img"}))
    events = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert [event["kind"] for event in events] == ["start", "verification", "completion"]
    assert all(event["schema"] == SCHEMA for event in events)


def test_failure_envelope_has_stable_exit_taxonomy() -> None:
    stdout = StringIO()
    sink = EventSink(command="install", output_mode=OutputMode.JSON, stdout=stdout, stderr=StringIO())
    sink.start()
    sink.fail(
        CliError(
            "target changed",
            exit_code=ExitCode.SAFETY_REFUSAL,
            category="target_identity_mismatch",
        )
    )
    payload = json.loads(stdout.getvalue())
    assert payload["kind"] == "failure"
    assert payload["error"] == {
        "category": "target_identity_mismatch",
        "exit_code": 4,
        "message": "target changed",
    }
