#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAB_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT_DIR="$(cd "$LAB_DIR/../.." && pwd)"
LOCK_FILE="$LAB_DIR/.vagrant-lab.lock"

run_with_retry() {
  local tries=0
  local max_tries=3
  local delay=5
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

vm_is_running() {
  vagrant status --machine-readable controller 2>/dev/null | grep -q ',state,running'
}

exec 9>"$LOCK_FILE"
flock -w 60 9

pushd "$LAB_DIR" >/dev/null

echo "== tails-cloner vagrant lab status =="
echo "project_root=$ROOT_DIR"
echo "lab_dir=$LAB_DIR"
echo
echo "-- vagrant status --"
vagrant status controller

if ! vm_is_running; then
  echo
  echo "VM is not running; skipping SSH-based status checks."
  popd >/dev/null
  exit 0
fi

echo
echo "-- ssh reachability --"
run_with_retry vagrant ssh controller -c "hostname; uname -a"

echo
echo "-- fixture state --"
run_with_retry vagrant ssh controller -c "if [ -f /opt/tails-cloner-fixtures/fixture-state.json ]; then cat /opt/tails-cloner-fixtures/fixture-state.json; else echo 'missing /opt/tails-cloner-fixtures/fixture-state.json'; exit 2; fi"

echo
echo "-- fixture disks --"
run_with_retry vagrant ssh controller -c "lsblk -o NAME,PATH,SIZE,TYPE,FSTYPE,LABEL,MOUNTPOINTS"

echo
echo "-- artifact files --"
run_with_retry vagrant ssh controller -c "find /opt/tails-cloner-fixtures/tails-images -maxdepth 1 -type f -printf '%f %s bytes\n' 2>/dev/null | sort || true"

echo
echo "-- workspace sync --"
run_with_retry vagrant ssh controller -c "test -d /workspace/tails-cloner && ls -ld /workspace/tails-cloner && test -f /workspace/tails-cloner/pyproject.toml"

popd >/dev/null
