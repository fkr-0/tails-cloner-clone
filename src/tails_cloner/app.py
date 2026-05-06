from __future__ import annotations

import tkinter as tk
import sys
import webbrowser
from datetime import datetime
import hashlib
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from urllib.request import urlopen

from tails_cloner.boot_loader import discover_boot_loader_entries
from tails_cloner.config import BRANDING, FONT_SIZE_LARGE, FONT_SIZE_MEDIUM, MIN_WINDOW_SIZE, REFRESH_INTERVAL_MS, WINDOW_SIZE
from tails_cloner.controller import ApplicationController
from tails_cloner.devices import MIN_INSTALLATION_SIZE_GB
from tails_cloner.models import BlockDevice, SourceMode
from tails_cloner.source import get_parent_disk_path


class TailsClonerApp(tk.Tk):
    def __init__(self, controller: ApplicationController, remote_index_url: str) -> None:
        super().__init__()
        self.controller = controller
        self.remote_index_url = remote_index_url
        self._set_window_class()
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
        self.source_details_var = tk.StringVar(value="Select a source mode.")
        self.remote_source_info_var = tk.StringVar(value=f"Remote source: {self.remote_index_url}\nLast refresh: never")
        self.remote_state_var = tk.StringVar(value="not downloaded")
        self.suggested_local_path_var = tk.StringVar(value="")
        self.suggested_checksum_var = tk.StringVar(value="")
        self.local_checksum_var = tk.StringVar(value="")
        self.action_mode_var = tk.StringVar(value="install")
        self.experimental_enabled_var = tk.BooleanVar(value=False)
        self.boot_loader_entry_var = tk.StringVar(value="")
        self.boot_loader_status_var = tk.StringVar(value="Parse entries from the selected image, then reorder them.")
        self.tab2_source_var = tk.StringVar(value="Source: not selected")
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
        self._last_versions_refresh_at: str = "never"
        self._checksum_job_id = 0

        self._set_window_icon()
        self._configure_theme()
        self._build_ui()
        self.image_path_var.trace_add("write", lambda *_: self._schedule_local_checksum_refresh())
        self.controller.startup()
        self.after(REFRESH_INTERVAL_MS, self._sync_state)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        # Keyboard bindings
        self.bind("<Control-r>", lambda e: self.controller.executor.submit(self.controller.refresh_versions))
        self.bind("<Control-d>", lambda e: self.controller.executor.submit(self.controller.refresh_devices))
        self.bind("<Control-q>", lambda e: self._on_close())
        self.bind("<Escape>", lambda e: self._on_close())

        header = ttk.Frame(self, padding=16)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        title_frame = ttk.Frame(header)
        title_frame.grid(row=0, column=0, sticky="w")
        self.header_icon_label = ttk.Label(title_frame, text="")
        self.header_icon_label.grid(row=0, column=0, sticky="w", padx=(0, 8))
        self._set_header_icon()
        ttk.Label(title_frame, text="Tails Cloner Clone", font=("TkDefaultFont", 22, "bold")).grid(row=0, column=1, sticky="w")
        ttk.Label(
            header,
            text="Inofficial(!) Tails download/install/update tool. Refer to",
            foreground="#555555",
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.downloads_link_label = tk.Label(header, text="https://downloads.tails.net", cursor="hand2", fg="#4f8cff")
        self.downloads_link_label.grid(row=1, column=0, sticky="w", padx=(370, 0), pady=(4, 0))
        self.downloads_link_label.bind("<Button-1>", lambda _e: webbrowser.open_new_tab("https://downloads.tails.net"))
        self.downloads_link_label.bind("<Enter>", self._on_link_enter)
        self.downloads_link_label.bind("<Leave>", self._on_link_leave)
        ttk.Label(header, textvariable=self.remote_url_var, foreground="#666666").grid(row=2, column=0, sticky="w", pady=(4, 0))
        ttk.Button(header, text="Refresh Versions (Ctrl+R)", command=lambda: self.controller.executor.submit(self._refresh_versions_task)).grid(row=0, column=1, padx=(12, 0))
        ttk.Button(header, text="Refresh Devices (Ctrl+D)", command=lambda: self.controller.executor.submit(self.controller.refresh_devices)).grid(row=0, column=2, padx=(8, 0))
        self.theme_button = ttk.Button(header, text="☀" if self.dark_mode_var.get() else "🌙", width=3, command=self._on_toggle_dark_mode)
        self.theme_button.grid(row=0, column=3, padx=(8, 0))
        ttk.Button(header, text="✕", width=3, command=self._on_close).grid(row=0, column=4, padx=(8, 0))

        notebook = ttk.Notebook(self)
        notebook.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 8))
        tab_source = ttk.Frame(notebook, padding=8)
        tab_write = ttk.Frame(notebook, padding=8)
        notebook.add(tab_source, text="Source")
        notebook.add(tab_write, text="Write")
        tab_source.columnconfigure(0, weight=1)
        tab_source.columnconfigure(1, weight=1)
        tab_source.rowconfigure(1, weight=1)
        tab_write.columnconfigure(0, weight=1)
        tab_write.rowconfigure(1, weight=1)

        # Source selection panel (tab1 top-left)
        source_frame = ttk.LabelFrame(tab_source, text="Source", padding=12)
        source_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=(0, 8))
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

        source_details = ttk.LabelFrame(tab_source, text="Current source details", padding=12)
        source_details.grid(row=0, column=1, sticky="nsew", padx=(8, 0), pady=(0, 8))
        ttk.Label(source_details, textvariable=self.source_details_var, wraplength=500, justify="left").grid(row=0, column=0, sticky="w")

        # Remote versions panel (tab1 bottom-left)
        left = ttk.LabelFrame(tab_source, text="Remote versions", padding=12)
        left.grid(row=1, column=0, sticky="nsew", padx=(0, 8), pady=(0, 8))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=1)
        ttk.Label(left, textvariable=self.remote_source_info_var, foreground="#666666", justify="left").grid(row=0, column=0, sticky="w", pady=(0, 8))

        version_toolbar = ttk.Frame(left)
        version_toolbar.grid(row=1, column=0, sticky="ew")
        version_toolbar.columnconfigure(0, weight=1)
        self.version_status_label = ttk.Label(version_toolbar, text="Idle", foreground="#666666")
        self.version_status_label.grid(row=0, column=0, sticky="w")

        self.versions_list = tk.Listbox(left, exportselection=False, activestyle="none", font=("TkDefaultFont", FONT_SIZE_MEDIUM))
        self.versions_list.grid(row=2, column=0, sticky="nsew")
        self.versions_list.bind("<<ListboxSelect>>", self._on_version_selected)
        # Allow keyboard navigation in versions list
        self.versions_list.bind("<KeyRelease-Up>", self._on_version_key_nav)
        self.versions_list.bind("<KeyRelease-Down>", self._on_version_key_nav)

        details = ttk.Frame(tab_source)
        details.grid(row=1, column=1, sticky="nsew", padx=(8, 0), pady=(0, 8))
        details.columnconfigure(1, weight=1)
        details_box = ttk.LabelFrame(details, text="Selected remote metadata", padding=12)
        details_box.grid(row=0, column=0, sticky="nsew")
        details_box.columnconfigure(1, weight=1)
        self._add_readonly_row(details_box, 0, "Selected version", self.selected_version_var)
        self._add_readonly_row(details_box, 1, "Suggested ISO URL", self.selected_iso_url_var)
        self._add_readonly_row(details_box, 2, "Suggested IMG URL", self.selected_image_url_var)
        self._add_readonly_row(details_box, 3, "Signature URL", self.selected_signature_url_var)
        self._add_readonly_row(details_box, 4, "Remote/download state", self.remote_state_var)
        self._add_readonly_row(details_box, 5, "Suggested local path", self.suggested_local_path_var)
        ttk.Label(details_box, text="Suggested checksum").grid(row=6, column=0, sticky="nw", pady=(0, 6), padx=(0, 8))
        self.suggested_checksum_entry = ttk.Entry(details_box, textvariable=self.suggested_checksum_var)
        self.suggested_checksum_entry.grid(row=6, column=1, sticky="ew", pady=(0, 6))
        self._add_readonly_row(details_box, 7, "Local checksum", self.local_checksum_var)

        exp_panel = ttk.LabelFrame(tab_write, text="Experimental", padding=12)
        exp_panel.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        exp_panel.columnconfigure(0, weight=1)
        ttk.Checkbutton(
            exp_panel,
            text="Enable experimental controls",
            variable=self.experimental_enabled_var,
            command=self._sync_experimental_state,
        ).grid(row=0, column=0, sticky="w")

        self.experimental_notebook = ttk.Notebook(exp_panel)
        self.experimental_notebook.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        self.boot_order_tab = ttk.Frame(self.experimental_notebook, padding=8)
        self.experimental_notebook.add(self.boot_order_tab, text="Boot-loader order")
        self.boot_order_tab.columnconfigure(0, weight=1)
        self.boot_order_tab.columnconfigure(1, weight=0)
        self.boot_order_tab.rowconfigure(1, weight=1)

        ttk.Label(
            self.boot_order_tab,
            text="Parse boot entries from the selected image, then reorder, remove, or add entries.",
            wraplength=720,
            justify="left",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
        self.boot_order_list = tk.Listbox(self.boot_order_tab, height=5, exportselection=False, activestyle="none")
        self.boot_order_list.grid(row=1, column=0, sticky="nsew")
        boot_buttons = ttk.Frame(self.boot_order_tab)
        boot_buttons.grid(row=1, column=1, sticky="ns", padx=(8, 0))
        self.boot_order_up_button = ttk.Button(boot_buttons, text="↑", width=3, command=lambda: self._move_boot_order_entry(-1))
        self.boot_order_up_button.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        self.boot_order_down_button = ttk.Button(boot_buttons, text="↓", width=3, command=lambda: self._move_boot_order_entry(1))
        self.boot_order_down_button.grid(row=1, column=0, sticky="ew", pady=(0, 4))
        self.boot_order_remove_button = ttk.Button(boot_buttons, text="−", width=3, command=self._remove_boot_order_entry)
        self.boot_order_remove_button.grid(row=2, column=0, sticky="ew", pady=(0, 4))
        add_row = ttk.Frame(self.boot_order_tab)
        add_row.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        add_row.columnconfigure(0, weight=1)
        self.boot_order_entry = ttk.Entry(add_row, textvariable=self.boot_loader_entry_var)
        self.boot_order_entry.grid(row=0, column=0, sticky="ew")
        self.boot_order_add_button = ttk.Button(add_row, text="+", width=3, command=self._add_boot_order_entry)
        self.boot_order_add_button.grid(row=0, column=1, padx=(8, 0))
        self.boot_order_parse_button = ttk.Button(
            self.boot_order_tab,
            text="Parse from selected image/source",
            command=lambda: self.controller.executor.submit(self._parse_boot_order_entries_task),
        )
        self.boot_order_parse_button.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Label(
            self.boot_order_tab,
            textvariable=self.boot_loader_status_var,
            foreground="#666666",
            wraplength=720,
            justify="left",
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(6, 0))

        right = ttk.LabelFrame(tab_write, text="Write image to device", padding=12)
        right.grid(row=1, column=0, sticky="nsew")
        right.columnconfigure(1, weight=1)
        ttk.Label(right, textvariable=self.tab2_source_var, wraplength=600, justify="left").grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))
        mode_row = ttk.Frame(right)
        mode_row.grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 10))
        ttk.Radiobutton(mode_row, text="Install", value="install", variable=self.action_mode_var, command=self._on_action_mode_changed).grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(mode_row, text="Update", value="update", variable=self.action_mode_var, command=self._on_action_mode_changed).grid(row=0, column=1, sticky="w", padx=(16, 0))

        ttk.Label(right, text="Target device").grid(row=2, column=0, sticky="w")
        self.device_combo = ttk.Combobox(right, textvariable=self.device_var, state="readonly")
        self.device_combo.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(4, 0))
        self.device_combo.bind("<<ComboboxSelected>>", self._on_device_selected)

        self.device_status_label = ttk.Label(right, text="Idle", foreground="#666666", wraplength=320)
        self.device_status_label.grid(row=4, column=0, columnspan=3, sticky="w", pady=(12, 0))

        self.device_warning_label = ttk.Label(right, text="", foreground="#a63636", wraplength=320)
        self.device_warning_label.grid(row=5, column=0, columnspan=3, sticky="w", pady=(4, 0))

        self.upgrade_plan_label = ttk.Label(
            right,
            textvariable=self.upgrade_plan_var,
            foreground="#1f4d8f",
            wraplength=320,
            justify="left",
        )
        self.upgrade_plan_label.grid(row=6, column=0, columnspan=3, sticky="w", pady=(8, 0))

        # Progress bar for clone operation
        self.progress_frame = ttk.Frame(right)
        self.progress_frame.grid(row=7, column=0, columnspan=3, sticky="ew", pady=(12, 0))
        self.progress_bar = ttk.Progressbar(self.progress_frame, mode="indeterminate")
        self.progress_bar.grid(row=0, column=0, sticky="ew")
        self.progress_label = ttk.Label(self.progress_frame, text="", foreground="#666666")
        self.progress_label.grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.progress_frame.grid_remove()  # Hidden by default

        self.clone_button = ttk.Button(right, text="Install", command=self._confirm_and_clone)
        self.clone_button.grid(row=8, column=0, columnspan=3, sticky="ew", pady=(20, 0))
        # Make clone button the default (activated by Enter)
        self.clone_button.bind("<Return>", lambda e: self._confirm_and_clone())
        warning_text = (
            "This tool writes directly to the selected block device with dd. "
            "Treat it like a loaded weapon and verify the target path every time.\n\n"
            "All data on the target device will be permanently lost."
        )
        self.install_warning_label = ttk.Label(right, text=warning_text, wraplength=340, justify="left", foreground="#7a1f1f")
        self.install_warning_label.grid(row=9, column=0, columnspan=3, sticky="w", pady=(12, 0))

        ttk.Label(self, textvariable=self.status_var, relief="sunken", anchor="w", padding=(12, 8)).grid(
            row=2,
            column=0,
            sticky="ew",
            padx=16,
            pady=(0, 16),
        )

        # Set initial focus to device combo for quick access
        self.device_combo.focus_set()
        self._sync_experimental_state()

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
        style.configure("TEntry", fieldbackground=entry_bg, foreground=fg)
        style.configure("TCombobox", fieldbackground=entry_bg, foreground=fg)
        style.map("TRadiobutton", background=[("active", bg)], foreground=[("active", fg)])
        style.map("TButton", background=[("active", active_bg)], foreground=[("active", fg)])
        style.map("TEntry", fieldbackground=[("readonly", entry_bg)], foreground=[("readonly", fg)])

        self.option_add("*Listbox.Background", listbox_bg)
        self.option_add("*Listbox.Foreground", fg)
        self.option_add("*Listbox.SelectBackground", select_bg)
        self.option_add("*Listbox.SelectForeground", "#ffffff")
        if hasattr(self, "versions_list"):
            self.versions_list.configure(bg=listbox_bg, fg=fg, selectbackground=select_bg, selectforeground="#ffffff")
        if hasattr(self, "boot_order_list"):
            self.boot_order_list.configure(bg=listbox_bg, fg=fg, selectbackground=select_bg, selectforeground="#ffffff")
        if hasattr(self, "theme_button"):
            self.theme_button.config(text="☀" if dark_mode else "🌙")
        if hasattr(self, "downloads_link_label"):
            self.downloads_link_label.configure(bg=bg, fg="#7db0ff" if dark_mode else "#2f6db5")

    def _on_toggle_dark_mode(self) -> None:
        self.dark_mode_var.set(not self.dark_mode_var.get())
        self._apply_theme(self.dark_mode_var.get())

    def _set_window_class(self) -> None:
        try:
            self.tk.call("wm", "class", self._w, "tails-cloner-clone")
        except tk.TclError:
            # Some Tk builds may not expose wm class control consistently.
            pass

    def _set_header_icon(self) -> None:
        icon_path = self._asset_path("tails-cloner-clone-32.png")
        if not icon_path.exists():
            return
        try:
            icon = tk.PhotoImage(file=str(icon_path))
            self.header_icon_label.configure(image=icon)
            self._header_icon_ref = icon
        except tk.TclError:
            pass

    def _on_link_enter(self, _event=None) -> None:
        self.downloads_link_label.configure(fg="#ffd166", font=("TkDefaultFont", FONT_SIZE_MEDIUM, "underline"))

    def _on_link_leave(self, _event=None) -> None:
        self.downloads_link_label.configure(
            fg="#7db0ff" if self.dark_mode_var.get() else "#2f6db5",
            font=("TkDefaultFont", FONT_SIZE_MEDIUM),
        )

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

    def _refresh_versions_task(self) -> None:
        self._last_versions_refresh_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.controller.refresh_versions()

    def _sync_remote_source_info(self) -> None:
        self.remote_source_info_var.set(f"Remote source: {self.remote_index_url}\nLast refresh: {self._last_versions_refresh_at}")

    def _sync_source_details(self) -> None:
        mode = self.controller.state.source_mode
        if mode == SourceMode.RUNNING:
            self.source_details_var.set(
                f"Clone current live system\nVersion: {self.controller.state.running_tails_version or 'unknown'}\n"
                f"Source device: {self.controller.state.running_tails_device or 'unknown'}\n"
                "Persistence is not cloned."
            )
        elif mode == SourceMode.REMOTE:
            self.source_details_var.set(
                f"Remote version source\nSelected version: {self.controller.state.selected_version or 'none'}\n"
                f"IMG URL: {self.controller.state.selected_image_url or 'n/a'}\n"
                f"State: {self.remote_state_var.get()}"
            )
        else:
            local_path = self.image_path_var.get().strip() or "not selected"
            self.source_details_var.set(
                f"Local image source\nPath: {local_path}\n"
                f"Local checksum: {self.local_checksum_var.get() or 'not computed'}"
            )

    def _sync_tab2_source_summary(self) -> None:
        if self.controller.state.source_mode == SourceMode.RUNNING:
            self.tab2_source_var.set(
                f"Source: running Tails {self.controller.state.running_tails_version or 'unknown'} "
                f"from {self.controller.state.running_tails_device or 'unknown'}"
            )
        else:
            self.tab2_source_var.set(f"Source: {self.image_path_var.get().strip() or 'not selected'}")

    def _sync_experimental_state(self) -> None:
        state = "normal" if self.experimental_enabled_var.get() else "disabled"
        widgets = [
            self.boot_order_list,
            self.boot_order_entry,
            self.boot_order_add_button,
            self.boot_order_remove_button,
            self.boot_order_up_button,
            self.boot_order_down_button,
            self.boot_order_parse_button,
        ]
        for widget in widgets:
            try:
                widget.config(state=state)
            except tk.TclError:
                pass
        self._sync_post_write_options()

    def _boot_order_entries(self) -> list[str]:
        return [str(self.boot_order_list.get(index)) for index in range(self.boot_order_list.size())]

    def _set_boot_order_entries(self, entries: list[str]) -> None:
        self.boot_order_list.delete(0, tk.END)
        seen: set[str] = set()
        for raw_entry in entries:
            entry = raw_entry.strip()
            if not entry or entry in seen:
                continue
            seen.add(entry)
            self.boot_order_list.insert(tk.END, entry)
        if self.boot_order_list.size() > 0:
            self.boot_order_list.selection_set(0)
        self._sync_post_write_options()

    def _sync_post_write_options(self) -> None:
        options = self.controller.state.post_write_options
        options.enabled = self.experimental_enabled_var.get()
        options.boot_loader_order.enabled = self.experimental_enabled_var.get()
        options.boot_loader_order.entries = self._boot_order_entries() if self.experimental_enabled_var.get() else []

    def _selected_boot_order_index(self) -> int | None:
        selection = self.boot_order_list.curselection()
        return int(selection[0]) if selection else None

    def _move_boot_order_entry(self, direction: int) -> None:
        index = self._selected_boot_order_index()
        if index is None:
            return
        new_index = index + direction
        if new_index < 0 or new_index >= self.boot_order_list.size():
            return
        entry = self.boot_order_list.get(index)
        self.boot_order_list.delete(index)
        self.boot_order_list.insert(new_index, entry)
        self.boot_order_list.selection_clear(0, tk.END)
        self.boot_order_list.selection_set(new_index)
        self.boot_order_list.activate(new_index)
        self._sync_post_write_options()

    def _remove_boot_order_entry(self) -> None:
        index = self._selected_boot_order_index()
        if index is None:
            return
        self.boot_order_list.delete(index)
        next_index = min(index, self.boot_order_list.size() - 1)
        if next_index >= 0:
            self.boot_order_list.selection_set(next_index)
        self._sync_post_write_options()

    def _add_boot_order_entry(self) -> None:
        entry = self.boot_loader_entry_var.get().strip()
        if not entry:
            return
        if entry not in self._boot_order_entries():
            self.boot_order_list.insert(tk.END, entry)
            self.boot_order_list.selection_clear(0, tk.END)
            self.boot_order_list.selection_set(tk.END)
        self.boot_loader_entry_var.set("")
        self._sync_post_write_options()

    def _parse_boot_order_entries_task(self) -> None:
        source_path = self.image_path_var.get().strip()
        if self.controller.state.source_mode == SourceMode.RUNNING:
            source_path = self.controller.state.running_tails_device
        if not source_path:
            self.after(0, lambda: self.boot_loader_status_var.set("No image/source selected to parse."))
            return
        entries = discover_boot_loader_entries(source_path)
        if not entries:
            self.after(0, lambda: self.boot_loader_status_var.set("No boot-loader entries found in selected source."))
            return
        self.after(0, lambda e=entries: self._apply_parsed_boot_order_entries(e))

    def _apply_parsed_boot_order_entries(self, entries: list[str]) -> None:
        self._set_boot_order_entries(entries)
        self.boot_loader_status_var.set(f"Parsed {len(entries)} boot-loader entr{'y' if len(entries) == 1 else 'ies'}.")

    def _on_action_mode_changed(self) -> None:
        if self.action_mode_var.get() == "update":
            self.install_warning_label.config(
                text="Update mode preserves existing Persistent Storage when possible.",
                foreground="#2e7d32",
            )
        else:
            warning_text = (
                "This tool writes directly to the selected block device with dd. "
                "Treat it like a loaded weapon and verify the target path every time.\n\n"
                "All data on the target device will be permanently lost."
            )
            self.install_warning_label.config(text=warning_text, foreground="#7a1f1f")
        self._sync_devices()

    def _schedule_local_checksum_refresh(self) -> None:
        self._checksum_job_id += 1
        job_id = self._checksum_job_id
        image_path = self.image_path_var.get().strip()
        if not image_path:
            self.local_checksum_var.set("")
            return

        def worker() -> None:
            checksum = self._compute_file_sha256(image_path)
            self.after(0, lambda: self._apply_local_checksum(job_id, checksum))

        self.controller.executor.submit(worker)

    def _apply_local_checksum(self, job_id: int, checksum: str) -> None:
        if job_id != self._checksum_job_id:
            return
        self.local_checksum_var.set(checksum)

    def _compute_file_sha256(self, path: str) -> str:
        candidate = Path(path)
        if not candidate.exists() or not candidate.is_file():
            return ""
        digest = hashlib.sha256()
        try:
            with candidate.open("rb") as handle:
                while True:
                    chunk = handle.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
            return digest.hexdigest()
        except Exception:
            return ""

    def _fetch_suggested_checksum(self) -> None:
        url = self.controller.state.selected_checksum_url.strip()
        if not url:
            self.after(0, lambda: self.suggested_checksum_var.set(""))
            return
        try:
            with urlopen(url, timeout=20) as response:  # noqa: S310 - remote metadata chosen by user context
                content = response.read().decode("utf-8", errors="replace")
            token = content.strip().split()[0] if content.strip() else ""
            checksum = token if len(token) >= 32 else ""
        except Exception:
            checksum = ""
        self.after(0, lambda c=checksum: self.suggested_checksum_var.set(c))

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
            self.remote_state_var.set("downloaded")
            self.suggested_local_path_var.set(str(target_path))
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
        self._sync_source_details()
        self._sync_tab2_source_summary()
        self._sync_versions()
        self._sync_remote_source_info()
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

        if self.action_mode_var.get() == "update":
            devices = [d for d in devices if d.has_tails]
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
        self.controller.executor.submit(self._fetch_suggested_checksum)

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
