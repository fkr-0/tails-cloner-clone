# Real-Boot Lane (QEMU)

This lane is for booting a real Tails image in a VM (not just fake drive fixtures).

## Goal

Validate app-adjacent assumptions against an actually booted Tails instance:
- Tails image boots
- expected live filesystem layout exists (`/lib/live/mount/medium/live/Tails.version`)
- version string can be read in a real booted environment

## Space-aware usage (3GB host free-space)

Use one image at a time. Do **not** keep both `7.7.1` and `7.7.2` locally when space is low.

## Quick start

```bash
cd infra/vagrant-lab/real-boot-lane
./boot_tails_qemu.sh /path/to/tails-amd64-7.7.2.img
```

This starts QEMU with a temporary writable overlay (`-snapshot`) and no host mutation.

## What this lane can/can't validate

Can:
- real boot viability of an image
- real in-guest file layout/version visibility

Can't:
- hardware USB behavior parity
- persistence unlock workflows without additional automation
- exact behavior of physical-machine firmware quirks
