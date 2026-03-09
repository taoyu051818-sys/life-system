#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="/opt/life-system"
VENV_PATH="/opt/life-system/.venv"
ENV_FILE="/etc/life-system/life-system.env"

log() {
  echo "[INFO] $*"
}

restart_or_start_unit() {
  local unit="$1"
  if systemctl list-unit-files "$unit" --no-legend 2>/dev/null | grep -q "^$unit"; then
    log "restarting $unit"
    if systemctl is-active --quiet "$unit"; then
      systemctl restart "$unit"
    else
      systemctl start "$unit"
    fi
  else
    log "skip missing unit: $unit"
  fi
}

log "project dir: ${PROJECT_ROOT}"
cd "${PROJECT_ROOT}"

log "activating venv: ${VENV_PATH}"
# shellcheck disable=SC1091
source "${VENV_PATH}/bin/activate"

log "loading env: ${ENV_FILE}"
set -a
# shellcheck disable=SC1091
source "${ENV_FILE}"
set +a

# No daemon-reload here: this script is for code/shell updates only.
# Unit file changes should be handled separately with daemon-reload.

restart_or_start_unit "life-reminders.timer"
restart_or_start_unit "life-telegram-poll.timer"
restart_or_start_unit "life-summary.timer"
restart_or_start_unit "life-web.service"

log "status checks"
systemctl is-active life-reminders.timer || true
systemctl is-active life-telegram-poll.timer || true
systemctl is-active life-summary.timer || true
systemctl is-active life-web.service || true

log "done"
