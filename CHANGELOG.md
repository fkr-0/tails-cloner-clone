# Changelog

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
