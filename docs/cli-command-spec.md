# Tails Cloner Clone CLI command specification

## Current v1 contract (2026-11)

The authoritative implementation is `src/tails_cloner/cli.py` and
`src/tails_cloner/cli_events.py`. The historical roadmap below is not the
current argument reference (in particular `--quiet` and `--dry-run` are not
implemented CLI options). Current output flags are `--output human|json|jsonl`,
`--json` and `--log-file PATH`. The v1 final completion/failure envelope keeps
its established fields and event `post_write_step` is additive, with status
`start|changed|skipped|no_change|failed|complete` and optional file/reason.

Install-only options available on `install` and read-only `plan install`:

```text
--enable-post-write-customizations
--disable-post-write-sync
--post-write-settle-seconds SECONDS   (finite, 0 to 60; default 2.0)
--post-write-log-file PATH
--boot-order-entry ENTRY     (repeatable)
```

The experimental warning has category `experimental_post_write`; human mode
always prints it to stderr, JSONL emits it and `--log-file` records it in any
mode. JSON stdout deliberately contains only the final envelope. The plan
adds `post_write_preview` with `enabled`, `experimental`, `requested_order`,
`available_entries`, `resolved_order`, `ignored_entries` and
`discovery_verified`. Preview never mounts a disk and needs no root. Discovery
currently handles supported ISO9660 images/directories, but refuses opaque
IMG/FAT content rather than guessing. A catalog source without a local image
cannot be previewed; unavailable/empty discovery is a refusal, not success.

Every install-only post-write option, including `--disable-post-write-sync`, `--post-write-settle-seconds` and `--post-write-log-file`, is rejected on both `upgrade` and `plan upgrade` with `post_write_upgrade_unsupported`, even if explicitly set to its install default. This is checked before source resolution or device access.

Experimental refusal categories (exit 3 unless stated otherwise):

<!-- experimental-categories-start -->
post_write_option_requires_enable
post_write_upgrade_unsupported
post_write_settle_invalid
boot_order_entry_empty
boot_order_entry_duplicate
boot_order_entry_unknown
boot_order_source_unavailable
boot_order_discovery_unavailable
boot_order_discovery_empty
post_write_failed_after_write
<!-- experimental-categories-end -->

`post_write_failed_after_write` exits **10**: the image was written, but
customization failed, without rollback. Current per-file step events are
derived from progress messages, so an interrupted operation need not contain a
complete file inventory. Unit tests and read-only previews cannot establish
real-device boot compatibility.

## Goal

Expose the same core capabilities as the GUI for CLI-capable users and for automation in the Vagrant/Tails lab, while keeping dangerous write operations explicit and hard to trigger accidentally.

## Proposed command shape

```text
tails-cloner-clone [global-options] <command> [command-options]
```

Global options:

```text
--json                         Emit machine-readable JSON where applicable.
--quiet                        Suppress progress output except errors.
--remote-index-url URL          Override the Tails version index URL.
--yes                           Confirm destructive prompts non-interactively.
--dry-run                       Show intended actions without writing devices.
--log-file PATH                 Write an operation log.
```

Safety defaults:

```text
- Commands that write to block devices require --yes in non-interactive mode.
- Commands refuse the running Tails source device.
- Commands refuse an attached source device as target.
- Device arguments are normalized to parent disk paths before safety checks.
- JSON output includes selectable=false and disabled_reason for source devices.
```

## Commands

### `versions list`

Fetch available Tails versions from the configured remote index.

```text
tails-cloner-clone versions list [--json]
```

Example JSON:

```json
{
  "versions": [
    {
      "version": "7.7.2",
      "directory_url": "https://download.tails.net/tails/stable/tails-amd64-7.7.2/",
      "iso_url": "...iso",
      "img_url": "...img",
      "sig_url": "...sig",
      "sha256_url": "...sha256"
    }
  ]
}
```

### `versions show`

Show URLs and checksums for one version.

```text
tails-cloner-clone versions show VERSION [--json]
```

### `download image`

Download a Tails IMG for later install/upgrade.

```text
tails-cloner-clone download image \
  --version VERSION \
  [--output PATH] \
  [--verify sha256|sig|none]
```

Behavior:

```text
- resolves VERSION through the remote index
- downloads IMG by default, because whole-device install should use IMG
- optionally downloads/checks matching SHA256 or detached signature
- prints final local image path
```

### `download iso`

Download a Tails ISO for workflows that intentionally need ISO input.

```text
tails-cloner-clone download iso \
  --version VERSION \
  [--output PATH] \
  [--verify sha256|sig|none]
```

### `devices list`

Enumerate all disk devices.

```text
tails-cloner-clone devices list [--json] [--include-partitions]
```

JSON fields:

```json
{
  "devices": [
    {
      "path": "/dev/sdb",
      "size_bytes": 16008609792,
      "size_label": "14.9 GiB",
      "vendor": "SanDisk",
      "model": "USB DISK",
      "transport": "usb",
      "removable": true,
      "read_only": false,
      "has_tails": true,
      "is_running_system_device": false,
      "is_attached_source_device": false,
      "selectable": true,
      "disabled_reason": ""
    }
  ]
}
```

### `devices inspect`

Inspect one target candidate in detail.

```text
tails-cloner-clone devices inspect /dev/sdX [--json]
```

Reports:

```text
- parent disk
- partitions
- Tails detection
- Persistence detection if available
- running/source status
- install eligibility
- upgrade eligibility
```

### `source running`

Print the currently running Tails source information.

```text
tails-cloner-clone source running [--json]
```

Example:

```json
{
  "running_tails_available": true,
  "version": "7.7.2",
  "device": "/dev/sdb1",
  "parent_device": "/dev/sdb",
  "iso_path": "/lib/live/mount/medium/live/Tails.iso"
}
```

### `source validate-attached`

Validate an attached/mounted Tails live source.

```text
tails-cloner-clone source validate-attached \
  --device /dev/sdX1 \
  --mount-point /mnt/tails-source \
  [--json]
```

### `install`

Install or reinstall from a local image, downloaded image, or running Tails source.

```text
tails-cloner-clone install \
  --target /dev/sdX \
  (--image PATH | --version VERSION | --running-source) \
  [--yes] [--dry-run]
```

Rules:

```text
- --target is always a whole disk target.
- --version VERSION implies download/resolve the IMG before writing.
- --running-source uses the embedded running Tails ISO when available.
- Existing Tails on target is treated as reinstall and warns that Persistence is deleted.
- The command refuses source/running devices even with --yes.
```

### `upgrade`

Upgrade an existing Tails target while preserving Persistent Storage.

```text
tails-cloner-clone upgrade \
  --target /dev/sdX \
  (--image PATH | --version VERSION | --running-source | --attached-source-device /dev/sdY1 --attached-source-mount /mnt/tails-source) \
  [--yes] [--dry-run]
```

Rules:

```text
- target must already contain a Tails installation
- target must not be the running source device
- target must not be the attached source device
- Persistence-preserving upgrade uses partition-scoped write logic
```

### `plan`

Produce a normalized operation plan without performing writes.

```text
tails-cloner-clone plan install --target /dev/sdX --image PATH [--json]
tails-cloner-clone plan upgrade --target /dev/sdX --version VERSION [--json]
```

This is the recommended API for tests and automation. It should return the same safety decisions used by `install` and `upgrade`.

Example JSON:

```json
{
  "operation": "upgrade",
  "source": {
    "type": "image",
    "path": "/home/amnesia/tails.img"
  },
  "target": {
    "path": "/dev/sdc",
    "has_tails": true,
    "selectable": true
  },
  "warnings": [
    "Persistent Storage will be preserved."
  ],
  "requires_confirmation": true,
  "would_write": true
}
```

## Implementation status

### Phase 1: read-only commands

Implemented in `src/tails_cloner/cli.py` and dispatched through the existing `tails-cloner-clone` entry point when a CLI subcommand is used.

Implemented:

```text
- versions list
- versions show
- devices list
- devices inspect
- source running
- source validate-attached
- plan install
- plan upgrade
```

Validation:

```text
PYTHONPATH=src python3 -m pytest -q tests/test_cli.py
# 5 passed
```

## Implementation plan

### Phase 1: read-only commands

```text
- complete: versions list/show
- complete: devices list/inspect
- complete: source running
- complete: source validate-attached
- complete: plan install/upgrade
- complete: shared planner used by CLI, GUI warning state, GUI confirmation copy, and controller pre-write guard
```

### Phase 2: download commands

```text
- download image
- download iso
- checksum verification
- structured progress events for --json
```

### Phase 3: write commands

```text
- install
- upgrade
- --yes confirmation handling
- --dry-run parity with plan commands
- progress callback output
```

### Phase 4: Vagrant/Tails e2e integration

```text
- run devices list --json inside Tails VM
- assert running source device is visible but selectable=false
- run plan install/upgrade for safe target fixtures
- assert source devices are refused
- run AppImage CLI smoke from downloaded release asset
```

## Recommended internal architecture

```text
src/tails_cloner/__main__.py
  parse global options
  dispatch subcommands

src/tails_cloner/cli.py
  command parser and output formatting

src/tails_cloner/planner.py
  side-effect-free install/upgrade plan construction
  shared by GUI and CLI

src/tails_cloner/controller.py
  keep orchestration and write operations

src/tails_cloner/models.py
  add serializable operation plan dataclasses
```

The biggest improvement is introducing a shared planner so the GUI and CLI use the same safety decisions and confirmation copy.
