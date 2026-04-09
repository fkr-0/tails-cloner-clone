from __future__ import annotations

from concurrent.futures import Executor, ThreadPoolExecutor

from tails_cloner.models import AppState, SourceMode, VersionAssets
from tails_cloner.source import is_running_tails, get_running_tails_version, get_running_tails_device


"""Application controller for Tails Cloner.

The controller manages the application state and coordinates between
different services (version fetching, device detection, clone operations).
It runs expensive operations asynchronously in a thread pool.
"""


class ApplicationController:
    """Controller for the Tails Cloner application.

    Manages application state and coordinates between services.
    Runs expensive operations asynchronously in a thread pool.
    """

    def __init__(
        self,
        state: AppState,
        version_service,
        device_service,
        clone_service,
        executor: Executor | None = None,
    ) -> None:
        """Initialize the controller.

        Args:
            state: Application state instance
            version_service: Service for fetching Tails versions
            device_service: Service for listing available devices
            clone_service: Service for cloning images to devices
            executor: Thread pool for async operations (defaults to ThreadPoolExecutor)
        """
        self.state = state
        self.version_service = version_service
        self.device_service = device_service
        self.clone_service = clone_service
        self.executor = executor or ThreadPoolExecutor(max_workers=4)

    def startup(self) -> None:
        """Initialize the application.

        Detects if running from Tails and starts async refresh of
        versions and devices.
        """
        # Check if we're running from Tails
        self._detect_running_tails()
        self.executor.submit(self.refresh_versions)
        self.executor.submit(self.refresh_devices)

    def _detect_running_tails(self) -> None:
        """Detect if running from Tails and update state accordingly."""
        running = is_running_tails()
        self.state.running_tails_available = running
        if running:
            self.state.running_tails_version = get_running_tails_version() or "Unknown"
            self.state.running_tails_device = get_running_tails_device() or ""
            self.state.source_mode = SourceMode.RUNNING
            self.state.status_message = f"Running from Tails {self.state.running_tails_version}. Ready to clone."
        else:
            self.state.source_mode = SourceMode.LOCAL
            self.state.status_message = "Not running from Tails. Use a downloaded image."

    def set_source_mode(self, mode: SourceMode) -> None:
        """Change the source mode."""
        if mode == SourceMode.RUNNING and not self.state.running_tails_available:
            raise ValueError("Cannot use running Tails mode: not running from Tails")
        self.state.source_mode = mode
        if mode == SourceMode.RUNNING:
            self.state.status_message = f"Cloning from running Tails {self.state.running_tails_version}."
        elif mode == SourceMode.LOCAL:
            self.state.status_message = "Using local image file."
        elif mode == SourceMode.REMOTE:
            self.state.status_message = "Using remote downloaded version."

    def shutdown(self) -> None:
        shutdown = getattr(self.executor, "shutdown", None)
        if shutdown is not None:
            shutdown(wait=False)

    def refresh_versions(self) -> None:
        """Refresh the list of available Tails versions from remote index."""
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
        """Scan for available block devices."""
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
        """Select a specific Tails version from available versions."""
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

    def clone_selected_image(self, image_path: str | None, device_path: str, progress_callback=None) -> None:
        """Clone an image to the target device.

        If image_path is None and source_mode is RUNNING, uses the running Tails ISO.
        """
        actual_image_path = image_path

        # If cloning from running Tails, use the embedded ISO
        if self.state.source_mode == SourceMode.RUNNING and actual_image_path is None:
            from tails_cloner.source import RunningLiveSystemSource
            source = RunningLiveSystemSource()
            iso_path = source.get_iso_path()
            if iso_path and iso_path.exists():
                actual_image_path = str(iso_path)
                self.state.status_message = f"Using embedded Tails ISO from running system..."
            else:
                self.state.status_message = "Error: Tails ISO not found in running system."
                raise RuntimeError("Tails ISO not found in running system at /lib/live/mount/medium/live/Tails.iso")

        if actual_image_path is None:
            self.state.status_message = "Error: No image path specified."
            raise ValueError("Image path is required when not cloning from running Tails")

        self.state.status_message = f"Cloning {actual_image_path} to {device_path}…"

        def on_progress(message: str) -> None:
            self.state.last_clone_progress = message
            self.state.status_message = f"Cloning… {message}"
            if progress_callback:
                progress_callback(message)

        self.clone_service.clone_image(image_path=actual_image_path, device_path=device_path, progress_callback=on_progress)
        self.state.status_message = "Clone completed successfully."
