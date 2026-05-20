#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAB_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$LAB_DIR"
timeout 180 scripts/vagrant_safe.sh ssh controller -c 'bash /workspace/tails-cloner/infra/vagrant-lab/scripts/controller_appimage_cli_probe_guest.sh'
