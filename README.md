# Tails Cloner Clone

Tails Cloner Clone is a standalone Linux GUI **and automation CLI** for safely installing or upgrading Tails on an explicitly selected block device, including an intentional internal-disk target when it is not the running/current/source system.

The **only** implicit GUI invocation is an empty argument vector:

```bash
tails-cloner-clone
```

Any argument at all makes the CLI the primary dispatcher. This includes `--help`, `--version`, global CLI options, unknown options, and old GUI-looking options. There is no second implicit GUI argument parser and no `--gui` bypass. Unsupported arguments fail through the normal CLI usage path instead of accidentally starting the GUI.

The no-argument GUI also emits the stable `tails-cloner-cli/v1` JSONL operator event stream on stdout, with `command: "gui"`, so GUI operation is observable without scraping widgets.

## Safety model

The CLI and GUI share the same controller, planner, device-discovery, creator, upgrader, source, and verification code paths. The CLI does not implement a separate shell writer.

Key invariants:

- The running/current operating-system disk and every live-source disk are refused as targets.
- Source and target hardware identity are re-enumerated and compared again immediately before a destructive operation crosses the write boundary.
- Stable `/dev/disk/by-id` paths are preferred for actual writes.
- A noninteractive write needs both `--yes` **and** the exact stable `confirmation_token` reported by `devices inspect` or `plan`.
- A target without WWN, serial, or a stable by-id path cannot be acknowledged for a noninteractive CLI write.
- Official catalog images are accepted only after HTTPS retrieval, SHA-256 verification, and detached OpenPGP verification against the bundled pinned Tails signing key.
- An already-present catalog image is reused only after fresh SHA-256 and OpenPGP verification; failed verification never replaces the existing file.
- An upgrade writes only the Tails system partition. It never whole-disk-writes and existing Persistent Storage is preserved if present.
- Attached/live upgrade sources are treated read-only and source/target aliasing is refused.

> **Destructive operations are real.** `install` writes the whole target disk. `upgrade` rewrites the Tails system partition. Always inspect the plan and hardware identity before executing.

## Requirements

The write path uses standard Linux tools including `lsblk`, `pkexec`, `dd`, `mount`, `umount`, `blockdev`, `udevadm`, `losetup`, and `gpg`. Remote downloads fail closed when GnuPG is unavailable. When a local Tor SOCKS port is detected, existing Tor-aware network behavior uses `torify` and `curl`; otherwise HTTPS is used directly.

## CLI command tree

```text
tails-cloner-clone
├── status                         # alias: inspect
├── versions
│   ├── list
│   ├── latest
│   └── show VERSION
├── devices
│   ├── list
│   └── inspect DEVICE
├── source
│   ├── running
│   └── validate-attached --device DEVICE --mount-point PATH
├── download [VERSION|latest]
├── plan
│   ├── install SOURCE --target DEVICE
│   └── upgrade SOURCE --target DEVICE
├── install SOURCE --target DEVICE --yes --target-id TOKEN
└── upgrade SOURCE --target DEVICE --yes --target-id TOKEN
```

Use nested help for the authoritative option set:

```bash
tails-cloner-clone --help
tails-cloner-clone install --help
tails-cloner-clone plan upgrade --help
```

`tails-cloner-clone --version` prints the application version. Global options such as `--output`, `--log-file`, and `--remote-index-url` may be placed before or after nested commands.

## Catalog and verified downloads

```bash
# List known versions and identify latest/default.
tails-cloner-clone versions list

# Show exact ISO/IMG/signature/checksum metadata.
tails-cloner-clone versions show 7.7.2 --output json
tails-cloner-clone versions latest --output json

# Download latest to the normal cache and verify it.
tails-cloner-clone download latest

# Choose an explicit destination and stream machine events.
tails-cloner-clone download 7.7.2 \
  --destination "$HOME/Downloads/tails-amd64-7.7.2.img" \
  --output jsonl
```

The final download result records the source URL, checksum URL, signature URL, SHA-256 digest, signing fingerprint, pinned primary fingerprint, verification mechanisms, and whether a verified existing file was reused.

The stable catalog endpoint can be overridden for controlled environments/tests:

```bash
tails-cloner-clone versions list \
  --remote-index-url https://download.tails.net/tails/stable/
```

Remote image/checksum/signature URLs themselves remain HTTPS-only.

## Inspect current state and devices

```bash
tails-cloner-clone status
tails-cloner-clone status --output json
tails-cloner-clone devices list
tails-cloner-clone devices inspect /dev/disk/by-id/usb-EXAMPLE --output json
```

Device output includes, when available:

- kernel path and stable by-id path;
- stable identity tuple and `confirmation_token`;
- size, vendor/model, transport, serial, WWN, filesystem/label and GPT/isohybrid evidence;
- installed-Tails/version evidence;
- Persistent Storage presence/size;
- running/current-system/source flags;
- `selectable`, status label, and exact refusal reason.

Version inspection of an unmounted Tails partition remains non-privileged by default. The result explicitly reports when a read-only privileged mount would be required instead of silently prompting.

`status` reports application version, detected running-Tails source/version/device, current-system backing devices, attached-source metadata when supplied, and discovered-device count.

## Source selectors

`plan`, `install`, and `upgrade` accept exactly one source selector:

```text
--image PATH                  local IMG/ISO
--version VERSION             official catalog IMG; use latest for newest
--running-source              currently running Tails medium (upgrade only)
--attached-source DEVICE      mounted Tails live source (upgrade only)
  --attached-mount PATH       required with --attached-source
```

For a local image, optional first-class verification inputs are:

```bash
--sha256 DIGEST --signature FILE
```

For destructive execution, an unverified local image is refused unless the operator also supplies the explicit `--allow-unverified-local-image` acknowledgement. That override does **not** weaken target/source safety checks. Official catalog downloads cannot bypass SHA-256 + pinned OpenPGP verification.

## Dry-run planning

Planning never writes:

```bash
tails-cloner-clone plan install \
  --image ./tails.img \
  --target /dev/disk/by-id/usb-TARGET

tails-cloner-clone plan upgrade \
  --version latest \
  --target /dev/disk/by-id/usb-TARGET \
  --output json

tails-cloner-clone plan upgrade \
  --running-source \
  --target /dev/disk/by-id/usb-TARGET
```

Plans expose exact source resolution, target identity, `confirmation_token`, warnings, blocking errors, and explicit data semantics:

```yaml
install:
  destructive_scope: whole_device
  persistence_behavior: destroyed_if_present

upgrade:
  destructive_scope: tails_system_partition_only
  persistence_behavior: preserved_if_present
```

A catalog plan intentionally reports that the remote image must be downloaded before the write boundary; execution with `--version` performs the verified download automatically.

## Execute an install

First obtain the current token from `plan` or `devices inspect`, then repeat it exactly:

```bash
tails-cloner-clone install \
  --version latest \
  --target /dev/disk/by-id/usb-TARGET \
  --yes \
  --target-id 'serial:example123'
```

For a locally verified image:

```bash
tails-cloner-clone install \
  --image ./tails.img \
  --sha256 "$EXPECTED_SHA256" \
  --signature ./tails.img.sig \
  --target /dev/disk/by-id/usb-TARGET \
  --yes \
  --target-id 'wwn:example'
```

Immediately before mutation, the controller re-enumerates the devices, requires the same target hardware identity, replans, and then passes the stable write path to the existing creator.

### Install-only post-write controls

The existing optional customization hook is available without changing its default-off behavior:

```text
--enable-post-write-customizations
--disable-post-write-sync
--post-write-settle-seconds SECONDS
--post-write-log-file PATH
--boot-order-entry ENTRY          # repeatable; experimental existing feature
```

`--post-write-log-file` is the legacy/customization hook log. It is separate from the stable CLI event `--log-file` described below. Boot-loader ordering remains experimental and is not exposed for upgrades.

## Execute a persistence-preserving upgrade

Catalog image:

```bash
tails-cloner-clone upgrade \
  --version latest \
  --target /dev/disk/by-id/usb-TARGET \
  --yes \
  --target-id 'serial:example123'
```

Running Tails source:

```bash
tails-cloner-clone upgrade \
  --running-source \
  --target /dev/disk/by-id/usb-TARGET \
  --yes \
  --target-id 'serial:example123'
```

Explicit mounted attached live source:

```bash
tails-cloner-clone upgrade \
  --attached-source /dev/disk/by-id/usb-SOURCE-part1 \
  --attached-mount /run/media/example/tails \
  --target /dev/disk/by-id/usb-TARGET \
  --yes \
  --target-id 'serial:example123'
```

The upgrader keeps the v0.5.4 lifecycle hardening: read-only source attachment, bounded loop-partition discovery independent of global `udevadm settle` success, source/target anti-alias checks, partition-only write, post-write verification, and loop cleanup.

## Stable machine-output contract

The versioned contract is:

```yaml
schema: tails-cloner-cli/v1
schema_version: 1
```

Every event also contains a monotonic per-process `sequence`, a stable `kind`, and `command`. Stable event kinds are:

```text
start
discovery
resolution
verification
warning
progress
write_boundary
completion
failure
```

### Output modes

```text
--output human   readable final result on stdout; progress/diagnostics on stderr
--output json    exactly one final completion/failure JSON object on stdout
--output jsonl   stream schema-v1 events on stdout, one object per line
```

`--json` remains a shorthand for `--output json`.

The no-argument GUI does not need an output flag: it always writes the same schema-v1 event records as JSONL to stdout. GUI events cover startup, asynchronous version/device discovery and state changes, verified-download progress/verification, destructive write boundaries, install/upgrade progress, completion, and failure. Event messages are bounded and the same sink is safe to use from the GUI's executor threads.

Example:

```bash
tails-cloner-clone status --output json
tails-cloner-clone download latest --output jsonl
```

Machine consumers should key on `schema`, `kind`, and named fields rather than parsing human text. Progress messages are bounded and optional numeric fields such as transferred/written bytes are additive; consumers do not need to parse `dd` prose.

### Audit/event log

`--log-file PATH` appends the same `tails-cloner-cli/v1` JSONL events regardless of stdout mode:

```bash
tails-cloner-clone upgrade \
  --running-source \
  --target /dev/disk/by-id/usb-TARGET \
  --yes --target-id 'serial:example123' \
  --output human \
  --log-file ./tails-cloner-audit.jsonl
```

Events include relevant source/target stable identity, verification provenance, write-boundary state, completion/failure classification, and bounded diagnostics. They do not intentionally record secrets.

## Exit-code taxonomy

```text
0  success / successful dry-run plan
2  command-line syntax / argparse usage error
3  invalid input, unavailable source, or non-safety plan blocker
4  safety refusal, protected target, unstable identity, or acknowledgement mismatch
5  checksum/OpenPGP/local-image verification failure
6  catalog/download/network failure
7  install/write failure after the accepted safety boundary
8  upgrade failure after the accepted safety boundary
9  unexpected internal failure or interruption
```

Blocked dry-run plans still return their deterministic plan object, with a nonzero exit code reflecting the blocker category.

## Local development

```bash
PYTHONPATH=src python3 -m pytest
uvx ruff check .
mypy src tests
git diff --check

PYTHONPATH=src python3 -m tails_cloner --version
PYTHONPATH=src python3 -m tails_cloner status --output json
PYTHONPATH=src python3 -m tails_cloner
```

## AppImage build and CLI smoke

```bash
./scripts/build_appimage.sh dev
python3 scripts/smoke_appimage_release.py \
  --appimage ./dist/tails-cloner-clone-dev-x86_64.AppImage \
  --sha256-file ./dist/tails-cloner-clone-dev-x86_64.AppImage.sha256
```

The smoke gate executes the built artifact itself in **both entrypoint modes**: empty argv must open the GUI under Xvfb and emit a schema-v1 GUI startup record, while CLI probes exercise top-level and nested help, `--version`, schema-v1 `status`, schema-v1 `devices list`, and a non-destructive install plan against a discovered device when one is available. It never executes a real install or upgrade; destructive CLI E2E coverage uses deterministic fake services in pytest.

The AppImage `AppRun` forwards all arguments unchanged. The top-level routing contract is exact: `[] -> GUI`, nonempty argv -> CLI. The packaged application retains the pinned Tails signing-key resource required by download verification. The desktop entry remains `Terminal=false`; when stdout is connected, the GUI emits its JSONL event stream there even though it is a graphical application.

## Release flow

Release tags are `vX.Y.Z` matching the package version. The release workflow runs pytest, Ruff, and mypy, builds the AppImage, emits SHA-256 checksums, and creates the release asset.

The post-v0.5.4 CLI work described here is development work; it does not move or rewrite the `v0.5.4` tag.
