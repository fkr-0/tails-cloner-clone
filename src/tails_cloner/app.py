from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from tails_cloner.config import BRANDING, MIN_WINDOW_SIZE, REFRESH_INTERVAL_MS, WINDOW_SIZE
from tails_cloner.controller import ApplicationController
from tails_cloner.models import BlockDevice


class TailsClonerApp(tk.Tk):
    def __init__(self, controller: ApplicationController, remote_index_url: str) -> None:
        super().__init__()
        self.controller = controller
        self.remote_index_url = remote_index_url
        self.title(BRANDING.window_title)
        self.geometry(WINDOW_SIZE)
        self.minsize(*MIN_WINDOW_SIZE)

        self.status_var = tk.StringVar(value=self.controller.state.status_message)
        self.remote_url_var = tk.StringVar(value=self.remote_index_url)
        self.selected_version_var = tk.StringVar()
        self.selected_iso_url_var = tk.StringVar()
        self.selected_image_url_var = tk.StringVar()
        self.selected_signature_url_var = tk.StringVar()
        self.image_path_var = tk.StringVar()
        self.device_var = tk.StringVar()
        self._device_labels: dict[str, str] = {}
        self._last_versions_snapshot: tuple[str, ...] = ()
        self._last_devices_snapshot: tuple[str, ...] = ()
        self._last_selected_version: str = ""
        self._last_status: str = ""
        self._versions_busy_text = ""
        self._devices_busy_text = ""

        self._build_ui()
        self.controller.startup()
        self.after(REFRESH_INTERVAL_MS, self._sync_state)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=3)
        self.columnconfigure(1, weight=2)
        self.rowconfigure(1, weight=1)

        header = ttk.Frame(self, padding=16)
        header.grid(row=0, column=0, columnspan=2, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text=BRANDING.distribution + " Cloner", font=("TkDefaultFont", 18, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(
            header,
            text="Refreshed standalone installer architecture inspired by the original Tails installer layout.",
            foreground="#555555",
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Label(header, textvariable=self.remote_url_var, foreground="#666666").grid(row=2, column=0, sticky="w", pady=(4, 0))
        ttk.Button(header, text="Refresh versions", command=lambda: self.controller.executor.submit(self.controller.refresh_versions)).grid(row=0, column=1, padx=(12, 0))
        ttk.Button(header, text="Refresh devices", command=lambda: self.controller.executor.submit(self.controller.refresh_devices)).grid(row=0, column=2, padx=(8, 0))

        left = ttk.LabelFrame(self, text="Remote versions", padding=12)
        left.grid(row=1, column=0, sticky="nsew", padx=(16, 8), pady=(0, 8))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=1)

        version_toolbar = ttk.Frame(left)
        version_toolbar.grid(row=0, column=0, sticky="ew")
        version_toolbar.columnconfigure(0, weight=1)
        self.version_status_label = ttk.Label(version_toolbar, text="Idle", foreground="#666666")
        self.version_status_label.grid(row=0, column=0, sticky="w")

        self.versions_list = tk.Listbox(left, exportselection=False, activestyle="none")
        self.versions_list.grid(row=1, column=0, sticky="nsew")
        self.versions_list.bind("<<ListboxSelect>>", self._on_version_selected)

        details = ttk.Frame(left)
        details.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        details.columnconfigure(1, weight=1)
        self._add_readonly_row(details, 0, "Selected version", self.selected_version_var)
        self._add_readonly_row(details, 1, "Suggested ISO URL", self.selected_iso_url_var)
        self._add_readonly_row(details, 2, "Suggested IMG URL", self.selected_image_url_var)
        self._add_readonly_row(details, 3, "Signature URL", self.selected_signature_url_var)

        right = ttk.LabelFrame(self, text="Write image to removable device", padding=12)
        right.grid(row=1, column=1, sticky="nsew", padx=(8, 16), pady=(0, 8))
        right.columnconfigure(1, weight=1)

        ttk.Label(right, text="Local image file").grid(row=0, column=0, sticky="w")
        ttk.Entry(right, textvariable=self.image_path_var).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        ttk.Button(right, text="Browse…", command=self._browse_image).grid(row=1, column=2, padx=(8, 0))

        ttk.Label(right, text="Removable target device").grid(row=2, column=0, sticky="w", pady=(16, 0))
        self.device_combo = ttk.Combobox(right, textvariable=self.device_var, state="readonly")
        self.device_combo.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(4, 0))

        self.device_status_label = ttk.Label(right, text="Idle", foreground="#666666")
        self.device_status_label.grid(row=4, column=0, columnspan=3, sticky="w", pady=(12, 0))

        ttk.Button(right, text="Clone image", command=self._confirm_and_clone).grid(row=5, column=0, columnspan=3, sticky="ew", pady=(20, 0))
        warning_text = (
            "This tool writes directly to the selected block device with dd. "
            "Treat it like a loaded weapon and verify the target path every time."
        )
        ttk.Label(right, text=warning_text, wraplength=320, foreground="#7a1f1f").grid(row=6, column=0, columnspan=3, sticky="w", pady=(12, 0))

        ttk.Label(self, textvariable=self.status_var, relief="sunken", anchor="w", padding=(12, 8)).grid(
            row=2,
            column=0,
            columnspan=2,
            sticky="ew",
            padx=16,
            pady=(0, 16),
        )

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

    def _confirm_and_clone(self) -> None:
        image_path = self.image_path_var.get().strip()
        device_path = self._device_labels.get(self.device_var.get(), "")
        if not image_path:
            messagebox.showerror("Missing image", "Choose a local ISO or IMG file before cloning.")
            return
        if not device_path:
            messagebox.showerror("Missing device", "Choose a removable device before cloning.")
            return
        confirmed = messagebox.askyesno(
            "Confirm destructive write",
            f"Write {Path(image_path).name} to {device_path}?\n\nThis permanently overwrites the target device.",
            icon=messagebox.WARNING,
        )
        if not confirmed:
            return
        self.controller.executor.submit(self._run_clone, image_path, device_path)

    def _run_clone(self, image_path: str, device_path: str) -> None:
        try:
            self.controller.clone_selected_image(image_path, device_path)
        except Exception as error:  # noqa: BLE001 - converted into visible UI feedback
            self.controller.state.status_message = f"Clone failed: {error}"
            self.after(0, lambda: messagebox.showerror("Clone failed", str(error)))

    def _sync_state(self) -> None:
        self.status_var.set(self.controller.state.status_message)
        self._sync_versions()
        self._sync_devices()
        self._sync_selected_version_fields()
        self._sync_loading_labels()
        self.after(REFRESH_INTERVAL_MS, self._sync_state)

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
        labels = {device.pretty_name: device.path for device in self.controller.state.devices}
        snapshot = tuple(labels)
        if snapshot == self._last_devices_snapshot:
            return
        self._last_devices_snapshot = snapshot
        self._device_labels = labels
        values = list(labels)
        self.device_combo["values"] = values
        if values and self.device_var.get() not in labels:
            self.device_var.set(values[0])

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
        device_label = "Scanning removable devices…" if self.controller.state.devices_loading else "Device scan idle"
        self.version_status_label.config(text=version_label)
        self.device_status_label.config(text=device_label)

    def _on_close(self) -> None:
        self.controller.shutdown()
        self.destroy()
