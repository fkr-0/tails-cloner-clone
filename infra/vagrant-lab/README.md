# Vagrant Lab for Install/Upgrade Testing

This lab provisions a reproducible VM environment for testing install/upgrade flows with virtual block devices.

## Why this path name

`infra/vagrant-lab/` is recommended because:
- it is clearly infrastructure, not app runtime code
- it scales if you later add CI adapters or other VM/container labs
- it avoids crowding `tests/` with environment bootstrapping logic

## Quick start

```bash
cd infra/vagrant-lab
./scripts/run_e2e.sh
```

This provisioning also downloads pinned test artifacts:
- `tails-amd64-7.7.1.img`
- `tails-amd64-7.7.2.img`

`run_e2e.sh` serializes Vagrant actions with a lock file and retries commands if
Vagrant reports machine-lock contention.
For manual operations, use the same strategy via:

```bash
./scripts/vagrant_safe.sh status
./scripts/vagrant_safe.sh provision controller
```

### Low-space mode (for ~3GB free host space)

A full dual-image refresh typically needs >4GB free. For low-space hosts:

```bash
LOW_SPACE_MODE=1 ./scripts/run_e2e.sh
```

This skips forced reprovision/download refresh and runs fixture + test validation
against current artifacts.

## Disk and space estimates

### Virtual disks configured now
- `tc_source_disk`: 8 GB
- `tc_target_fresh`: 8 GB
- `tc_target_upgrade`: 8 GB
- `tc_nonremovable_like`: 16 GB

Raw logical allocation: **40 GB**.

### Real host consumption
VirtualBox VDI uses dynamic allocation, so used space grows with written blocks.
Typical usage:
- Base box + VM OS + packages: ~4-7 GB
- Fixture disks after formatting and light writes: ~2-5 GB
- Total practical footprint: **~8-15 GB**

Recommended free host space for comfortable runs: **at least 25 GB**.

If you plan to keep multiple snapshots or larger ISO fixture copies, keep **40+ GB** free.

## What is provisioned
- Ansible installs disk tooling (`lsblk`, `parted`, `sgdisk`, `mkfs.*`).
- Extra disks are discovered (excluding the OS disk).
- Fixture states are created:
  - source-like disk
  - fresh install target
  - upgrade-like target with persistence marker file
- Fixture metadata is written to:
  - `/opt/tails-cloner-fixtures/fixture-state.json`

## Next extension points
- Add scenario runner to invoke app/controller operations against fixture disks.
- Add post-operation validators (partition layout, persistence survival, checksums).
- Use `real-boot-lane/` for real image boot smoke tests with QEMU.
