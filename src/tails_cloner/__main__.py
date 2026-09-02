from __future__ import annotations

import os
import sys
from collections.abc import Sequence

from tails_cloner import __version__
from tails_cloner.cli import main as cli_main
from tails_cloner.cli_events import CliError, CommandResult, EventSink, ExitCode, OutputMode
from tails_cloner.config import DEFAULT_REMOTE_INDEX_URL
from tails_cloner.runtime import build_application_controller


def _run_gui() -> int:
    """Launch the GUI with the same stable JSONL event vocabulary as the CLI."""
    sink = EventSink(command="gui", output_mode=OutputMode.JSONL)
    sink.start(application_version=__version__, mode="gui")
    remote_index_url = os.environ.get("TAILS_REMOTE_INDEX_URL", DEFAULT_REMOTE_INDEX_URL)
    controller = None
    try:
        from tails_cloner.app import TailsClonerApp

        controller = build_application_controller(remote_index_url=remote_index_url)
        app = TailsClonerApp(
            controller=controller,
            remote_index_url=remote_index_url,
            event_sink=sink,
        )
        app.mainloop()
    except Exception as error:  # noqa: BLE001 - stable launcher failure event
        sink.fail(
            CliError(
                f"GUI failed: {error}",
                exit_code=ExitCode.INTERNAL_ERROR,
                category="gui_failure",
            )
        )
        if controller is not None:
            controller.shutdown()
        return int(ExitCode.INTERNAL_ERROR)
    assert controller is not None
    controller.shutdown()
    sink.complete(
        CommandResult(
            payload={
                "activity": "gui_session",
                "status": "closed",
                "application_version": __version__,
            }
        )
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    effective_argv = list(sys.argv[1:] if argv is None else argv)
    if effective_argv:
        return cli_main(effective_argv)
    return _run_gui()


if __name__ == "__main__":
    raise SystemExit(main())
