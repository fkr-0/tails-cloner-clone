# Tails Cloner Design

## Chosen approach

Keep the original installer's broad component split — config, source handling, creator logic, and GUI flow — but modernize it into a smaller standalone desktop app that avoids the old GTK/UDisks runtime assumptions. The refreshed app uses Tk for the GUI, `lsblk` for removable-device discovery, and a background executor so remote version enumeration stays interactive.

## Why this shape

- It preserves the understandable installer architecture from the original codebase while reducing deployment weight.
- It keeps startup responsive by loading remote versions and removable devices asynchronously.
- It builds around testable controller and parser seams rather than GTK signal handlers.
- It keeps AppImage creation in CI so tagged releases can produce a single downloadable artifact.

## Data flow

1. App startup schedules async version and device refresh tasks.
2. Version refresh enumerates the remote directory index and materializes clickable Tails version entries.
3. Device refresh discovers removable disks through `lsblk --json`.
4. Version selection updates derived ISO, IMG, checksum, and signature URLs in the details pane.
5. Clone action runs `pkexec dd` in a worker thread and streams progress back into shared UI state.
