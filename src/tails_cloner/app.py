from __future__ import annotations

import tkinter as tk
import sys
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from urllib.request import urlopen

from tails_cloner.config import BRANDING, FONT_SIZE_LARGE, FONT_SIZE_MEDIUM, MIN_WINDOW_SIZE, REFRESH_INTERVAL_MS, WINDOW_SIZE
from tails_cloner.controller import ApplicationController
from tails_cloner.devices import MIN_INSTALLATION_SIZE_GB
from tails_cloner.models import BlockDevice, SourceMode
from tails_cloner.source import get_parent_disk_path


class TailsClonerApp(tk.Tk):
    def __init__(self, controller: ApplicationController, remote_index_url: str) -> None:
        super().__init__(className="tails-cloner-clone")
        self.controller = controller
        self.remote_index_url = remote_index_url
        self.wm_class("tails-cloner-clone", "tails-cloner-clone")
        self.title(BRANDING.window_title)
        self.geometry(WINDOW_SIZE)
        self.minsize(*MIN_WINDOW_SIZE)

        # Set larger default font sizes
        self.option_add("*Font", f"TkDefaultFont {FONT_SIZE_MEDIUM}")

        self.status_var = tk.StringVar(value=self.controller.state.status_message)
        self.remote_url_var = tk.StringVar(value=self.remote_index_url)
        self.selected_version_var = tk.StringVar()
        self.selected_iso_url_var = tk.StringVar()
        self.selected_image_url_var = tk.StringVar()
        self.selected_signature_url_var = tk.StringVar()
        self.image_path_var = tk.StringVar()
        self.source_status_var = tk.StringVar(value="")
        self.device_var = tk.StringVar()
        # Source mode variables
        self.source_mode_var = tk.StringVar(value="local")
        self.running_tails_version_var = tk.StringVar()
        self.running_tails_device_var = tk.StringVar()
        self.upgrade_plan_var = tk.StringVar(value="Source: not selected\nTarget: not selected\nAction: not selected")
        self._device_labels: dict[str, str] = {}
        self._last_versions_snapshot: tuple[str, ...] = ()
        self._last_devices_snapshot: tuple[str, ...] = ()
        self._last_selected_version: str = ""
        self._last_status: str = ""
        self._versions_busy_text = ""
        self._devices_busy_text = ""
        self.dark_mode_var = tk.BooleanVar(value=True)

        self._set_window_icon()
        self._configure_theme()
        self._build_ui()
        self.controller.startup()
        self.after(REFRESH_INTERVAL_MS, self._sync_state)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=3)
        self.columnconfigure(1, weight=2)
        self.rowconfigure(2, weight=1)

        # Keyboard bindings
        self.bind("<Control-r>", lambda e: self.controller.executor.submit(self.controller.refresh_versions))
        self.bind("<Control-d>", lambda e: self.controller.executor.submit(self.controller.refresh_devices))
        self.bind("<Control-q>", lambda e: self._on_close())
        self.bind("<Escape>", lambda e: self._on_close())

        header = ttk.Frame(self, padding=16)
        header.grid(row=0, column=0, columnspan=2, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text=BRANDING.distribution + " Cloner", font=("TkDefaultFont", 22, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(
            header,
            text="Refreshed standalone installer architecture inspired by the original Tails installer layout.",
            foreground="#555555",
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Label(header, textvariable=self.remote_url_var, foreground="#666666").grid(row=2, column=0, sticky="w", pady=(4, 0))
        ttk.Button(header, text="Refresh Versions (Ctrl+R)", command=lambda: self.controller.executor.submit(self.controller.refresh_versions)).grid(row=0, column=1, padx=(12, 0))
        ttk.Button(header, text="Refresh Devices (Ctrl+D)", command=lambda: self.controller.executor.submit(self.controller.refresh_devices)).grid(row=0, column=2, padx=(8, 0))
        self.theme_button = ttk.Button(header, text="🌙", width=3, command=self._on_toggle_dark_mode)
        self.theme_button.grid(row=0, column=3, padx=(8, 0))
        ttk.Button(header, text="✕", width=3, command=self._on_close).grid(row=0, column=4, padx=(8, 0))

        # Source selection panel
        source_frame = ttk.LabelFrame(self, text="Source", padding=12)
        source_frame.grid(row=1, column=0, sticky="nsew", padx=(16, 8), pady=(0, 8))
        source_frame.columnconfigure(0, weight=1)

        # Remote source option
        self.source_remote_frame = ttk.Frame(source_frame)
        self.source_remote_frame.grid(row=0, column=0, sticky="ew")
        self.source_remote_frame.columnconfigure(0, weight=1)
        self.source_remote_radio = ttk.Radiobutton(
            self.source_remote_frame,
            text="Use a remote Tails version (download first)",
            value="remote",
            variable=self.source_mode_var,
            command=self._on_source_mode_changed,
        )
        self.source_remote_radio.grid(row=0, column=0, sticky="w")
        self.download_button = ttk.Button(
            self.source_remote_frame,
            text="Download selected IMG to local cache",
            command=lambda: self.controller.executor.submit(self._download_selected_remote_image),
        )
        self.download_button.grid(row=1, column=0, sticky="w", padx=(20, 0), pady=(4, 0))
        ttk.Label(
            self.source_remote_frame,
            textvariable=self.source_status_var,
            foreground="#666666",
            wraplength=520,
        ).grid(row=2, column=0, sticky="w", padx=(20, 0), pady=(2, 0))

        # Running Tails source option
        self.running_tails_frame = ttk.Frame(source_frame)
        self.running_tails_frame.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        self.running_tails_frame.columnconfigure(1, weight=1)

        self.source_running_radio = ttk.Radiobutton(
            self.running_tails_frame,
            text="Clone the current Tails",
            value="running",
            variable=self.source_mode_var,
            command=self._on_source_mode_changed
        )
        self.source_running_radio.grid(row=0, column=0, sticky="w", columnspan=2)
        ttk.Label(self.running_tails_frame, text="Version:", foreground="#666666").grid(row=1, column=0, sticky="w", padx=(20, 4))
        ttk.Label(self.running_tails_frame, textvariable=self.running_tails_version_var, foreground="#333333").grid(row=1, column=1, sticky="w")
        ttk.Label(self.running_tails_frame, text="Device:", foreground="#666666").grid(row=2, column=0, sticky="w", padx=(20, 4))
        ttk.Label(self.running_tails_frame, textvariable=self.running_tails_device_var, foreground="#333333").grid(row=2, column=1, sticky="w")
        ttk.Label(
            self.running_tails_frame,
            text="Boot newer Tails from USB, then upgrade another target device.",
            foreground="#555555",
        ).grid(row=3, column=0, columnspan=2, sticky="w", padx=(20, 0), pady=(4, 0))

        # Local file source option
        self.source_local_frame = ttk.Frame(source_frame)
        self.source_local_frame.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        self.source_local_frame.columnconfigure(1, weight=1)
        self.source_local_radio = ttk.Radiobutton(
            self.source_local_frame,
            text="Use a local image file",
            value="local",
            variable=self.source_mode_var,
            command=self._on_source_mode_changed
        )
        self.source_local_radio.grid(row=0, column=0, columnspan=3, sticky="w")
        self.image_entry = ttk.Entry(self.source_local_frame, textvariable=self.image_path_var)
        self.image_entry.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0), padx=(20, 0))
        self.browse_button = ttk.Button(self.source_local_frame, text="Browse...", command=self._browse_image)
        self.browse_button.grid(row=1, column=2, padx=(8, 0), pady=(4, 0))

        # Remote versions panel (hidden when using running Tails or local file)
        left = ttk.LabelFrame(self, text="Remote versions", padding=12)
        left.grid(row=2, column=0, sticky="nsew", padx=(16, 8), pady=(0, 8))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=1)

        version_toolbar = ttk.Frame(left)
        version_toolbar.grid(row=0, column=0, sticky="ew")
        version_toolbar.columnconfigure(0, weight=1)
        self.version_status_label = ttk.Label(version_toolbar, text="Idle", foreground="#666666")
        self.version_status_label.grid(row=0, column=0, sticky="w")

        self.versions_list = tk.Listbox(left, exportselection=False, activestyle="none", font=("TkDefaultFont", FONT_SIZE_MEDIUM))
        self.versions_list.grid(row=1, column=0, sticky="nsew")
        self.versions_list.bind("<<ListboxSelect>>", self._on_version_selected)
        # Allow keyboard navigation in versions list
        self.versions_list.bind("<KeyRelease-Up>", self._on_version_key_nav)
        self.versions_list.bind("<KeyRelease-Down>", self._on_version_key_nav)

        details = ttk.Frame(left)
        details.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        details.columnconfigure(1, weight=1)
        self._add_readonly_row(details, 0, "Selected version", self.selected_version_var)
        self._add_readonly_row(details, 1, "Suggested ISO URL", self.selected_iso_url_var)
        self._add_readonly_row(details, 2, "Suggested IMG URL", self.selected_image_url_var)
        self._add_readonly_row(details, 3, "Signature URL", self.selected_signature_url_var)

        right = ttk.LabelFrame(self, text="Write image to device", padding=12)
        right.grid(row=2, column=1, sticky="nsew", padx=(8, 16), pady=(0, 8))
        right.columnconfigure(1, weight=1)

        ttk.Label(right, text="Target device").grid(row=0, column=0, sticky="w")
        self.device_combo = ttk.Combobox(right, textvariable=self.device_var, state="readonly")
        self.device_combo.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(4, 0))
        self.device_combo.bind("<<ComboboxSelected>>", self._on_device_selected)

        self.device_status_label = ttk.Label(right, text="Idle", foreground="#666666", wraplength=320)
        self.device_status_label.grid(row=2, column=0, columnspan=3, sticky="w", pady=(12, 0))

        self.device_warning_label = ttk.Label(right, text="", foreground="#a63636", wraplength=320)
        self.device_warning_label.grid(row=3, column=0, columnspan=3, sticky="w", pady=(4, 0))

        self.upgrade_plan_label = ttk.Label(
            right,
            textvariable=self.upgrade_plan_var,
            foreground="#1f4d8f",
            wraplength=320,
            justify="left",
        )
        self.upgrade_plan_label.grid(row=4, column=0, columnspan=3, sticky="w", pady=(8, 0))

        # Progress bar for clone operation
        self.progress_frame = ttk.Frame(right)
        self.progress_frame.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(12, 0))
        self.progress_bar = ttk.Progressbar(self.progress_frame, mode="indeterminate")
        self.progress_bar.grid(row=0, column=0, sticky="ew")
        self.progress_label = ttk.Label(self.progress_frame, text="", foreground="#666666")
        self.progress_label.grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.progress_frame.grid_remove()  # Hidden by default

        self.clone_button = ttk.Button(right, text="Install", command=self._confirm_and_clone)
        self.clone_button.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(20, 0))
        # Make clone button the default (activated by Enter)
        self.clone_button.bind("<Return>", lambda e: self._confirm_and_clone())
        warning_text = (
            "This tool writes directly to the selected block device with dd. "
            "Treat it like a loaded weapon and verify the target path every time.\n\n"
            "All data on the target device will be permanently lost."
        )
        ttk.Label(right, text=warning_text, wraplength=340, justify="left", foreground="#7a1f1f").grid(row=7, column=0, columnspan=3, sticky="w", pady=(12, 0))

        ttk.Label(self, textvariable=self.status_var, relief="sunken", anchor="w", padding=(12, 8)).grid(
            row=3,
            column=0,
            columnspan=2,
            sticky="ew",
            padx=16,
            pady=(0, 16),
        )

        # Set initial focus to device combo for quick access
        self.device_combo.focus_set()

    def _configure_theme(self) -> None:
        self._apply_theme(self.dark_mode_var.get())

    def _apply_theme(self, dark_mode: bool) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        if dark_mode:
            bg = "#1a1d21"
            fg = "#e8eaed"
            entry_bg = "#262b31"
            listbox_bg = "#252a31"
            select_bg = "#3b6ea8"
            active_bg = "#242a31"
        else:
            bg = "#f0f0f0"
            fg = "#111111"
            entry_bg = "#ffffff"
            listbox_bg = "#ffffff"
            select_bg = "#2f6db5"
            active_bg = "#e3e8ee"

        self.configure(bg=bg)
        style.configure("TLabel", background=bg, foreground=fg)
        style.configure("TFrame", background=bg)
        style.configure("TLabelframe", background=bg, foreground=fg)
        style.configure("TLabelframe.Label", background=bg, foreground=fg)
        style.configure("TRadiobutton", background=bg, foreground=fg)
        style.configure("TButton", padding=(10, 6))
        style.configure("TEntry", fieldbackground=entry_bg)
        style.configure("TCombobox", fieldbackground=entry_bg)
        style.map("TRadiobutton", background=[("active", bg)], foreground=[("active", fg)])
        style.map("TButton", background=[("active", active_bg)])

        self.option_add("*Listbox.Background", listbox_bg)
        self.option_add("*Listbox.Foreground", fg)
        self.option_add("*Listbox.SelectBackground", select_bg)
        self.option_add("*Listbox.SelectForeground", "#ffffff")
        if hasattr(self, "versions_list"):
            self.versions_list.configure(bg=listbox_bg, fg=fg, selectbackground=select_bg, selectforeground="#ffffff")
        if hasattr(self, "theme_button"):
            self.theme_button.config(text="☀" if dark_mode else "🌙")

    def _on_toggle_dark_mode(self) -> None:
        self.dark_mode_var.set(not self.dark_mode_var.get())
        self._apply_theme(self.dark_mode_var.get())

    def _asset_path(self, name: str) -> Path:
        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            return Path(getattr(sys, "_MEIPASS")) / "assets" / name
        return Path(__file__).resolve().parents[2] / "assets" / name

    def _set_window_icon(self) -> None:
        icon_candidates = [
            self._asset_path("tails-cloner-clone-64.png"),
            self._asset_path("tails-cloner-clone-48.png"),
            self._asset_path("tails-cloner-clone.png"),
        ]
        for icon_path in icon_candidates:
            if not icon_path.exists():
                continue
            try:
                icon_image = tk.PhotoImage(file=str(icon_path))
                self.iconphoto(True, icon_image)
                self._icon_image_ref = icon_image
                return
            except tk.TclError:
                continue

    def _add_readonly_row(self, parent: ttk.Frame, row: int, label: str, variable: tk.StringVar) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="nw", pady=(0, 6), padx=(0, 8))
        entry = ttk.Entry(parent, textvariable=variable)
        entry.grid(row=row, column=1, sticky="ew", pady=(0, 6))
        entry.state(["readonly"])

    def _browse_image(self) -> None:
        path = filedialog.askopenfilename(
            title="Choose a Tails image",
            filetypes=[("Disk images", "*.img *.iso"), ("All files", "*.*")],
        )
        if path:
            self.image_path_var.set(path)

    def _on_version_selected(self, _event=None) -> None:
        selection = self.versions_list.curselection()
        if not selection:
            return
        version = self.versions_list.get(selection[0])
        self.controller.select_version(version)
        self._sync_selected_version_fields()

    def _on_version_key_nav(self, _event=None) -> None:
        """Handle keyboard navigation in versions list."""
        selection = self.versions_list.curselection()
        if not selection:
            return
        version = self.versions_list.get(selection[0])
        self.controller.select_version(version)
        self._sync_selected_version_fields()

    def _on_source_mode_changed(self) -> None:
        """Handle source mode radio button change."""
        mode_str = self.source_mode_var.get()
        if mode_str == "running":
            self.controller.set_source_mode(SourceMode.RUNNING)
        elif mode_str == "local":
            self.controller.set_source_mode(SourceMode.LOCAL)
        elif mode_str == "remote":
            self.controller.set_source_mode(SourceMode.REMOTE)

    def _download_selected_remote_image(self) -> None:
        img_url = self.controller.state.selected_image_url.strip()
        if not img_url:
            self.controller.state.status_message = "No remote IMG URL selected."
            return

        cache_dir = Path.home() / ".cache" / "tails-cloner-clone" / "downloads"
        cache_dir.mkdir(parents=True, exist_ok=True)
        filename = Path(img_url).name or f"tails-{self.controller.state.selected_version}.img"
        target_path = cache_dir / filename

        self.source_status_var.set(f"Downloading {filename}…")
        self.controller.state.status_message = f"Downloading {filename}…"
        try:
            with urlopen(img_url, timeout=60) as response:  # noqa: S310 - user-selected remote source
                with target_path.open("wb") as out:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        out.write(chunk)
            self.image_path_var.set(str(target_path))
            self.controller.set_source_mode(SourceMode.LOCAL)
            self.source_mode_var.set("local")
            self.source_status_var.set(f"Downloaded to {target_path}")
            self.controller.state.status_message = f"Downloaded {filename}. Using local file source."
        except Exception as error:  # noqa: BLE001 - visible UI feedback
            self.source_status_var.set(f"Download failed: {error}")
            self.controller.state.status_message = f"Download failed: {error}"

    def _on_device_selected(self, _event=None) -> None:
        self._update_device_warnings_and_button()

    def _confirm_and_clone(self) -> None:
        selected_device_name = self.device_var.get()
        device_path = self._device_labels.get(selected_device_name, "")

        # Get image path or None if using running Tails
        image_path = None
        if self.controller.state.source_mode != SourceMode.RUNNING:
            image_path = self.image_path_var.get().strip()
            if not image_path:
                if self.controller.state.source_mode == SourceMode.REMOTE:
                    messagebox.showerror("Missing image", "Download the selected remote IMG first, then clone the local file.")
                else:
                    messagebox.showerror("Missing image", "Choose a local ISO or IMG file before cloning.")
                return

        if not device_path:
            messagebox.showerror("Missing device", "Choose a device before cloning.")
            return

        # Find the device object to check its state
        device = None
        for d in self.controller.state.devices:
            if d.pretty_name == selected_device_name:
                device = d
                break

        # Customize confirmation message based on device state
        button_text = self.clone_button.cget("text")

        # Get source description for confirmation
        if self.controller.state.source_mode == SourceMode.RUNNING:
            source_desc = f"running Tails {self.controller.state.running_tails_version}"
        else:
            source_desc = Path(image_path).name

        if button_text == "Upgrade":
            title = "Confirm upgrade"
            message = (
                f"Upgrade {device_path} from {source_desc}?\n\n"
                f"Source: {source_desc}\n"
                f"Target: {device_path} (existing Tails installation detected; installed version not detected)\n"
                f"Action: Upgrade preserving Persistent Storage\n\n"
                f"This will upgrade the existing Tails installation while preserving the Persistent Storage."
            )
        elif button_text == "Reinstall (delete all data)":
            title = "Confirm reinstallation"
            message = (
                f"Reinstall Tails on {device_path} from {source_desc}?\n\n"
                f"The Persistent Storage on this USB stick will be lost.\n\n"
                f"All data on this USB stick will be lost."
            )
        else:
            title = "Confirm installation"
            message = (
                f"Install Tails to {device_path} from {source_desc}?\n\n"
                f"All data on this USB stick will be lost."
            )

        confirmed = messagebox.askyesno(title, message, icon=messagebox.WARNING)
        if not confirmed:
            return
        self.controller.executor.submit(self._run_clone, image_path, device_path)

    def _run_clone(self, image_path: str | None, device_path: str) -> None:
        # Show progress UI
        self.after(0, lambda: self._show_clone_progress(True))
        self.after(0, lambda: self.clone_button.config(state="disabled"))
        self.after(0, lambda: self.progress_bar.start(10))

        try:
            def on_progress(message: str) -> None:
                self.after(0, lambda m=message: self.progress_label.config(text=m))

            self.controller.clone_selected_image(image_path, device_path, progress_callback=on_progress)

            # Clone completed successfully
            self.after(0, lambda: self._show_clone_progress(False))
            self.after(0, lambda: self.clone_button.config(state="normal"))
            self.after(0, lambda: messagebox.showinfo("Installation complete", f"Tails has been successfully installed to {device_path}."))
        except Exception as error:  # noqa: BLE001 - converted into visible UI feedback
            self.after(0, lambda: self._show_clone_progress(False))
            self.after(0, lambda: self.clone_button.config(state="normal"))
            self.controller.state.status_message = f"Clone failed: {error}"
            self.after(0, lambda: messagebox.showerror("Clone failed", str(error)))

    def _show_clone_progress(self, show: bool) -> None:
        if show:
            self.progress_frame.grid()
            self.progress_label.config(text="Starting clone operation...")
        else:
            self.progress_bar.stop()
            self.progress_frame.grid_remove()
            self.progress_label.config(text="")

    def _sync_state(self) -> None:
        self.status_var.set(self.controller.state.status_message)
        self._sync_source_mode()
        self._sync_versions()
        self._sync_devices()
        self._sync_selected_version_fields()
        self._sync_loading_labels()
        self._sync_upgrade_plan()
        self.after(REFRESH_INTERVAL_MS, self._sync_state)

    def _sync_source_mode(self) -> None:
        """Sync source mode radio buttons and running Tails info."""
        # Sync running Tails info
        self.running_tails_version_var.set(self.controller.state.running_tails_version or "Not available")
        self.running_tails_device_var.set(self.controller.state.running_tails_device or "Unknown device")

        # Sync source mode radio button
        current_mode = self.controller.state.source_mode
        if current_mode == SourceMode.RUNNING:
            self.source_mode_var.set("running")
            # Show running Tails frame, enable radio button
            self.source_running_radio.state(["!disabled"])
        else:
            self.source_running_radio.state(["disabled"])
        if current_mode == SourceMode.LOCAL:
            self.source_mode_var.set("local")
        elif current_mode == SourceMode.REMOTE:
            self.source_mode_var.set("remote")

        if current_mode == SourceMode.LOCAL:
            self.image_entry.state(["!disabled"])
            self.browse_button.state(["!disabled"])
            self.download_button.state(["disabled"])
        elif current_mode == SourceMode.REMOTE:
            self.image_entry.state(["disabled"])
            self.browse_button.state(["disabled"])
            self.download_button.state(["!disabled"])
        else:
            self.image_entry.state(["disabled"])
            self.browse_button.state(["disabled"])
            self.download_button.state(["disabled"])

    def _sync_versions(self) -> None:
        snapshot = tuple(entry.version for entry in self.controller.state.available_versions)
        if snapshot == self._last_versions_snapshot:
            return
        self._last_versions_snapshot = snapshot
        self.versions_list.delete(0, tk.END)
        for entry in self.controller.state.available_versions:
            self.versions_list.insert(tk.END, entry.version)
        if self.controller.state.selected_version:
            try:
                index = snapshot.index(self.controller.state.selected_version)
            except ValueError:
                return
            self.versions_list.selection_clear(0, tk.END)
            self.versions_list.selection_set(index)
            self.versions_list.activate(index)

    def _sync_devices(self) -> None:
        # Filter out the running Tails device when cloning from running Tails
        running_device = self.controller.state.running_tails_device
        devices = self.controller.state.devices

        if self.controller.state.source_mode == SourceMode.RUNNING and running_device:
            running_parent = get_parent_disk_path(running_device)
            # Exclude the running source disk and all partitions on that disk.
            devices = [
                d for d in devices
                if get_parent_disk_path(d.path) != running_parent
            ]

        labels = {device.pretty_name: device.path for device in devices}
        snapshot = tuple(labels)
        if snapshot == self._last_devices_snapshot:
            return
        self._last_devices_snapshot = snapshot
        self._device_labels = labels
        values = list(labels)

        # Keep current selection if it still exists
        current_selection = self.device_var.get()
        self.device_combo["values"] = values
        if values and current_selection not in labels:
            self.device_var.set(values[0])

        # Update warnings and button text
        self._update_device_warnings_and_button()

    def _update_device_warnings_and_button(self) -> None:
        """Update device warnings and clone button text based on selected device."""
        selected_name = self.device_var.get()
        if not selected_name:
            self.device_warning_label.config(text="")
            self.clone_button.config(text="Install", state="disabled")
            return

        # Find the device object
        device = None
        for d in self.controller.state.devices:
            if d.pretty_name == selected_name:
                device = d
                break

        if not device:
            self.device_warning_label.config(text="")
            self.clone_button.config(text="Install", state="disabled")
            return

        warnings = []

        # Check for various device issues (matching legacy installer behavior)
        if device.read_only:
            warnings.append("This device is read-only and cannot be written to.")
        elif not device.is_big_enough_for_installation:
            warnings.append(f"This device is too small to install Tails (at least {MIN_INSTALLATION_SIZE_GB} GB is required).")
            self.device_warning_label.config(text="\n".join(warnings))
            self.clone_button.config(text="Install", state="disabled")
            return
        elif device.has_tails:
            if device.is_big_enough_for_upgrade:
                self.clone_button.config(text="Upgrade")
                self.device_status_label.config(text="Device has Tails installed. You can upgrade or reinstall.", foreground="#2e7d32")
            else:
                warnings.append("This device has Tails installed but is too small for an upgrade from this version. Use a downloaded ISO image instead.")
                self.clone_button.config(text="Reinstall (delete all data)")
        elif not device.removable:
            warnings.append("This device is configured as non-removable by its manufacturer. Tails may fail to start from it. Please try installing on a different model.")

        if warnings:
            self.device_warning_label.config(text="\n".join(warnings))
            if device.read_only or not device.is_big_enough_for_installation:
                self.clone_button.config(state="disabled")
            else:
                self.clone_button.config(state="normal")
        else:
            self.device_warning_label.config(text="")
            self.clone_button.config(state="normal")

    def _sync_selected_version_fields(self) -> None:
        version = self.controller.state.selected_version
        if version == self._last_selected_version and self.selected_iso_url_var.get() == self.controller.state.selected_iso_url:
            return
        self._last_selected_version = version
        self.selected_version_var.set(version)
        self.selected_iso_url_var.set(self.controller.state.selected_iso_url)
        self.selected_image_url_var.set(self.controller.state.selected_image_url)
        self.selected_signature_url_var.set(self.controller.state.selected_signature_url)

    def _sync_loading_labels(self) -> None:
        version_label = "Refreshing remote versions…" if self.controller.state.versions_loading else "Remote versions idle"
        device_label = "Scanning devices…" if self.controller.state.devices_loading else "Device scan idle"
        self.version_status_label.config(text=version_label)
        self.device_status_label.config(text=device_label)

    def _sync_upgrade_plan(self) -> None:
        selected_name = self.device_var.get().strip()
        target_device = self._device_labels.get(selected_name, selected_name) if selected_name else "not selected"

        if self.controller.state.source_mode == SourceMode.RUNNING:
            running_version = self.controller.state.running_tails_version or "unknown"
            source_desc = f"running Tails {running_version} (live medium)"
        elif self.controller.state.source_mode == SourceMode.REMOTE:
            source_desc = f"remote {self.controller.state.selected_version or 'version'} (download required)"
        else:
            image_path = self.image_path_var.get().strip()
            source_desc = Path(image_path).name if image_path else "local ISO/IMG not selected"

        action = self.clone_button.cget("text")
        if action == "Upgrade":
            action_desc = "Upgrade preserving Persistent Storage"
        elif action == "Reinstall (delete all data)":
            action_desc = "Reinstall (deletes all data)"
        else:
            action_desc = "Install (deletes all data)"

        self.upgrade_plan_var.set(f"Source: {source_desc}\nTarget: {target_device}\nAction: {action_desc}")

    def _on_close(self) -> None:
        self.controller.shutdown()
        self.destroy()
