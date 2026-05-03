# Changelog

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
