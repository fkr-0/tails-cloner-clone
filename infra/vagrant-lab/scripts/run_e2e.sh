#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
LAB_DIR="$ROOT_DIR/infra/vagrant-lab"
LOCK_FILE="$LAB_DIR/.vagrant-lab.lock"

# 3GB free-space note: two full Tails images need >4GB, so we fail early unless
# caller explicitly opts into low-space mode.
MIN_KB_REQUIRED=4500000
AVAILABLE_KB=$(df -Pk "$ROOT_DIR" | awk 'NR==2{print $4}')
LOW_SPACE_MODE="${LOW_SPACE_MODE:-0}"
if [[ "$LOW_SPACE_MODE" != "1" && "$AVAILABLE_KB" -lt "$MIN_KB_REQUIRED" ]]; then
  echo "Not enough free space for full lab run: have ${AVAILABLE_KB}KB, need >= ${MIN_KB_REQUIRED}KB"
  echo "Set LOW_SPACE_MODE=1 to run only fixture verification without forcing dual-image refresh."
  exit 1
fi

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

pushd "$LAB_DIR" >/dev/null
run_with_retry vagrant up
if [[ "$LOW_SPACE_MODE" != "1" ]]; then
  run_with_retry vagrant provision controller
fi
run_with_retry vagrant ssh controller -c "PYTHONPATH=/workspace/tails-cloner/src python3 /vagrant/scripts/verify_fixture_state.py"
run_with_retry vagrant ssh controller -c "cd /workspace/tails-cloner && PYTHONPATH=src python3 infra/vagrant-lab/scripts/run_lab_scenario.py validate-layout"
run_with_retry vagrant ssh controller -c "cd /workspace/tails-cloner && PYTHONPATH=src python3 infra/vagrant-lab/scripts/run_lab_scenario.py dry-run-install --target fresh --version 7.7.2"
run_with_retry vagrant ssh controller -c "cd /workspace/tails-cloner && PYTHONPATH=src python3 infra/vagrant-lab/scripts/run_lab_scenario.py dry-run-install --target upgrade --version 7.7.2"
if [ "${RUN_DESTRUCTIVE_UPGRADE_MILESTONE:-0}" = "1" ]; then
  run_with_retry vagrant ssh controller -c "cd /workspace/tails-cloner && sudo TAILS_CLONER_LAB_ALLOW_DESTRUCTIVE=1 PYTHONPATH=src python3 infra/vagrant-lab/scripts/run_lab_scenario.py simulate-internal-upgrade-preserve-persistence --version 7.7.2"
else
  echo "Skipping destructive persistence-preserving internal-upgrade milestone; set RUN_DESTRUCTIVE_UPGRADE_MILESTONE=1 to run it."
fi
run_with_retry vagrant ssh controller -c "sudo apt-get update -y && sudo apt-get install -y python3-pytest && cd /vagrant && PYTHONPATH=/workspace/tails-cloner/src python3 -m pytest -q tests/e2e"
popd >/dev/null
