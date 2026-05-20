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
