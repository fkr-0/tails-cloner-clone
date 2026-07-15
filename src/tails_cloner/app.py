from __future__ import annotations

import subprocess
import sys
import tkinter as tk
import webbrowser
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime
from functools import partial
from pathlib import Path
from queue import Empty, SimpleQueue
from tempfile import NamedTemporaryFile
from tkinter import filedialog, messagebox, ttk
from urllib.parse import urlparse
from urllib.request import urlopen

from tails_cloner.boot_loader import discover_boot_loader_entries
from tails_cloner.config import (
    BRANDING,
    FONT_SIZE_MEDIUM,
    MIN_WINDOW_SIZE,
    REFRESH_INTERVAL_MS,
    WINDOW_SIZE,
)
from tails_cloner.controller import ApplicationController
from tails_cloner.models import SourceMode
from tails_cloner.network import fetch_text_torified, should_use_torify
from tails_cloner.planner import OperationKind
from tails_cloner.verification import parse_sha256_text, sha256_file, verify_sha256


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
        self.attached_source_device_var = tk.StringVar(value="")
        self.attached_source_mount_var = tk.StringVar(value="")
        self.attached_source_status_var = tk.StringVar(value="No attached live source selected.")
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
        self._ui_events: SimpleQueue[Callable[[], None]] = SimpleQueue()
        self._write_in_progress = False

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
        self.bind("<Control-r>", lambda _event: self.controller.executor.submit(self.controller.refresh_versions))
        self.bind("<Control-d>", lambda _event: self.controller.executor.submit(self.controller.refresh_devices))
        self.bind("<Control-q>", lambda _event: self._on_close())
        self.bind("<Escape>", lambda _event: self._on_close())

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
            command=self._start_remote_download,
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

        # Attached live source option
        self.source_attached_frame = ttk.Frame(source_frame)
        self.source_attached_frame.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        self.source_attached_frame.columnconfigure(1, weight=1)
        self.source_attached_radio = ttk.Radiobutton(
            self.source_attached_frame,
            text="Use attached Tails live source for upgrade",
            value="attached",
            variable=self.source_mode_var,
            command=self._on_source_mode_changed,
        )
        self.source_attached_radio.grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(self.source_attached_frame, text="Source device:", foreground="#666666").grid(row=1, column=0, sticky="w", padx=(20, 4), pady=(4, 0))
        self.attached_source_device_entry = ttk.Entry(self.source_attached_frame, textvariable=self.attached_source_device_var)
        self.attached_source_device_entry.grid(row=1, column=1, sticky="ew", pady=(4, 0))
        ttk.Label(self.source_attached_frame, text="Mount point:", foreground="#666666").grid(row=2, column=0, sticky="w", padx=(20, 4), pady=(4, 0))
        self.attached_source_mount_entry = ttk.Entry(self.source_attached_frame, textvariable=self.attached_source_mount_var)
        self.attached_source_mount_entry.grid(row=2, column=1, sticky="ew", pady=(4, 0))
        self.attached_source_validate_button = ttk.Button(
            self.source_attached_frame,
            text="Validate attached source",
            command=self._validate_attached_live_source,
        )
        self.attached_source_validate_button.grid(row=1, column=2, rowspan=2, sticky="ns", padx=(8, 0), pady=(4, 0))
        ttk.Label(
            self.source_attached_frame,
            textvariable=self.attached_source_status_var,
            foreground="#666666",
            wraplength=520,
        ).grid(row=3, column=0, columnspan=3, sticky="w", padx=(20, 0), pady=(4, 0))

        # Local file source option
        self.source_local_frame = ttk.Frame(source_frame)
        self.source_local_frame.grid(row=3, column=0, sticky="ew", pady=(10, 0))
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
            command=self._start_parse_boot_order_entries,
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
        ttk.Radiobutton(
            mode_row,
            text="Install / Reinstall",
            value="install",
            variable=self.action_mode_var,
            command=self._on_action_mode_changed,
        ).grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(
            mode_row,
            text="Upgrade existing Tails",
            value="upgrade",
            variable=self.action_mode_var,
            command=self._on_action_mode_changed,
        ).grid(row=0, column=1, sticky="w", padx=(16, 0))

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
        self.clone_button.bind("<Return>", lambda _event: self._confirm_and_clone())
        warning_text = self._install_warning_text()
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
        with suppress(tk.TclError):
            style.theme_use("clam")

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
        # Some Tk builds may not expose wm class control consistently.
        with suppress(tk.TclError):
            self.tk.call("wm", "class", getattr(self, "_w", "."), "tails-cloner-clone")

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

    def _on_link_enter(self, _event: tk.Event | None = None) -> None:
        self.downloads_link_label.configure(fg="#ffd166", font=("TkDefaultFont", FONT_SIZE_MEDIUM, "underline"))

    def _on_link_leave(self, _event: tk.Event | None = None) -> None:
        self.downloads_link_label.configure(
            fg="#7db0ff" if self.dark_mode_var.get() else "#2f6db5",
            font=("TkDefaultFont", FONT_SIZE_MEDIUM),
        )

    def _asset_path(self, name: str) -> Path:
        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            return Path(sys._MEIPASS) / "assets" / name
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
        elif mode == SourceMode.ATTACHED:
            self.source_details_var.set(
                f"Attached live source\nVersion: {self.controller.state.attached_live_source_version or 'unknown'}\n"
                f"Source device: {self.controller.state.attached_live_source_device or 'not selected'}\n"
                f"Mount point: {self.controller.state.attached_live_source_mount or 'not selected'}\n"
                "Only persistence-preserving upgrade is supported."
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
        elif self.controller.state.source_mode == SourceMode.ATTACHED:
            self.tab2_source_var.set(
                f"Source: attached Tails live source {self.controller.state.attached_live_source_version or 'unknown'} "
                f"from {self.controller.state.attached_live_source_device or 'not selected'}"
            )
        else:
            self.tab2_source_var.set(f"Source: {self.image_path_var.get().strip() or 'not selected'}")

    def _sync_experimental_state(self) -> None:
        state = "normal" if self.experimental_enabled_var.get() else "disabled"
        widgets: list[tk.Widget] = [
            self.boot_order_list,
            self.boot_order_entry,
            self.boot_order_add_button,
            self.boot_order_remove_button,
            self.boot_order_up_button,
            self.boot_order_down_button,
            self.boot_order_parse_button,
        ]
        for widget in widgets:
            with suppress(tk.TclError):
                widget.configure({"state": state})
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
        selection = self.boot_order_list.curselection()  # type: ignore[no-untyped-call]
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

    def _start_parse_boot_order_entries(self) -> None:
        source_path = self.image_path_var.get().strip()
        if self.controller.state.source_mode == SourceMode.RUNNING:
            source_path = self.controller.state.running_tails_device
        if not source_path:
            self.boot_loader_status_var.set("No image/source selected to parse.")
            return
        self.controller.executor.submit(self._parse_boot_order_entries_task, source_path)

    def _parse_boot_order_entries_task(self, source_path: str) -> None:
        entries = discover_boot_loader_entries(source_path)
        if not entries:
            self._queue_ui(lambda: self.boot_loader_status_var.set("No boot-loader entries found in selected source."))
            return
        self._queue_ui(partial(self._apply_parsed_boot_order_entries, entries))

    def _apply_parsed_boot_order_entries(self, entries: list[str]) -> None:
        self._set_boot_order_entries(entries)
        self.boot_loader_status_var.set(f"Parsed {len(entries)} boot-loader entr{'y' if len(entries) == 1 else 'ies'}.")

    def _install_warning_text(self) -> str:
        return (
            "Install/Reinstall writes a complete image to the selected block device. "
            "Treat it like a loaded weapon and verify the target path every time.\n\n"
            "All data on the target device, including Persistent Storage, will be permanently lost."
        )

    def _upgrade_warning_text(self) -> str:
        return (
            "Upgrade replaces only the existing Tails system partition. "
            "Existing Persistent Storage, if present, is kept intact.\n\n"
            "Only select this for a device that already has Tails installed."
        )

    def _upgrade_mode_enabled(self) -> bool:
        return self.action_mode_var.get() in {"upgrade", "update"}

    def _on_action_mode_changed(self) -> None:
        if self._upgrade_mode_enabled():
            self.install_warning_label.config(text=self._upgrade_warning_text(), foreground="#2e7d32")
        else:
            self.install_warning_label.config(text=self._install_warning_text(), foreground="#7a1f1f")
        self._sync_devices()
        self._update_device_warnings_and_button()
        self._sync_upgrade_plan()

    def _schedule_local_checksum_refresh(self) -> None:
        self._checksum_job_id += 1
        job_id = self._checksum_job_id
        image_path = self.image_path_var.get().strip()
        if image_path != self.controller.state.verified_image_path:
            self.controller.state.verified_image_path = ""
            self.controller.state.verified_image_sha256 = ""
        if not image_path:
            self.local_checksum_var.set("")
            return

        def worker() -> None:
            checksum = self._compute_file_sha256(image_path)
            self._queue_ui(lambda: self._apply_local_checksum(job_id, checksum))

        self.controller.executor.submit(worker)

    def _apply_local_checksum(self, job_id: int, checksum: str) -> None:
        if job_id != self._checksum_job_id:
            return
        self.local_checksum_var.set(checksum)

    def _compute_file_sha256(self, path: str) -> str:
        candidate = Path(path)
        if not candidate.exists() or not candidate.is_file():
            return ""
        try:
            return sha256_file(candidate)
        except OSError:
            return ""

    def _fetch_suggested_checksum(self, url: str) -> None:
        if not url:
            self._queue_ui(lambda: self.suggested_checksum_var.set(""))
            return
        try:
            with urlopen(url, timeout=20) as response:  # noqa: S310 - remote metadata chosen by user context
                content = response.read().decode("utf-8", errors="replace")
            token = content.strip().split()[0] if content.strip() else ""
            checksum = token if len(token) >= 32 else ""
        except Exception:
            checksum = ""
        self._queue_ui(partial(self.suggested_checksum_var.set, checksum))

    def _add_readonly_row(self, parent: tk.Misc, row: int, label: str, variable: tk.StringVar) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="nw", pady=(0, 6), padx=(0, 8))
        entry = ttk.Entry(parent, textvariable=variable)
        entry.grid(row=row, column=1, sticky="ew", pady=(0, 6))
        entry.configure(state="readonly")

    def _browse_image(self) -> None:
        path = filedialog.askopenfilename(
            title="Choose a Tails image",
            filetypes=[("Disk images", "*.img *.iso"), ("All files", "*.*")],
        )
        if path:
            self.image_path_var.set(path)

    def _on_version_selected(self, _event: tk.Event | None = None) -> None:
        selection = self.versions_list.curselection()  # type: ignore[no-untyped-call]
        if not selection:
            return
        version = self.versions_list.get(selection[0])
        self.controller.select_version(version)
        self._sync_selected_version_fields()

    def _on_version_key_nav(self, _event: tk.Event | None = None) -> None:
        """Handle keyboard navigation in versions list."""
        selection = self.versions_list.curselection()  # type: ignore[no-untyped-call]
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
        elif mode_str == "attached":
            self.controller.set_source_mode(SourceMode.ATTACHED)
            self.action_mode_var.set("upgrade")
        elif mode_str == "local":
            self.controller.set_source_mode(SourceMode.LOCAL)
        elif mode_str == "remote":
            self.controller.set_source_mode(SourceMode.REMOTE)

    def _validate_attached_live_source(self) -> None:
        device_path = self.attached_source_device_var.get().strip()
        mount_point = self.attached_source_mount_var.get().strip()
        if not device_path or not mount_point:
            messagebox.showerror("Missing attached source", "Enter both the source device and its mount point.")
            return
        try:
            source = self.controller.set_attached_live_source(device_path, mount_point)
            self.attached_source_status_var.set(f"Validated attached Tails {source.version or 'unknown'} from {device_path}")
            self.action_mode_var.set("upgrade")
            self._sync_devices()
        except Exception as error:  # noqa: BLE001 - visible UI feedback
            self.attached_source_status_var.set(f"Attached source validation failed: {error}")
            messagebox.showerror("Attached source validation failed", str(error))

    @staticmethod
    def _require_https_remote_url(url: str, purpose: str) -> None:
        if urlparse(url).scheme.casefold() != "https":
            raise ValueError(f"{purpose} URL must use HTTPS: {url}")

    def _start_remote_download(self) -> None:
        img_url = self.controller.state.selected_image_url.strip()
        checksum_url = self.controller.state.selected_checksum_url.strip()
        if not img_url:
            self.controller.state.status_message = "No remote IMG URL selected."
            return
        if not checksum_url:
            self.controller.state.status_message = "Remote IMG has no published SHA-256 checksum; refusing download."
            return
        try:
            self._require_https_remote_url(img_url, "Remote IMG")
            self._require_https_remote_url(checksum_url, "Remote checksum")
        except ValueError as error:
            self.controller.state.status_message = str(error)
            return

        filename = Path(urlparse(img_url).path).name or f"tails-{self.controller.state.selected_version}.img"
        self.controller.state.verified_image_path = ""
        self.controller.state.verified_image_sha256 = ""
        self._show_remote_download_started(filename)
        self.controller.executor.submit(
            self._download_selected_remote_image,
            img_url,
            checksum_url,
            filename,
        )

    def _read_remote_checksum(self, checksum_url: str) -> str:
        self._require_https_remote_url(checksum_url, "Remote checksum")
        if should_use_torify():
            checksum_text = fetch_text_torified(checksum_url, 30)
            if len(checksum_text.encode("utf-8")) > 256 * 1024:
                raise ValueError("Published checksum response is unexpectedly large")
            return checksum_text

        with urlopen(checksum_url, timeout=30) as checksum_response:  # noqa: S310 - HTTPS enforced
            effective_url = str(checksum_response.geturl()) if hasattr(checksum_response, "geturl") else checksum_url
            self._require_https_remote_url(effective_url, "Remote checksum redirect")
            checksum_payload: bytes = checksum_response.read(256 * 1024 + 1)
        if len(checksum_payload) > 256 * 1024:
            raise ValueError("Published checksum response is unexpectedly large")
        return checksum_payload.decode("utf-8", errors="strict")

    def _download_remote_image_to(self, img_url: str, partial_path: Path) -> None:
        self._require_https_remote_url(img_url, "Remote IMG")
        if should_use_torify():
            subprocess.run(
                [
                    "torify",
                    "curl",
                    "-fL",
                    "--proto",
                    "=https",
                    "--proto-redir",
                    "=https",
                    "--output",
                    str(partial_path),
                    img_url,
                ],
                check=True,
                text=True,
                capture_output=True,
            )
            return

        with (
            urlopen(img_url, timeout=60) as response,  # noqa: S310 - HTTPS enforced
            partial_path.open("wb") as out,
        ):
            effective_url = str(response.geturl()) if hasattr(response, "geturl") else img_url
            self._require_https_remote_url(effective_url, "Remote IMG redirect")
            while chunk := response.read(1024 * 1024):
                out.write(chunk)

    def _download_selected_remote_image(self, img_url: str, checksum_url: str, filename: str) -> None:
        cache_dir = Path.home() / ".cache" / "tails-cloner-clone" / "downloads"
        cache_dir.mkdir(parents=True, exist_ok=True)
        target_path = cache_dir / filename
        partial_path: Path | None = None

        try:
            checksum_text = self._read_remote_checksum(checksum_url)
            expected_digest = parse_sha256_text(checksum_text, expected_filename=filename)

            with NamedTemporaryFile(
                mode="wb",
                prefix=f".{filename}.",
                suffix=".part",
                dir=cache_dir,
                delete=False,
            ) as out:
                partial_path = Path(out.name)
            self._download_remote_image_to(img_url, partial_path)

            actual_digest = verify_sha256(partial_path, expected_digest)
            partial_path.replace(target_path)
            partial_path = None
            self._queue_ui(
                partial(self._apply_remote_download_success, target_path, filename, actual_digest)
            )
        except Exception as error:  # noqa: BLE001 - visible UI feedback
            if partial_path is not None:
                partial_path.unlink(missing_ok=True)
            self._queue_ui(partial(self._apply_remote_download_failure, str(error)))

    def _show_remote_download_started(self, filename: str) -> None:
        self.remote_state_var.set("downloading; verification pending")
        self.source_status_var.set(f"Downloading and SHA-256 verifying {filename}…")
        self.controller.state.status_message = f"Downloading and verifying {filename}…"

    def _apply_remote_download_success(self, target_path: Path, filename: str, digest: str) -> None:
        self.image_path_var.set(str(target_path))
        self.controller.set_source_mode(SourceMode.LOCAL)
        self.source_mode_var.set("local")
        self.remote_state_var.set("SHA-256 verified")
        self.suggested_local_path_var.set(str(target_path))
        self.local_checksum_var.set(digest)
        self.controller.state.verified_image_path = str(target_path)
        self.controller.state.verified_image_sha256 = digest
        self.source_status_var.set(f"Downloaded and verified to {target_path}")
        self.controller.state.status_message = f"Downloaded and SHA-256 verified {filename}. Using local file source."

    def _apply_remote_download_failure(self, message: str) -> None:
        self.remote_state_var.set("download or verification failed")
        self.source_status_var.set(f"Download/verification failed: {message}")
        self.controller.state.status_message = f"Download/verification failed: {message}"

    def _on_device_selected(self, _event: tk.Event | None = None) -> None:
        self._update_device_warnings_and_button()

    def _confirm_and_clone(self) -> None:
        selected_device_name = self.device_var.get()
        device_path = self._device_labels.get(selected_device_name, "")

        # Get image path or None if using running Tails
        image_path = None
        if self.controller.state.source_mode not in {SourceMode.RUNNING, SourceMode.ATTACHED}:
            image_path = self.image_path_var.get().strip()
            if not image_path:
                if self.controller.state.source_mode == SourceMode.REMOTE:
                    messagebox.showerror("Missing image", "Download the selected remote IMG first, then clone the local file.")
                else:
                    messagebox.showerror("Missing image", "Choose a local ISO or IMG file before cloning.")
                return
        elif self.controller.state.source_mode == SourceMode.ATTACHED and not self.controller.state.attached_live_source_device:
            messagebox.showerror("Missing attached source", "Validate an attached Tails live source before upgrading.")
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

        if device is None:
            messagebox.showerror("Missing device", "Choose a device before cloning.")
            return

        operation = OperationKind.UPGRADE if self._upgrade_mode_enabled() else OperationKind.INSTALL
        plan = self.controller.plan_target_operation(operation, device_path, image_path)
        if plan.blocking_errors:
            messagebox.showerror("Device cannot be selected", "\n".join(plan.blocking_errors))
            self._update_device_warnings_and_button()
            return

        confirmed = messagebox.askyesno(plan.confirmation_title, plan.confirmation_message, icon=messagebox.WARNING)
        if not confirmed:
            return
        is_upgrade = plan.operation == OperationKind.UPGRADE
        operation_label = "upgrade" if is_upgrade else "installation"
        self._write_in_progress = True
        self._show_clone_progress(True, operation_label=operation_label)
        self.clone_button.config(state="disabled")
        self.progress_bar.start(10)
        self.controller.executor.submit(self._run_write_operation, image_path, device_path, is_upgrade)

    def _run_write_operation(self, image_path: str | None, device_path: str, is_upgrade: bool) -> None:
        operation_label = "upgrade" if is_upgrade else "installation"
        complete_title = "Upgrade complete" if is_upgrade else "Installation complete"
        complete_message = (
            f"Tails has been upgraded on {device_path}. Existing Persistent Storage, if present, was preserved."
            if is_upgrade
            else f"Tails has been successfully installed to {device_path}."
        )

        try:
            def on_progress(message: str) -> None:
                self._queue_ui(partial(self._set_progress_text, message))

            if is_upgrade:
                self.controller.upgrade_selected_image(image_path, device_path, progress_callback=on_progress)
            else:
                self.controller.clone_selected_image(image_path, device_path, progress_callback=on_progress)

            self._queue_ui(lambda: self._finish_write_success(complete_title, complete_message))
        except Exception as error:  # noqa: BLE001 - converted into visible UI feedback
            error_message = str(error)
            self.controller.state.status_message = f"{operation_label.title()} failed: {error_message}"
            self._queue_ui(partial(self._finish_write_failure, operation_label, error_message))

    def _set_progress_text(self, message: str) -> None:
        self.progress_label.configure(text=message)

    def _finish_write_success(self, title: str, message: str) -> None:
        self._write_in_progress = False
        self._show_clone_progress(False)
        self.clone_button.config(state="normal")
        messagebox.showinfo(title, message)

    def _finish_write_failure(self, operation_label: str, message: str) -> None:
        self._write_in_progress = False
        self._show_clone_progress(False)
        self.clone_button.config(state="normal")
        messagebox.showerror(f"{operation_label.title()} failed", message)

    def _show_clone_progress(self, show: bool, operation_label: str = "operation") -> None:
        if show:
            self.progress_frame.grid()
            self.progress_label.config(text=f"Starting {operation_label}...")
        else:
            self.progress_bar.stop()
            self.progress_frame.grid_remove()
            self.progress_label.config(text="")

    def _queue_ui(self, callback: Callable[[], None]) -> None:
        self._ui_events.put(callback)

    def _drain_ui_events(self) -> None:
        while True:
            try:
                callback = self._ui_events.get_nowait()
            except Empty:
                return
            callback()

    def _sync_state(self) -> None:
        self._drain_ui_events()
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

        # Keep the running-source option available whenever startup detection
        # found a running Tails system, even after the user temporarily selects
        # another source mode.
        current_mode = self.controller.state.source_mode
        if current_mode == SourceMode.RUNNING:
            self.source_mode_var.set("running")
        self.source_running_radio.configure(
            state="normal" if self.controller.state.running_tails_available else "disabled"
        )
        if current_mode == SourceMode.ATTACHED:
            self.source_mode_var.set("attached")
        elif current_mode == SourceMode.LOCAL:
            self.source_mode_var.set("local")
        elif current_mode == SourceMode.REMOTE:
            self.source_mode_var.set("remote")

        attached_widgets: list[tk.Widget] = [
            self.attached_source_device_entry,
            self.attached_source_mount_entry,
            self.attached_source_validate_button,
        ]
        attached_state = "normal" if current_mode == SourceMode.ATTACHED else "disabled"
        for widget in attached_widgets:
            widget.configure({"state": attached_state})

        if current_mode == SourceMode.LOCAL:
            self.image_entry.configure(state="normal")
            self.browse_button.configure(state="normal")
            self.download_button.configure(state="disabled")
        elif current_mode == SourceMode.REMOTE:
            self.image_entry.configure(state="disabled")
            self.browse_button.configure(state="disabled")
            self.download_button.configure(state="normal")
        else:
            self.image_entry.configure(state="disabled")
            self.browse_button.configure(state="disabled")
            self.download_button.configure(state="disabled")

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
        # Keep source/running devices visible so the user understands why they
        # cannot be selected; the controller marks them disabled and refuses
        # them again at operation time.
        self.controller.annotate_device_selection_state()
        devices = self.controller.state.devices

        if self._upgrade_mode_enabled():
            devices = [d for d in devices if d.has_tails or d.disabled_reason]
        labels = {device.pretty_name: device.path for device in devices}
        snapshot = tuple(labels)
        if snapshot != self._last_devices_snapshot:
            self._last_devices_snapshot = snapshot
            self._device_labels = labels
            values = list(labels)

            # Keep current selection if it still exists
            current_selection = self.device_var.get()
            self.device_combo["values"] = values
            if values and current_selection not in labels:
                self.device_var.set(values[0])
            elif not values:
                self.device_var.set("")
        else:
            # Device labels can remain stable while operation/source semantics
            # change, so keep the path map current and always recompute the plan.
            self._device_labels = labels

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

        operation = OperationKind.UPGRADE if self._upgrade_mode_enabled() else OperationKind.INSTALL
        image_path = None
        if self.controller.state.source_mode not in {SourceMode.RUNNING, SourceMode.ATTACHED}:
            image_path = self.image_path_var.get().strip() or None
        plan = self.controller.plan_target_operation(operation, device.path, image_path)

        messages = plan.blocking_errors + plan.warnings
        self.device_warning_label.config(text="\n".join(messages))
        self.clone_button.config(text=plan.action_label, state="normal" if plan.would_write else "disabled")
        foreground = plan.status_foreground or "#2e7d32"
        self.device_status_label.config(text=plan.status_message, foreground=foreground)

    def _sync_selected_version_fields(self) -> None:
        version = self.controller.state.selected_version
        if version == self._last_selected_version and self.selected_iso_url_var.get() == self.controller.state.selected_iso_url:
            return
        self._last_selected_version = version
        self.selected_version_var.set(version)
        self.selected_iso_url_var.set(self.controller.state.selected_iso_url)
        self.selected_image_url_var.set(self.controller.state.selected_image_url)
        self.selected_signature_url_var.set(self.controller.state.selected_signature_url)
        checksum_url = self.controller.state.selected_checksum_url.strip()
        self.controller.executor.submit(self._fetch_suggested_checksum, checksum_url)

    def _sync_loading_labels(self) -> None:
        version_label = "Refreshing remote versions…" if self.controller.state.versions_loading else "Remote versions idle"
        self.version_status_label.config(text=version_label)
        if self.controller.state.devices_loading:
            self.device_status_label.config(text="Scanning devices…", foreground="#666666")
        elif not self.controller.state.devices:
            self.device_status_label.config(text="No devices detected.", foreground="#666666")

    def _sync_upgrade_plan(self) -> None:
        selected_name = self.device_var.get().strip()
        target_device = self._device_labels.get(selected_name, selected_name) if selected_name else "not selected"

        if self.controller.state.source_mode == SourceMode.RUNNING:
            running_version = self.controller.state.running_tails_version or "unknown"
            source_desc = f"running Tails {running_version} (live medium)"
        elif self.controller.state.source_mode == SourceMode.ATTACHED:
            attached_version = self.controller.state.attached_live_source_version or "unknown"
            attached_device = self.controller.state.attached_live_source_device or "not selected"
            source_desc = f"attached Tails {attached_version} from {attached_device}"
        elif self.controller.state.source_mode == SourceMode.REMOTE:
            source_desc = f"remote {self.controller.state.selected_version or 'version'} (download required)"
        else:
            image_path = self.image_path_var.get().strip()
            source_desc = Path(image_path).name if image_path else "local ISO/IMG not selected"

        action = self.clone_button.cget("text")
        if action == "Upgrade":
            action_desc = "Upgrade existing Tails; existing Persistent Storage preserved if present"
        elif action == "Reinstall (delete all data)":
            action_desc = "Reinstall; deletes all data including Persistent Storage"
        else:
            action_desc = "Install; deletes all data on the target"

        self.upgrade_plan_var.set(f"Source: {source_desc}\nTarget: {target_device}\nAction: {action_desc}")

    def _on_close(self) -> None:
        if self._write_in_progress:
            messagebox.showwarning(
                "Write in progress",
                "A Tails write operation is still running. Keep this window open until it completes.",
            )
            return
        self.controller.shutdown()
        self.destroy()
