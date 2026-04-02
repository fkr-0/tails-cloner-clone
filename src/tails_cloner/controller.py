from __future__ import annotations

from concurrent.futures import Executor, ThreadPoolExecutor

from tails_cloner.models import AppState, VersionAssets


class ApplicationController:
    def __init__(
        self,
        state: AppState,
        version_service,
        device_service,
        clone_service,
        executor: Executor | None = None,
    ) -> None:
        self.state = state
        self.version_service = version_service
        self.device_service = device_service
        self.clone_service = clone_service
        self.executor = executor or ThreadPoolExecutor(max_workers=4)

    def startup(self) -> None:
        self.executor.submit(self.refresh_versions)
        self.executor.submit(self.refresh_devices)

    def shutdown(self) -> None:
        shutdown = getattr(self.executor, "shutdown", None)
        if shutdown is not None:
            shutdown(wait=False)

    def refresh_versions(self) -> None:
        self.state.versions_loading = True
        self.state.status_message = "Loading remote Tails versions…"
        try:
            versions = self.version_service.fetch_versions()
            self.state.available_versions = versions
            if versions:
                self.apply_version_selection(versions[0])
                self.state.status_message = f"Loaded {len(versions)} remote Tails versions."
            else:
                self.state.selected_version = ""
                self.state.selected_iso_url = ""
                self.state.selected_image_url = ""
                self.state.selected_signature_url = ""
                self.state.selected_checksum_url = ""
                self.state.status_message = "No remote Tails versions were found."
        except Exception as error:  # noqa: BLE001 - surfaced in UI state
            self.state.status_message = f"Version refresh failed: {error}"
        finally:
            self.state.versions_loading = False

    def refresh_devices(self) -> None:
        self.state.devices_loading = True
        self.state.status_message = "Scanning removable devices…"
        try:
            self.state.devices = self.device_service.list_removable_devices()
            if self.state.devices:
                self.state.status_message = f"Found {len(self.state.devices)} removable device(s)."
            else:
                self.state.status_message = "No removable devices detected."
        except Exception as error:  # noqa: BLE001 - surfaced in UI state
            self.state.status_message = f"Device scan failed: {error}"
        finally:
            self.state.devices_loading = False

    def select_version(self, version: str) -> None:
        for entry in self.state.available_versions:
            if entry.version == version:
                self.apply_version_selection(entry)
                self.state.status_message = f"Selected remote Tails version {version}."
                return
        raise ValueError(f"Unknown version: {version}")

    def apply_version_selection(self, entry: VersionAssets) -> None:
        self.state.selected_version = entry.version
        self.state.selected_iso_url = entry.iso_url
        self.state.selected_image_url = entry.img_url
        self.state.selected_signature_url = entry.sig_url
        self.state.selected_checksum_url = entry.sha256_url

    def clone_selected_image(self, image_path: str, device_path: str) -> None:
        self.state.status_message = f"Cloning {image_path} to {device_path}…"

        def on_progress(message: str) -> None:
            self.state.last_clone_progress = message
            self.state.status_message = f"Cloning… {message}"

        self.clone_service.clone_image(image_path=image_path, device_path=device_path, progress_callback=on_progress)
        self.state.status_message = "Clone completed successfully."
