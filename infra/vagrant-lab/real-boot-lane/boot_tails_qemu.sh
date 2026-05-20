#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage: boot_tails_qemu.sh [options] /path/to/tails-amd64-<version>.img

Options:
  --dry-run              Print the qemu command without executing it.
  --headless             Use -display none and serial stdio instead of GTK.
  --timeout SEC          Wrap qemu in timeout --foreground SEC.
  --extra-drive PATH     Attach an additional raw drive snapshot=on. May be repeated.
  --qmp UNIX_SOCKET      Enable QMP control socket at UNIX_SOCKET.
  --pidfile PATH         Write the QEMU process id to PATH.
  --serial-log PATH      Write guest serial output to PATH instead of stdio.
  --share-dir PATH,TAG   Expose a host directory to the guest with virtio-9p. May be repeated.
  --no-network           Disable qemu user networking.
  -h, --help             Show this help.

Environment:
  TAILS_QEMU_MEMORY_MB   Memory in MiB, default: 4096
  TAILS_QEMU_CPUS        vCPU count, default: 2
  TAILS_QEMU_CPU_MODEL   CPU model override. Default: max for KVM/TCG portability
USAGE
}

DRY_RUN=0
HEADLESS=0
NETWORK=1
TIMEOUT_SEC="${TAILS_QEMU_TIMEOUT_SEC:-}"
QMP_SOCKET=""
PIDFILE=""
SERIAL_LOG=""
SHARE_DIRS=()
EXTRA_DRIVES=()
POSITIONAL=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --headless)
      HEADLESS=1
      shift
      ;;
    --timeout)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for --timeout" >&2
        exit 1
      fi
      TIMEOUT_SEC="$2"
      shift 2
      ;;
    --extra-drive)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for --extra-drive" >&2
        exit 1
      fi
      EXTRA_DRIVES+=("$2")
      shift 2
      ;;
    --qmp)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for --qmp" >&2
        exit 1
      fi
      QMP_SOCKET="$2"
      shift 2
      ;;
    --pidfile)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for --pidfile" >&2
        exit 1
      fi
      PIDFILE="$2"
      shift 2
      ;;
    --serial-log)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for --serial-log" >&2
        exit 1
      fi
      SERIAL_LOG="$2"
      shift 2
      ;;
    --share-dir)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for --share-dir" >&2
        exit 1
      fi
      SHARE_DIRS+=("$2")
      shift 2
      ;;
    --no-network)
      NETWORK=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      while [[ $# -gt 0 ]]; do
        POSITIONAL+=("$1")
        shift
      done
      ;;
    -* )
      echo "Unknown option: $1" >&2
      usage
      exit 1
      ;;
    *)
      POSITIONAL+=("$1")
      shift
      ;;
  esac
done

if [[ ${#POSITIONAL[@]} -ne 1 ]]; then
  usage
  exit 1
fi

IMG_PATH="${POSITIONAL[0]}"
if [[ ! -f "$IMG_PATH" ]]; then
  echo "Image not found: $IMG_PATH" >&2
  exit 1
fi

if ! command -v qemu-system-x86_64 >/dev/null 2>&1; then
  echo "Missing dependency: qemu-system-x86_64" >&2
  exit 1
fi

for drive in "${EXTRA_DRIVES[@]}"; do
  if [[ ! -f "$drive" && ! -b "$drive" ]]; then
    echo "Extra drive not found: $drive" >&2
    exit 1
  fi
done

for share_spec in "${SHARE_DIRS[@]}"; do
  share_path="${share_spec%%,*}"
  share_tag="${share_spec#*,}"
  if [[ "$share_path" == "$share_spec" || -z "$share_path" || -z "$share_tag" ]]; then
    echo "Invalid --share-dir value, expected PATH,TAG: $share_spec" >&2
    exit 1
  fi
  if [[ ! -d "$share_path" ]]; then
    echo "Shared directory not found: $share_path" >&2
    exit 1
  fi
done

if [[ -n "$SERIAL_LOG" ]]; then
  mkdir -p "$(dirname "$SERIAL_LOG")"
fi

# Keep defaults conservative for low-space hosts.
MEMORY_MB="${TAILS_QEMU_MEMORY_MB:-4096}"
CPUS="${TAILS_QEMU_CPUS:-2}"
if [[ -n "${TAILS_QEMU_CPU_MODEL:-}" ]]; then
  CPU_MODEL="$TAILS_QEMU_CPU_MODEL"
else
  # Use a TCG-safe default. Some nested/lab hosts expose /dev/kvm but fail
  # KVM_CREATE_VM at runtime; -cpu host then aborts after QEMU falls back to TCG.
  CPU_MODEL="max"
fi

QEMU_CMD=(
  qemu-system-x86_64
  -machine q35,accel=kvm:tcg
  -cpu "$CPU_MODEL"
  -m "$MEMORY_MB"
  -smp "$CPUS"
  -drive "file=$IMG_PATH,format=raw,if=virtio,snapshot=on"
  -boot order=c
)

if [[ -n "$QMP_SOCKET" ]]; then
  QEMU_CMD+=(-qmp "unix:$QMP_SOCKET,server=on,wait=off")
fi

if [[ -n "$PIDFILE" ]]; then
  QEMU_CMD+=(-pidfile "$PIDFILE")
fi

if [[ "$HEADLESS" -eq 1 ]]; then
  QEMU_CMD+=(-display none)
  if [[ -n "$SERIAL_LOG" ]]; then
    QEMU_CMD+=(-serial "file:$SERIAL_LOG")
  else
    QEMU_CMD+=(-serial mon:stdio)
  fi
else
  QEMU_CMD+=(-display gtk)
  if [[ -n "$SERIAL_LOG" ]]; then
    QEMU_CMD+=(-serial "file:$SERIAL_LOG")
  fi
fi

if [[ "$NETWORK" -eq 1 ]]; then
  QEMU_CMD+=(-net nic -net user)
else
  QEMU_CMD+=(-net none)
fi

for drive in "${EXTRA_DRIVES[@]}"; do
  QEMU_CMD+=(-drive "file=$drive,format=raw,if=virtio,snapshot=on")
done

share_index=0
for share_spec in "${SHARE_DIRS[@]}"; do
  share_path="${share_spec%%,*}"
  share_tag="${share_spec#*,}"
  QEMU_CMD+=(
    -fsdev "local,id=fsdev${share_index},path=${share_path},security_model=mapped-xattr,readonly=on"
    -device "virtio-9p-pci,fsdev=fsdev${share_index},mount_tag=${share_tag}"
  )
  share_index=$((share_index + 1))
done

if [[ -n "$TIMEOUT_SEC" ]]; then
  QEMU_CMD=(timeout --foreground "$TIMEOUT_SEC" "${QEMU_CMD[@]}")
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  printf '%q ' "${QEMU_CMD[@]}"
  printf '\n'
  exit 0
fi

exec "${QEMU_CMD[@]}"
