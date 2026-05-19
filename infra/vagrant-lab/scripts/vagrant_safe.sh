#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <vagrant-subcommand> [args...]" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAB_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LOCK_FILE="$LAB_DIR/.vagrant-lab.lock"

run_with_retry() {
  local tries=0
  local max_tries=8
  local delay=15
  until "$@"; do
    tries=$((tries + 1))
    if [[ "$tries" -ge "$max_tries" ]]; then
      echo "Command failed after $max_tries attempts: $*" >&2
      return 1
    fi
    echo "Retry $tries/$max_tries after lock/error: $*" >&2
    sleep "$delay"
  done
}

exec 9>"$LOCK_FILE"
flock -w 900 9

cd "$LAB_DIR"
run_with_retry vagrant "$@"
