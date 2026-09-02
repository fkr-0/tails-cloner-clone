from __future__ import annotations

import unittest

from tails_cloner.theme import (
    DARK_PALETTE,
    LIGHT_PALETTE,
    configure_canvas_widget,
    configure_combobox_popdown,
    configure_listbox_widget,
    configure_text_widget,
    configure_tk_option_database,
    configure_ttk_style,
    palette_for,
)


def _relative_luminance(color: str) -> float:
    channels = [int(color[index : index + 2], 16) / 255 for index in (1, 3, 5)]

    def linearize(value: float) -> float:
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4

    red, green, blue = (linearize(channel) for channel in channels)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _contrast(first: str, second: str) -> float:
    lighter, darker = sorted((_relative_luminance(first), _relative_luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


class _FakeStyle:
    def __init__(self) -> None:
        self.configured: dict[str, dict[str, object]] = {}
        self.mapped: dict[str, dict[str, object]] = {}

    def configure(self, name: str, **kwargs: object) -> None:
        self.configured.setdefault(name, {}).update(kwargs)

    def map(self, name: str, **kwargs: object) -> None:
        self.mapped.setdefault(name, {}).update(kwargs)


class _FakeRoot:
    def __init__(self) -> None:
        self.options: dict[str, str] = {}

    def option_add(self, pattern: str, value: str, priority: int | str | None = None) -> None:
        del priority
        self.options[pattern] = value


class _FakeWidget:
    def __init__(self) -> None:
        self.kwargs: dict[str, object] = {}

    def configure(self, **kwargs: object) -> None:
        self.kwargs.update(kwargs)


class ThemePaletteTests(unittest.TestCase):
    def test_palette_selection_is_deterministic(self) -> None:
        self.assertIs(palette_for(True), DARK_PALETTE)
        self.assertIs(palette_for(False), LIGHT_PALETTE)

    def test_reader_facing_text_colors_have_accessible_contrast(self) -> None:
        for palette in (DARK_PALETTE, LIGHT_PALETTE):
            with self.subTest(background=palette.background):
                for foreground in (
                    palette.foreground,
                    palette.muted_foreground,
                    palette.danger,
                    palette.success,
                    palette.info,
                    palette.link,
                    palette.link_hover,
                ):
                    self.assertGreaterEqual(_contrast(foreground, palette.background), 4.5)
                self.assertGreaterEqual(_contrast(palette.selected_foreground, palette.selected_background), 4.5)
                self.assertGreaterEqual(_contrast(palette.selected_foreground, palette.accent_hover_background), 4.5)
                self.assertGreaterEqual(_contrast(palette.selected_foreground, palette.accent_pressed_background), 4.5)
                self.assertGreaterEqual(_contrast(palette.danger_foreground, palette.danger_background), 4.5)
                self.assertGreaterEqual(_contrast(palette.danger_foreground, palette.danger_hover_background), 4.5)
                self.assertGreaterEqual(_contrast(palette.danger_foreground, palette.danger_pressed_background), 4.5)
                self.assertGreaterEqual(_contrast(palette.disabled_foreground, palette.disabled_background), 3.0)
                self.assertGreaterEqual(_contrast(palette.focus, palette.background), 3.0)
                self.assertGreaterEqual(_contrast(palette.border, palette.background), 3.0)
                self.assertGreaterEqual(_contrast(palette.selected_background, palette.background), 3.0)

    def test_style_configuration_covers_interactive_control_states(self) -> None:
        style = _FakeStyle()
        configure_ttk_style(style, DARK_PALETTE)  # type: ignore[arg-type]

        for control in ("TButton", "TRadiobutton", "TCheckbutton", "TEntry", "TCombobox", "TNotebook.Tab"):
            self.assertIn(control, style.configured)
            self.assertIn(control, style.mapped)

        self.assertIn("indicatorbackground", style.mapped["TRadiobutton"])
        self.assertIn("indicatorbackground", style.mapped["TCheckbutton"])
        self.assertIn("fieldbackground", style.mapped["TCombobox"])
        self.assertIn("arrowcolor", style.mapped["TCombobox"])
        self.assertIn("bordercolor", style.mapped["TEntry"])
        self.assertIn("Danger.TButton", style.configured)
        self.assertIn("Accent.TButton", style.configured)
        self.assertIn("Vertical.TScrollbar", style.configured)
        self.assertEqual(
            style.mapped["Accent.TButton"]["background"],
            [
                ("disabled", DARK_PALETTE.disabled_background),
                ("pressed", DARK_PALETTE.accent_pressed_background),
                ("active", DARK_PALETTE.accent_hover_background),
            ],
        )
        self.assertEqual(
            style.mapped["Danger.TButton"]["background"],
            [
                ("disabled", DARK_PALETTE.disabled_background),
                ("pressed", DARK_PALETTE.danger_pressed_background),
                ("active", DARK_PALETTE.danger_hover_background),
            ],
        )

    def test_combobox_popdown_and_listbox_options_follow_palette(self) -> None:
        root = _FakeRoot()
        configure_tk_option_database(root, DARK_PALETTE)

        self.assertEqual(root.options["*TCombobox*Listbox.background"], DARK_PALETTE.field_background)
        self.assertEqual(root.options["*TCombobox*Listbox.foreground"], DARK_PALETTE.foreground)
        self.assertEqual(root.options["*TCombobox*Listbox.selectBackground"], DARK_PALETTE.selected_background)
        self.assertEqual(root.options["*Label.Foreground"], DARK_PALETTE.foreground)
        self.assertEqual(root.options["*Listbox.DisabledForeground"], DARK_PALETTE.disabled_foreground)
        self.assertEqual(root.options["*Text.Background"], DARK_PALETTE.field_background)
        self.assertEqual(root.options["*Text.HighlightColor"], DARK_PALETTE.focus)
        self.assertEqual(root.options["*Canvas.Background"], DARK_PALETTE.background)

    def test_existing_classic_widgets_are_rethemed_on_runtime_toggle(self) -> None:
        listbox = _FakeWidget()
        text = _FakeWidget()
        canvas = _FakeWidget()

        configure_listbox_widget(listbox, DARK_PALETTE)  # type: ignore[arg-type]
        configure_text_widget(text, DARK_PALETTE)  # type: ignore[arg-type]
        configure_canvas_widget(canvas, DARK_PALETTE)  # type: ignore[arg-type]

        self.assertEqual(listbox.kwargs["background"], DARK_PALETTE.field_background)
        self.assertEqual(listbox.kwargs["highlightcolor"], DARK_PALETTE.focus)
        self.assertEqual(text.kwargs["foreground"], DARK_PALETTE.foreground)
        self.assertEqual(text.kwargs["selectbackground"], DARK_PALETTE.selected_background)
        self.assertEqual(canvas.kwargs["background"], DARK_PALETTE.background)

    def test_combobox_popdown_helper_is_available_for_runtime_retheme(self) -> None:
        self.assertTrue(callable(configure_combobox_popdown))


if __name__ == "__main__":
    unittest.main()
