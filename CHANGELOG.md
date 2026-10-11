# Changelog

## Unreleased

## v0.5.5 - 2026-10-12

### Review Fixes: Safety and Reliability

- Never delete files on a still-mounted boot partition: the optional boot-loader reordering now uses a plain scratch mount point instead of a recursively cleaned temporary directory, keeps the directory when `umount` fails, reports it, and no longer lets an unmount failure mask the original error.
- Treat a whole-disk filesystem as part of the target in install preparation: its mount is now unmounted before `dd`, and a whole disk mounted as the running system (or live medium) is refused, matching how partitions were already handled.
- Re-verify a previously verified image before the final device re-enumeration instead of after it, so a slow multi-GB hash no longer sits between the last hot-swap identity check and the write boundary.
- Surface the failing command's stderr (for example a busy `umount`) for install/upgrade failures instead of a bare exit status, and map those failures to the CLI's install/upgrade failure exit codes rather than the internal-error code.
- Fix whole-device install preparation reading a human-readable lsblk size (`14.9G`) as an integer: request `--bytes` and report an unreadable size as a normal failure instead of an internal error.
- Report oversized signature/metadata responses and HTTPS-downgrade redirects during downloads as download failures instead of leaking raw `ValueError`s.
- A failed loop-device detach during cleanup can no longer replace the real upgrade error with a different exception.
- The GUI's suggested-checksum preview now uses the shared Tor-aware, HTTPS-only, size-limited fetch (it previously used a direct `urlopen`, bypassing Tor routing), matches the checksum entry to the selected image filename, and ignores responses for a version the user has since left.

### Review Fixes: Interface and UX

- Show a determinate progress bar with a percentage for whole-device installs and stream `dd` progress for partition upgrades (previously silent for the whole copy); GUI progress events now carry `bytes_written` like the CLI's.
- Put the write form (target, plan, action button) at the top of the Write tab; the experimental boot-loader options sit below it and stay collapsed until enabled, so the primary action no longer starts below the fold.
- Destructive confirmation dialogs now default to "No"; `Esc` no longer quits the application (use `Ctrl+Q` or Close); starting a write is refused while a verified download is running.
- Wrapped labels reflow with the window width instead of using fixed pixel wrap lengths; add a "Continue to Write" action on the Source tab; label the version-catalog URL; fix the "Inofficial" typo and replace the install warning's flippant wording with a plain instruction.
- Keep the action warning style in sync after validating an attached live source, and drop a duplicate version-list key handler.

### Architecture

- Add `tails_cloner.blockdev` as the single home of the protected-mount policy, lsblk tree walking, streamed command execution, `dd` progress parsing, and command-failure formatting, replacing three copy-pasted implementations in discovery, install, and upgrade code.
- Collapse the controller's three near-identical write paths onto shared progress-relay, failure-translation, and live-source-upgrade helpers while preserving all status strings.

### First-class AppImage CLI

- Expand the AppImage/`tails-cloner-clone` operator surface from read-only seeds into catalog/version inspection, verified downloads, current-system/status inspection, rich device inspection, install/upgrade planning, and destructive install/upgrade execution through the same controller/planner/creator/upgrader code paths as the GUI.
- Require explicit noninteractive destructive acknowledgement with both `--yes` and the target's stable confirmation token; refuse CLI execution when the target lacks a stable WWN, serial, or by-id identity, while preserving immediate pre-write hardware revalidation and protected running/source-system refusal.
- Reuse one shared verified-download service in GUI and CLI with HTTPS-only assets/redirects, Tor-aware transfer, bounded metadata, SHA-256 plus pinned OpenPGP verification, atomic promotion, and freshly verified reuse of already-present files.
- Add running-Tails and attached-live-source CLI upgrades while preserving source/target anti-aliasing, read-only source semantics, partition-only upgrade writes, persistence preservation, post-write verification, and v0.5.4 loop lifecycle hardening.

### Automation Contract and Qualification

- Define the versioned `tails-cloner-cli/v1` JSON/JSONL event contract with stable discovery/resolution/verification/warning/progress/write-boundary/completion/failure kinds, deterministic final result objects, optional same-schema audit logs, and documented exit-code taxonomy.
- Preserve no-argument GUI startup while forwarding explicit CLI arguments unchanged through the launcher/AppImage and retaining packaged signing-key resources.
- Make the launcher boundary exact: only empty argv starts the GUI; every nonempty argv is dispatched unchanged to the CLI, so unknown and legacy GUI-looking options cannot enter a hidden second GUI parser.
- Give the no-argument GUI the same stable schema-v1 JSONL observability on stdout, including startup, discovery/state changes, verified download progress/verification, write boundary, install/upgrade progress, completion, and failure events.
- Add contract/regression tests for output, downloads, stable target acknowledgement, current-system refusal, catalog/local/running/attached source modes, fake-backed destructive install/upgrade paths, and built-AppImage CLI smoke coverage.

## v0.5.4 - 2026-09-02

### Upgrade and Device Safety

- Make image attachment robust when `udevadm settle` fails or partition nodes appear late: poll specifically for the loop-device partitions after read-only `losetup --partscan`, continue when the required state is visible, detach cleanly on failure, and report a bounded diagnostic when it never appears.
- Identify the block device backing the currently running system from `/` and Tails live-mount semantics, expose an explicit `Currently running Tails` / `Running system` status in device data and UI labels, and refuse it as a write target.

### UI and Accessibility

- Make the Source and Write views vertically scrollable so primary actions remain reachable on ordinary laptop/desktop heights; reveal focused controls automatically and route mouse-wheel scrolling without stealing interaction from list/text widgets.
- Replace the single-line write status with a scrollable progress history that supports keyboard navigation, preserves completion/failure output, and only auto-follows while the user is already at the bottom.
- Centralize light/dark theming with semantic colors and improve contrast and interaction states for combobox fields and dropdown items, radio buttons, checkboxes, buttons, focus/disabled/selected states, scrollbars, progress/status text, warnings, native Tk list/text widgets, and destructive actions.
- Make the downloads link keyboard-focusable and activatable, and replace symbol-only theme/close controls with explicit labels.

### Qualification

- Expand regression coverage for delayed loop-partition discovery, running-system device classification, responsive scrolling/progress behavior, and theme contrast/control styling.
- Requalify the combined release candidate with the full pytest suite, Ruff, mypy, diff checks, runtime dark/light GUI smoke, and AppImage build/smoke gates.

## v0.5.3 - 2026-09-02

### Safety and Upgrade Hardening

- Revalidate source and target hardware identity immediately before destructive operations, prefer stable `/dev/disk/by-id` paths, and show the selected hardware identity in confirmation prompts.
- Protect the running/system disk at both discovery and write boundaries, including `/home` and Tails live-media mount paths, while keeping intentional internal-disk targets supported with explicit warnings and confirmation.
- Preserve Tails persistence during upgrades by writing only the system partition and validating that the persistence partition remains unchanged.
- Require HTTPS for remote release assets, verify downloaded images with SHA256 plus the pinned Tails OpenPGP identity, and re-check verified image hashes immediately before writing.
- Refresh the bundled Tails public signing key and package it into the AppImage.

### Release and Qualification

- Converge package/application metadata on version `0.5.3` and update user-visible copy to reflect explicit block-device targeting rather than removable-only behavior.
- Run the GitHub release gate with pytest, Ruff, and mypy instead of the narrower unittest-only discovery path.
- Make the AppImage build script directly executable, matching the release workflow invocation, and fix release-candidate AppImage path handling in the Tails real-boot smoke helper.

## v0.5.0 - 2026-05-06

### Experimental Features

- **Boot Loader Order Editing**: Added ability to parse and reorder boot menu entries via new `boot_loader.py` module with UI integration in `post_write.py`.
- **Network/Tor Support**: Implemented `network.py` for Tor detection and `torify`/`curl` fetching with remote catalog integration.
- **Drive Inspection**: Added privileged version detection logic and Tails installation analysis via extended `drive_inspector.py`.
- **Persistence-Preserving Upgrades**: Introduced `upgrader.py` for Tails upgrades that preserve user persistence data.

### Technical Improvements

- Added `BootLoaderOrderOptions` to models and wired through `PostWriteOptions`.
- Refactored remote_index.py to use new network helpers and support Tor fetching.
- Extended test coverage for boot-loader, network, post-write, and upgrader features.

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
