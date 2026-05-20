from __future__ import annotations

from concurrent.futures import Executor, ThreadPoolExecutor
from pathlib import Path

from tails_cloner.models import AppState, BlockDevice, SourceMode, VersionAssets
from tails_cloner.planner import OperationKind, OperationPlan, OperationSource, plan_operation
from tails_cloner.source import AttachedLiveSystemSource, get_parent_disk_path, is_running_tails, get_running_tails_version, get_running_tails_device


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
        elif mode == SourceMode.ATTACHED:
            if self.state.attached_live_source_device:
                self.state.status_message = (
                    f"Using attached Tails live source {self.state.attached_live_source_version or 'Unknown'} "
                    f"from {self.state.attached_live_source_device}."
                )
            else:
                self.state.status_message = "Choose and validate an attached Tails live source."
        elif mode == SourceMode.LOCAL:
            self.state.status_message = "Using local image file."
        elif mode == SourceMode.REMOTE:
            self.state.status_message = "Using remote downloaded version."

    def set_attached_live_source(self, device_path: str, mount_point: str | Path) -> AttachedLiveSystemSource:
        """Register an attached Tails live medium as an explicit source.

        This source is separate from the OS running the app. It supports future
        any-Linux workflows where a live Tails USB is mounted as the source and
        another existing Tails device is upgraded as the target.
        """
        source = AttachedLiveSystemSource(device_path=device_path, mount_point=Path(mount_point))
        source.validate()
        self.state.attached_live_source_device = source.device_path
        self.state.attached_live_source_mount = str(source.mount_point)
        self.state.attached_live_source_version = source.version or "Unknown"
        self.state.source_mode = SourceMode.ATTACHED
        self.state.status_message = (
            f"Using attached Tails live source {self.state.attached_live_source_version} "
            f"from {self.state.attached_live_source_device}."
        )
        return source

    def clear_attached_live_source(self) -> None:
        """Clear the attached live source selection."""
        self.state.attached_live_source_device = ""
        self.state.attached_live_source_mount = ""
        self.state.attached_live_source_version = ""
        self.state.status_message = "Attached live source cleared."

    def target_is_attached_live_source(self, target_path: str) -> bool:
        """Return true when the target is the attached live source disk or partition."""
        if not self.state.attached_live_source_device:
            return False
        source_parent = get_parent_disk_path(self.state.attached_live_source_device)
        target_parent = get_parent_disk_path(target_path)
        return source_parent == target_parent

    def target_is_running_system_device(self, target_path: str) -> bool:
        """Return true when the target is the disk currently running Tails."""
        if not self.state.running_tails_device:
            return False
        running_parent = get_parent_disk_path(self.state.running_tails_device)
        target_parent = get_parent_disk_path(target_path)
        return running_parent == target_parent

    def annotate_device_selection_state(self) -> None:
        """Mark source/running devices as visible but not selectable targets."""
        for device in self.state.devices:
            device.is_running_system_device = self.target_is_running_system_device(device.path)
            device.is_attached_source_device = self.target_is_attached_live_source(device.path)
            if device.is_running_system_device:
                device.disabled_reason = "This is the device currently running Tails. It cannot be selected as a target."
            elif device.is_attached_source_device:
                device.disabled_reason = "This is the attached Tails source device. It cannot be selected as a target."
            else:
                device.disabled_reason = ""

    def _find_target_device(self, device_path: str) -> BlockDevice:
        target_parent = get_parent_disk_path(device_path)
        if not self.state.devices:
            self.refresh_devices()
        self.annotate_device_selection_state()
        for device in self.state.devices:
            if device.path == device_path or device.path == target_parent:
                return device
        raise RuntimeError(f"Target device is not present in the current device list: {device_path}")

    def _operation_source(self, image_path: str | None = None) -> OperationSource:
        if self.state.source_mode == SourceMode.RUNNING:
            return OperationSource(
                type="running_source",
                device=self.state.running_tails_device,
                version=self.state.running_tails_version,
            )
        if self.state.source_mode == SourceMode.ATTACHED:
            return OperationSource(
                type="attached_source",
                device=self.state.attached_live_source_device,
                version=self.state.attached_live_source_version,
            )
        if self.state.source_mode == SourceMode.REMOTE:
            return OperationSource(
                type="remote_image",
                path=image_path or self.state.selected_image_url,
                version=self.state.selected_version,
            )
        return OperationSource(type="image", path=image_path or "")

    def plan_target_operation(self, operation: OperationKind, device_path: str, image_path: str | None = None) -> OperationPlan:
        target = self._find_target_device(device_path)
        return plan_operation(
            operation=operation,
            source=self._operation_source(image_path),
            target=target,
        )

    def _require_valid_target_plan(self, operation: OperationKind, device_path: str, image_path: str | None = None) -> OperationPlan:
        plan = self.plan_target_operation(operation, device_path, image_path)
        if plan.blocking_errors:
            raise RuntimeError("\n".join(plan.blocking_errors))
        return plan

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
        self.state.status_message = "Scanning devices…"
        try:
            self.state.devices = self.device_service.list_devices()
            self.annotate_device_selection_state()
            if self.state.devices:
                self.state.status_message = f"Found {len(self.state.devices)} device(s)."
            else:
                self.state.status_message = "No devices detected."
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

    def _resolve_source_image_path(self, image_path: str | None, operation_name: str) -> str:
        """Resolve the image used for install/reinstall/upgrade operations."""
        actual_image_path = image_path

        if self.state.source_mode == SourceMode.RUNNING and actual_image_path is None:
            from tails_cloner.source import RunningLiveSystemSource
            source = RunningLiveSystemSource()
            iso_path = source.get_iso_path()
            if iso_path and iso_path.exists():
                actual_image_path = str(iso_path)
                self.state.status_message = "Using embedded Tails ISO from running system..."
            else:
                self.state.status_message = "Error: Tails ISO not found in running system."
                raise RuntimeError("Tails ISO not found in running system at /lib/live/mount/medium/live/Tails.iso")

        if actual_image_path is None:
            self.state.status_message = "Error: No image path specified."
            raise ValueError(f"Image path is required when not {operation_name} from running Tails")

        return actual_image_path

    def clone_selected_image(self, image_path: str | None, device_path: str, progress_callback=None) -> None:
        """Install or reinstall an image to the target device with a whole-device write."""
        if self.state.source_mode == SourceMode.ATTACHED:
            raise RuntimeError("Attached live sources are only supported for persistence-preserving upgrades.")
        self._require_valid_target_plan(OperationKind.INSTALL, device_path, image_path)
        actual_image_path = self._resolve_source_image_path(image_path, "installing")
        self.state.status_message = f"Installing {actual_image_path} to {device_path}…"

        def on_progress(message: str) -> None:
            self.state.last_clone_progress = message
            self.state.status_message = f"Installing… {message}"
            if progress_callback:
                progress_callback(message)

        self.clone_service.clone_image(
            image_path=actual_image_path,
            device_path=device_path,
            progress_callback=on_progress,
            post_write_options=self.state.post_write_options,
        )
        self.state.status_message = "Installation completed successfully."

    def upgrade_selected_image(self, image_path: str | None, device_path: str, progress_callback=None) -> None:
        """Upgrade an existing Tails target while preserving Persistent Storage."""
        if self.state.source_mode == SourceMode.ATTACHED:
            self.upgrade_selected_from_attached_live_source(device_path, progress_callback=progress_callback)
            return

        self._require_valid_target_plan(OperationKind.UPGRADE, device_path, image_path)
        actual_image_path = self._resolve_source_image_path(image_path, "upgrading")
        self.state.status_message = f"Upgrading {device_path} from {actual_image_path}; Persistent Storage will be preserved…"

        def on_progress(message: str) -> None:
            self.state.last_clone_progress = message
            self.state.status_message = f"Upgrading… {message}"
            if progress_callback:
                progress_callback(message)

        self.clone_service.upgrade_image(
            image_path=actual_image_path,
            device_path=device_path,
            progress_callback=on_progress,
        )
        self.state.status_message = "Upgrade completed successfully. Persistent Storage preserved."

    def upgrade_selected_from_attached_live_source(self, device_path: str, progress_callback=None) -> None:
        """Upgrade from an attached live source device without rewriting target persistence."""
        if not self.state.attached_live_source_device:
            raise RuntimeError("No attached Tails live source has been selected.")
        self._require_valid_target_plan(OperationKind.UPGRADE, device_path)

        source_device = get_parent_disk_path(self.state.attached_live_source_device)
        self.state.status_message = (
            f"Upgrading {device_path} from attached Tails live source {source_device}; "
            "Persistent Storage will be preserved…"
        )

        def on_progress(message: str) -> None:
            self.state.last_clone_progress = message
            self.state.status_message = f"Upgrading… {message}"
            if progress_callback:
                progress_callback(message)

        self.clone_service.upgrade_from_device(
            source_device=source_device,
            device_path=device_path,
            progress_callback=on_progress,
        )
        self.state.status_message = "Upgrade completed successfully from attached live source. Persistent Storage preserved."
