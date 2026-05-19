#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 /path/to/tails-amd64-<version>.img" >&2
  exit 1
fi

IMG_PATH="$1"
if [[ ! -f "$IMG_PATH" ]]; then
  echo "Image not found: $IMG_PATH" >&2
  exit 1
fi

if ! command -v qemu-system-x86_64 >/dev/null 2>&1; then
  echo "Missing dependency: qemu-system-x86_64" >&2
  exit 1
fi

# Keep defaults conservative for low-space hosts.
MEMORY_MB="${TAILS_QEMU_MEMORY_MB:-4096}"
CPUS="${TAILS_QEMU_CPUS:-2}"

exec qemu-system-x86_64 \
  -machine q35,accel=kvm:tcg \
  -cpu host \
  -m "$MEMORY_MB" \
  -smp "$CPUS" \
  -drive file="$IMG_PATH",format=raw,if=virtio,snapshot=on \
  -display gtk \
  -boot order=c \
  -net nic -net user
