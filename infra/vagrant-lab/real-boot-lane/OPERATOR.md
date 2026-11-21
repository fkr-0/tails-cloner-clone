# Real-boot capture lane

This lane tracks the remaining interactive Tails E2E evidence for:

- E2E-001: install from a running live Tails system
- E2E-004: upgrade from an outdated running Tails system using an on-disc newer image source
- E2E-005: upgrade from an outdated running Tails system using an attached newer source medium

## Current state model

The non-interactive preparation path is automated. The remaining unautomated step is running the printed guest command inside a booted Tails graphical session so the serial marker can be captured.

## Persistent artifact locations

```text
.cache/vagrant-lab/tails-images/
.cache/vagrant-lab/capture-media/
infra/vagrant-lab/real-boot-lane/out/capture-runbooks/
infra/vagrant-lab/real-boot-lane/out/serial-logs/
```

Serial evidence is intentionally kept under `infra/vagrant-lab/real-boot-lane/out/serial-logs/` instead of `/tmp`.

Expected serial logs:

```text
infra/vagrant-lab/real-boot-lane/out/serial-logs/running-live-install.log
infra/vagrant-lab/real-boot-lane/out/serial-logs/outdated-running-iso-upgrade.log
infra/vagrant-lab/real-boot-lane/out/serial-logs/outdated-running-source-device-upgrade.log
```

## Bridge workflow

```bash
real-boot-validate-local
real-boot-prepare-runbooks
real-boot-preflight
real-boot-artifacts
real-boot-state
```

Then choose one variant:

```bash
real-boot-next-install
real-boot-next-upgrade-iso
real-boot-next-upgrade-source-device
```

Each `real-boot-next-*` command prints the concrete sequence:

```bash
<variant>.sh print
<variant>.sh capture
# paste the printed guest command inside Tails
<variant>.sh record
real-boot-state
real-boot-evidence-strict
```

## Evidence promotion

The generated runbooks call `record_guest_probe_evidence.py --mark-done` through their `record` action. After a valid serial marker exists, run:

```bash
real-boot-evidence
real-boot-evidence-strict
```

`real-boot-evidence-strict` only passes once all three variants have valid captured guest evidence.

## Status snapshots and bundles

Refresh a persistent status snapshot before or after runtime capture:

```bash
real-boot-snapshot
```

Snapshot files:

```text
infra/vagrant-lab/real-boot-lane/out/status-snapshots/latest.json
infra/vagrant-lab/real-boot-lane/out/status-snapshots/latest.md
```

Create a non-strict bundle of the current persistent artifacts at any time:

```bash
real-boot-bundle
```

Bundle files:

```text
infra/vagrant-lab/real-boot-lane/out/evidence-bundles/latest.tar.gz
infra/vagrant-lab/real-boot-lane/out/evidence-bundles/latest.manifest.json
```

After all expected serial logs exist, create the promotion-ready bundle:

```bash
real-boot-bundle-strict
```

`real-boot-bundle-strict` exits nonzero until the three expected serial logs are present.

## Local validation

```bash
real-boot-validate-local
```

This checks shell syntax, compiles all real-boot lane Python scripts, and runs the focused E2E tests without starting QEMU/Tails.

## Boot-order verification (experimental, separate lane)

A successful file rewrite is not evidence that Tails boots or that its menu order
changed on screen. Verify each layer independently.

### Offline directory-copy evidence

Run the command on a synthetic fixture directory containing grub.cfg or
syslinux.cfg. Do not point it at a mounted block device.

```bash
python3 infra/vagrant-lab/real-boot-lane/boot_order_evidence.py --source-dir /path/to/fixture --entry "Second" --entry "First" --dry-run
```

Only after reviewing the dry-run, run the disposable-copy validation:

```bash
TAILS_CLONER_LAB_ALLOW_DESTRUCTIVE=1 python3 infra/vagrant-lab/real-boot-lane/boot_order_evidence.py --source-dir /path/to/fixture --entry "Second" --entry "First" --evidence infra/vagrant-lab/real-boot-lane/out/boot-order/boot-order-offline-evidence.json
```

The tool applies the production rewrite to a temporary copy, verifies that
the original and unrelated bytes remain unchanged, and records before/after
hashes and parsed entries. It fails closed if a requested entry is absent or
any discovered config was skipped or cannot reflect the requested order. It
does not modify the input directory. The test fixture is a directory tree,
not a FAT image, so `fat_image` remains `not_verified`.

### Human-run QEMU layer (not yet qualified)

1. Check available disk space, active/stale qemu-system-x86_64 and
   run_appimage_smoke_via_serial_login.py processes; allow only one VM.
2. Preserve `out/debug-boot/tails-amd64-7.7.2-boot-8g-debug-serial.img`.
   Use a distinct disposable copy for the boot-order experiment.
3. Use an unprivileged FAT image tool or isolated lab VM to inject only the
   validated boot config into the disposable image; never mount real devices.
4. Run `probe_tails_boot_screen.py --dry-run` before a bounded live probe;
   store baseline and modified QMP screenshots plus
   `boot-screen-probe-evidence.json` under
   `infra/vagrant-lab/real-boot-lane/out/boot-order/qemu/`.
5. Review the actual visible titles and their positions. Whole-frame hashes
   alone cannot prove menu ordering: timing, cursor, and boot-stage differences
   can change pixels without changing the menu order.

The expected reviewed evidence path is
`infra/vagrant-lab/real-boot-lane/out/boot-order/qemu/boot-screen-probe-evidence.json`.
Keep separate baseline and rewritten-image screenshot sets, and record the
human-observed title order with screenshot paths and image SHA256 hashes. The
existing QMP probe only proves capture; without a verified visual comparison,
set `visible_menu_order` to `not_verified`.

At the 2026-11-21 check the prepared serial image was **absent** from
`out/debug-boot/` and the host filesystem reported only 16 GB free (99% used).
No live QEMU boot or FAT injection was attempted. First restore the documented
prepared image from an already trusted local cache (no multi-GB download in
this lane), then do a dry-run of the screenshot probe and review disk/process
preflight again. Never substitute the historical screenshots in `out/` as
evidence of the newly rewritten image.

Neither dry-run success, offline copy success nor QMP capture alone proves
real-device boot compatibility. Record these layers as `not_verified`
until independent evidence exists.
