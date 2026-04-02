from __future__ import annotations

import argparse
import os
from collections.abc import Sequence

from tails_cloner.app import TailsClonerApp
from tails_cloner.config import DEFAULT_REMOTE_INDEX_URL
from tails_cloner.controller import ApplicationController
from tails_cloner.creator import clone_image_to_device
from tails_cloner.devices import DeviceService
from tails_cloner.models import AppState
from tails_cloner.remote_index import RemoteVersionIndex


class VersionService:
    def __init__(self, index: RemoteVersionIndex) -> None:
        self._index = index

    def fetch_versions(self):
        return self._index.fetch_versions()


class CloneService:
    def clone_image(self, image_path: str, device_path: str, progress_callback) -> None:
        clone_image_to_device(image_path=image_path, device_path=device_path, progress_callback=progress_callback)



def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Standalone GUI app for cloning Tails images onto removable devices.")
    parser.add_argument(
        "--remote-index-url",
        default=os.environ.get("TAILS_REMOTE_INDEX_URL", DEFAULT_REMOTE_INDEX_URL),
        help="Remote directory listing used to enumerate Tails versions asynchronously on startup.",
    )
    return parser



def main(argv: Sequence[str] | None = None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)

    controller = ApplicationController(
        state=AppState(),
        version_service=VersionService(RemoteVersionIndex(base_url=args.remote_index_url)),
        device_service=DeviceService(),
        clone_service=CloneService(),
    )
    app = TailsClonerApp(controller=controller, remote_index_url=args.remote_index_url)
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
