from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from tkinter import ttk
from typing import Literal, Protocol


@dataclass(frozen=True, slots=True)
class ThemePalette:
    background: str
    surface: str
    field_background: str
    foreground: str
    muted_foreground: str
    disabled_foreground: str
    disabled_background: str
    border: str
    hover_background: str
    pressed_background: str
    selected_background: str
    accent_hover_background: str
    accent_pressed_background: str
    selected_foreground: str
    focus: str
    link: str
    link_hover: str
    danger: str
    danger_background: str
    danger_hover_background: str
    danger_pressed_background: str
    danger_foreground: str
    success: str
    info: str
    separator: str
    progress_trough: str
    progress_bar: str


DARK_PALETTE = ThemePalette(
    background="#1a1d21",
    surface="#252a31",
    field_background="#2b3038",
    foreground="#f1f3f5",
    muted_foreground="#c2c7ce",
    disabled_foreground="#9299a3",
    disabled_background="#30353c",
    border="#747d88",
    hover_background="#353c46",
    pressed_background="#414a56",
    selected_background="#3f71ad",
    accent_hover_background="#315d91",
    accent_pressed_background="#294e79",
    selected_foreground="#ffffff",
    focus="#8ab4f8",
    link="#8ab4f8",
    link_hover="#ffd166",
    danger="#ffb4ab",
    danger_background="#9f3538",
    danger_hover_background="#b13f43",
    danger_pressed_background="#832b2e",
    danger_foreground="#ffffff",
    success="#81c995",
    info="#8ab4f8",
    separator="#5f6873",
    progress_trough="#30353c",
    progress_bar="#72a7e8",
)

LIGHT_PALETTE = ThemePalette(
    background="#f5f6f7",
    surface="#ffffff",
    field_background="#ffffff",
    foreground="#17191c",
    muted_foreground="#4f5964",
    disabled_foreground="#686f78",
    disabled_background="#e5e8eb",
    border="#707983",
    hover_background="#e8edf3",
    pressed_background="#d8e1eb",
    selected_background="#255f9e",
    accent_hover_background="#1e5189",
    accent_pressed_background="#17426f",
    selected_foreground="#ffffff",
    focus="#1558a6",
    link="#1b5fa7",
    link_hover="#764f00",
    danger="#8c1d22",
    danger_background="#9b272c",
    danger_hover_background="#aa3035",
    danger_pressed_background="#7f1f23",
    danger_foreground="#ffffff",
    success="#1f6b34",
    info="#245f9f",
    separator="#7d858e",
    progress_trough="#dde2e7",
    progress_bar="#2d6ca8",
)


class TkOptionRoot(Protocol):
    def option_add(
        self,
        pattern: str,
        value: str,
        priority: Literal["widgetDefault", "startupFile", "userDefault", "interactive"] | int | None = None,
    ) -> None: ...


def palette_for(dark_mode: bool) -> ThemePalette:
    return DARK_PALETTE if dark_mode else LIGHT_PALETTE


def configure_ttk_style(style: ttk.Style, palette: ThemePalette) -> None:
    """Apply one coherent accessible palette to all ttk controls used by the app."""

    base = {
        "background": palette.background,
        "foreground": palette.foreground,
    }
    style.configure("TFrame", background=palette.background)
    style.configure("TLabel", **base)
    style.configure("TLabelframe", **base, bordercolor=palette.border)
    style.configure("TLabelframe.Label", **base)

    style.configure("Muted.TLabel", **(base | {"foreground": palette.muted_foreground}))
    style.configure("Value.TLabel", **base)
    style.configure("Warning.TLabel", **(base | {"foreground": palette.danger}))
    style.configure("Success.TLabel", **(base | {"foreground": palette.success}))
    style.configure("Info.TLabel", **(base | {"foreground": palette.info}))

    button_options = {
        "background": palette.surface,
        "foreground": palette.foreground,
        "bordercolor": palette.border,
        "lightcolor": palette.border,
        "darkcolor": palette.border,
        "focuscolor": palette.focus,
        "focusthickness": 2,
        "padding": (10, 6),
    }
    style.configure("TButton", **button_options)
    style.map(
        "TButton",
        background=[
            ("disabled", palette.disabled_background),
            ("pressed", palette.pressed_background),
            ("active", palette.hover_background),
        ],
        foreground=[("disabled", palette.disabled_foreground)],
        bordercolor=[("focus", palette.focus), ("active", palette.focus)],
    )
    style.configure(
        "Accent.TButton",
        **(
            button_options
            | {
                "background": palette.selected_background,
                "foreground": palette.selected_foreground,
            }
        ),
    )
    style.map(
        "Accent.TButton",
        background=[
            ("disabled", palette.disabled_background),
            ("pressed", palette.accent_pressed_background),
            ("active", palette.accent_hover_background),
        ],
        foreground=[
            ("disabled", palette.disabled_foreground),
            ("!disabled", palette.selected_foreground),
        ],
        bordercolor=[("focus", palette.focus), ("active", palette.focus)],
    )
    style.configure(
        "Danger.TButton",
        **(
            button_options
            | {
                "background": palette.danger_background,
                "foreground": palette.danger_foreground,
                "bordercolor": palette.danger,
            }
        ),
    )
    style.map(
        "Danger.TButton",
        background=[
            ("disabled", palette.disabled_background),
            ("pressed", palette.danger_pressed_background),
            ("active", palette.danger_hover_background),
        ],
        foreground=[
            ("disabled", palette.disabled_foreground),
            ("!disabled", palette.danger_foreground),
        ],
        bordercolor=[("focus", palette.focus), ("active", palette.focus)],
    )

    for control in ("TRadiobutton", "TCheckbutton"):
        style.configure(
            control,
            **base,
            indicatorbackground=palette.field_background,
            indicatorforeground=palette.selected_foreground,
            upperbordercolor=palette.border,
            lowerbordercolor=palette.border,
            focuscolor=palette.focus,
            focusthickness=2,
        )
        style.map(
            control,
            background=[("active", palette.hover_background)],
            foreground=[("disabled", palette.disabled_foreground)],
            indicatorbackground=[
                ("disabled", palette.disabled_background),
                ("selected", palette.selected_background),
                ("active", palette.hover_background),
                ("!selected", palette.field_background),
            ],
            indicatorforeground=[
                ("disabled", palette.disabled_foreground),
                ("selected", palette.selected_foreground),
            ],
            upperbordercolor=[("focus", palette.focus), ("active", palette.focus)],
            lowerbordercolor=[("focus", palette.focus), ("active", palette.focus)],
        )

    entry_options = {
        "fieldbackground": palette.field_background,
        "foreground": palette.foreground,
        "bordercolor": palette.border,
        "lightcolor": palette.border,
        "insertcolor": palette.foreground,
        "selectbackground": palette.selected_background,
        "selectforeground": palette.selected_foreground,
    }
    style.configure("TEntry", **entry_options)
    style.map(
        "TEntry",
        fieldbackground=[
            ("disabled", palette.disabled_background),
            ("readonly", palette.surface),
        ],
        foreground=[
            ("disabled", palette.disabled_foreground),
            ("readonly", palette.foreground),
        ],
        bordercolor=[("focus", palette.focus)],
        lightcolor=[("focus", palette.focus)],
    )

    style.configure(
        "TCombobox",
        **entry_options,
        background=palette.surface,
        arrowcolor=palette.foreground,
    )
    style.map(
        "TCombobox",
        fieldbackground=[
            ("disabled", palette.disabled_background),
            ("readonly", palette.field_background),
        ],
        foreground=[
            ("disabled", palette.disabled_foreground),
            ("readonly", palette.foreground),
        ],
        background=[("active", palette.hover_background)],
        arrowcolor=[
            ("disabled", palette.disabled_foreground),
            ("!disabled", palette.foreground),
        ],
        bordercolor=[("focus", palette.focus), ("active", palette.focus)],
        lightcolor=[("focus", palette.focus)],
    )

    style.configure("TNotebook", background=palette.background, bordercolor=palette.border)
    style.configure(
        "TNotebook.Tab",
        background=palette.surface,
        foreground=palette.foreground,
        bordercolor=palette.border,
        lightcolor=palette.border,
        darkcolor=palette.border,
        focuscolor=palette.focus,
        focusthickness=2,
        padding=(10, 6),
    )
    style.map(
        "TNotebook.Tab",
        background=[
            ("selected", palette.selected_background),
            ("active", palette.hover_background),
        ],
        foreground=[
            ("selected", palette.selected_foreground),
            ("disabled", palette.disabled_foreground),
        ],
        bordercolor=[("focus", palette.focus)],
    )

    style.configure("TSeparator", background=palette.separator)
    style.configure(
        "Horizontal.TProgressbar",
        troughcolor=palette.progress_trough,
        background=palette.progress_bar,
        bordercolor=palette.border,
        lightcolor=palette.progress_bar,
        darkcolor=palette.progress_bar,
    )

    scrollbar_options = {
        "background": palette.surface,
        "troughcolor": palette.background,
        "bordercolor": palette.border,
        "lightcolor": palette.border,
        "darkcolor": palette.border,
        "arrowcolor": palette.foreground,
    }
    for control in ("Vertical.TScrollbar", "Horizontal.TScrollbar"):
        style.configure(control, **scrollbar_options)
        style.map(
            control,
            background=[
                ("disabled", palette.disabled_background),
                ("pressed", palette.pressed_background),
                ("active", palette.hover_background),
            ],
            arrowcolor=[
                ("disabled", palette.disabled_foreground),
                ("!disabled", palette.foreground),
            ],
        )


def configure_tk_option_database(root: TkOptionRoot, palette: ThemePalette) -> None:
    """Theme classic Tk listboxes, including ttk.Combobox popdown listboxes."""

    options = {
        "*Label.Background": palette.background,
        "*Label.Foreground": palette.foreground,
        "*Label.HighlightBackground": palette.border,
        "*Label.HighlightColor": palette.focus,
        "*Listbox.Background": palette.field_background,
        "*Listbox.Foreground": palette.foreground,
        "*Listbox.SelectBackground": palette.selected_background,
        "*Listbox.SelectForeground": palette.selected_foreground,
        "*Listbox.DisabledForeground": palette.disabled_foreground,
        "*Listbox.HighlightBackground": palette.border,
        "*Listbox.HighlightColor": palette.focus,
        "*TCombobox*Listbox.background": palette.field_background,
        "*TCombobox*Listbox.foreground": palette.foreground,
        "*TCombobox*Listbox.selectBackground": palette.selected_background,
        "*TCombobox*Listbox.selectForeground": palette.selected_foreground,
        "*Text.Background": palette.field_background,
        "*Text.Foreground": palette.foreground,
        "*Text.SelectBackground": palette.selected_background,
        "*Text.SelectForeground": palette.selected_foreground,
        "*Text.InsertBackground": palette.foreground,
        "*Text.HighlightBackground": palette.border,
        "*Text.HighlightColor": palette.focus,
        "*Canvas.Background": palette.background,
        "*Canvas.HighlightBackground": palette.border,
        "*Canvas.HighlightColor": palette.focus,
    }
    for pattern, value in options.items():
        root.option_add(pattern, value)


def configure_listbox_widget(widget: tk.Listbox, palette: ThemePalette) -> None:
    widget.configure(
        background=palette.field_background,
        foreground=palette.foreground,
        selectbackground=palette.selected_background,
        selectforeground=palette.selected_foreground,
        disabledforeground=palette.disabled_foreground,
        highlightbackground=palette.border,
        highlightcolor=palette.focus,
        highlightthickness=2,
    )


def configure_combobox_popdown(widget: ttk.Combobox, palette: ThemePalette) -> None:
    """Retheme an existing ttk combobox popdown, including after a runtime toggle."""

    try:
        popdown = str(widget.tk.call("ttk::combobox::PopdownWindow", widget))
        listbox = f"{popdown}.f.l"
        widget.tk.call(
            listbox,
            "configure",
            "-background",
            palette.field_background,
            "-foreground",
            palette.foreground,
            "-selectbackground",
            palette.selected_background,
            "-selectforeground",
            palette.selected_foreground,
            "-highlightbackground",
            palette.border,
            "-highlightcolor",
            palette.focus,
        )
    except tk.TclError:
        return


def configure_text_widget(widget: tk.Text, palette: ThemePalette) -> None:
    widget.configure(
        background=palette.field_background,
        foreground=palette.foreground,
        selectbackground=palette.selected_background,
        selectforeground=palette.selected_foreground,
        insertbackground=palette.foreground,
        highlightbackground=palette.border,
        highlightcolor=palette.focus,
        highlightthickness=2,
    )


def configure_canvas_widget(widget: tk.Canvas, palette: ThemePalette) -> None:
    widget.configure(background=palette.background)
