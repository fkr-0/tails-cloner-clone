"""Concrete application services shared by GUI and CLI entry points."""

from __future__ import annotations

from collections.abc import Callable

from tails_cloner.config import DEFAULT_REMOTE_INDEX_URL
from tails_cloner.controller import ApplicationController
from tails_cloner.creator import clone_image_to_device
from tails_cloner.devices import DeviceService
from tails_cloner.models import AppState, PostWriteOptions, VersionAssets
from tails_cloner.remote_index import RemoteVersionIndex
from tails_cloner.upgrader import (
    upgrade_tails_system_partition,
    upgrade_tails_system_partition_from_device,
)


class VersionService:
    def __init__(self, index: RemoteVersionIndex) -> None:
        self._index = index

    def fetch_versions(self) -> list[VersionAssets]:
        return self._index.fetch_versions()


class CloneService:
    def clone_image(
        self,
        image_path: str,
        device_path: str,
        progress_callback: Callable[[str], None] | None = None,
        post_write_options: PostWriteOptions | None = None,
    ) -> None:
        clone_image_to_device(
            image_path=image_path,
            device_path=device_path,
            progress_callback=progress_callback,
            post_write_options=post_write_options,
        )

    def upgrade_image(
        self,
        image_path: str,
        device_path: str,
        progress_callback: Callable[[str], None] | None = None,
    ) -> None:
        upgrade_tails_system_partition(
            image_path=image_path,
            target_device=device_path,
            progress_callback=progress_callback,
        )

    def upgrade_from_device(
        self,
        source_device: str,
        device_path: str,
        progress_callback: Callable[[str], None] | None = None,
    ) -> None:
        upgrade_tails_system_partition_from_device(
            source_device=source_device,
            target_device=device_path,
            progress_callback=progress_callback,
        )


def build_application_controller(
    *,
    remote_index_url: str = DEFAULT_REMOTE_INDEX_URL,
    post_write_options: PostWriteOptions | None = None,
) -> ApplicationController:
    return ApplicationController(
        state=AppState(post_write_options=post_write_options or PostWriteOptions()),
        version_service=VersionService(RemoteVersionIndex(base_url=remote_index_url)),
        device_service=DeviceService(),
        clone_service=CloneService(),
    )
