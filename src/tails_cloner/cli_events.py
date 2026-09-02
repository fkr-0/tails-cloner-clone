"""Stable machine-output contract and CLI error taxonomy."""

from __future__ import annotations

import json
import sys
import threading
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from pathlib import Path
from typing import IO, Any

SCHEMA = "tails-cloner-cli/v1"
SCHEMA_VERSION = 1
MAX_EVENT_MESSAGE_CHARS = 512


class ExitCode(IntEnum):
    SUCCESS = 0
    USAGE = 2
    INVALID_INPUT = 3
    SAFETY_REFUSAL = 4
    VERIFICATION_FAILED = 5
    DOWNLOAD_FAILED = 6
    INSTALL_FAILED = 7
    UPGRADE_FAILED = 8
    INTERNAL_ERROR = 9


class OutputMode(StrEnum):
    HUMAN = "human"
    JSON = "json"
    JSONL = "jsonl"


class CliError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        exit_code: ExitCode,
        category: str,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.category = category
        self.details = details or {}


@dataclass(frozen=True, slots=True)
class CommandResult:
    payload: dict[str, object]
    human_text: str = ""
    exit_code: ExitCode = ExitCode.SUCCESS


@dataclass(slots=True)
class EventSink:
    command: str
    output_mode: OutputMode
    log_file: Path | None = None
    stdout: IO[str] = field(default_factory=lambda: sys.stdout)
    stderr: IO[str] = field(default_factory=lambda: sys.stderr)
    _sequence: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def _base(self, kind: str) -> dict[str, object]:
        self._sequence += 1
        return {
            "schema": SCHEMA,
            "schema_version": SCHEMA_VERSION,
            "sequence": self._sequence,
            "kind": kind,
            "command": self.command,
        }

    @staticmethod
    def _bounded_message(message: str) -> str:
        normalized = " ".join(message.replace("\x00", "").split())
        if len(normalized) <= MAX_EVENT_MESSAGE_CHARS:
            return normalized
        return normalized[: MAX_EVENT_MESSAGE_CHARS - 1] + "…"

    def _write_log(self, event: dict[str, object]) -> None:
        if self.log_file is None:
            return
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        with self.log_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")

    def emit(self, kind: str, **fields: object) -> dict[str, object]:
        with self._lock:
            event = self._base(kind)
            if "message" in fields and isinstance(fields["message"], str):
                fields["message"] = self._bounded_message(fields["message"])
            event.update(fields)
            self._write_log(event)
            if self.output_mode == OutputMode.JSONL:
                print(
                    json.dumps(event, sort_keys=True, separators=(",", ":")),
                    file=self.stdout,
                    flush=True,
                )
            elif self.output_mode == OutputMode.HUMAN and kind in {
                "warning",
                "progress",
                "verification",
                "write_boundary",
            }:
                message = event.get("message")
                if isinstance(message, str) and message:
                    print(message, file=self.stderr, flush=True)
            return event

    def start(self, **fields: object) -> None:
        self.emit("start", **fields)

    def discovery(self, **fields: object) -> None:
        self.emit("discovery", **fields)

    def resolution(self, **fields: object) -> None:
        self.emit("resolution", **fields)

    def verification(self, message: str, **fields: object) -> None:
        self.emit("verification", message=message, **fields)

    def warning(self, message: str, **fields: object) -> None:
        self.emit("warning", message=message, **fields)

    def progress(self, message: str, **fields: object) -> None:
        self.emit("progress", message=message, **fields)

    def write_boundary(self, message: str, **fields: object) -> None:
        self.emit("write_boundary", message=message, **fields)

    def complete(self, result: CommandResult) -> None:
        event = self.emit("completion", exit_code=int(result.exit_code), result=result.payload)
        if self.output_mode == OutputMode.JSON:
            print(json.dumps(event, indent=2, sort_keys=True), file=self.stdout)
        elif self.output_mode == OutputMode.HUMAN:
            if result.human_text:
                print(result.human_text, file=self.stdout)
            else:
                print(json.dumps(result.payload, indent=2, sort_keys=True), file=self.stdout)

    def fail(self, error: CliError) -> None:
        payload: dict[str, Any] = {
            "category": error.category,
            "exit_code": int(error.exit_code),
            "message": self._bounded_message(str(error)),
        }
        if error.details:
            payload["details"] = error.details
        event = self.emit("failure", error=payload)
        if self.output_mode == OutputMode.JSON:
            print(json.dumps(event, indent=2, sort_keys=True), file=self.stdout)
        elif self.output_mode == OutputMode.HUMAN:
            print(f"error: {error}", file=self.stderr)
