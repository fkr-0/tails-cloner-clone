# Changelog

## v0.4.1 - 2026-05-03

- Introduced a tabbed UI workflow:
  - `Source` tab focused on source selection, source details, remote list, and remote metadata/checksum fields.
  - `Write` tab focused on target device actions with install/update mode selection and an experimental panel stub.
- Added source details panel that adapts to `remote`, `running`, and `local` source modes.
- Added remote source provenance block with URL and last refresh timestamp.
- Added editable suggested checksum field and automatic local file SHA256 computation for selected local images.
- Added remote download state and suggested local path tracking in UI.
- Added install/update mode selector; update mode filters device list to Tails-installed targets.
- Updated app header:
  - title now `Tails Cloner Clone` with icon next to headline.
  - subtitle replaced with clickable `https://downloads.tails.net` reference and hover highlight behavior.
- Improved dark-mode readability for entry/combobox/read-only field text and button hover foreground handling.
- Ensured theme toggle icon is initialized correctly at startup (`☀` for dark mode, `🌙` for light mode).
- Standardized WM class intent to `tails-cloner-clone` with robust Tk-compatible assignment path.
- Enhanced icon styling with stronger rainbow accents and regenerated PNG icon assets from SVG.

## v0.3.3 - 2026-05-03

- Fixed AppImage startup regression caused by unsupported `wm_class` method usage in some Tk builds.
- Switched WM class assignment to a Tcl-level `wm class` call with safe fallback handling.
- Added regression tests for window class assignment success and TclError fallback behavior.

## v0.3.2 - 2026-05-03

- Reworked source selection UX to clearly separate remote download, running live system clone, and local image file flows.
- Added remote image download-to-local handoff and improved source status messaging.
- Replaced dark-mode checkbox with header icon toggle (`🌙`/`☀`) and added header exit (`✕`) action.
- Fixed theme hover/readability issues for controls in dark mode.
- Hardened remote catalog TLS fallback to handle wrapped certificate verification failures.
- Updated device scan copy to use generic device wording (no inaccurate “removable” claim in status output).
- Improved desktop/app identity integration by setting `wm_class` to `tails-cloner-clone` and setting window icon via bundled PNG assets.
- Expanded AppImage icon packaging: `.DirIcon`, hicolor PNG sizes, and `StartupWMClass` in desktop entry.
- Updated project icon styling (rainbow text bars) and added README heading icon.
- Added/updated tests for TLS fallback behavior and controller device-status wording.
- Added checksum-first feature spec for future implementation in `docs/plans/2026-05-03-checksum-first-image-selection-spec.md`.

## v0.3.1 - 2026-05-03

- Defaulted the UI to dark mode and added a dark-mode toggle in the header.
- Added SSL-certificate verification fallback for remote version refresh in restricted CA environments.
- Renamed project/build/release naming consistently to `tails-cloner-clone`.

## v0.2.1 - 2026-04-30

- Fixed AppImage UI text artifacts where `&` mnemonic markers were shown literally in labels and buttons.
- Improved UI theme consistency to avoid mixed light/dark rendering and inconsistent widget styling.
- Hardened remote version catalog HTTPS fetching with explicit SSL context handling and CA loading fallback.
- Made running-live upgrade flow explicit in UI with source/target/action summary.
- Improved source device safety by excluding the entire currently-booted source disk family from target selection.
- Added coverage tests for remote index TLS handling and parent-disk path normalization.
