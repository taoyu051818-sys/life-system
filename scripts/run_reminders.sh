#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="/opt/life-system"
VENV_PATH="/opt/life-system/.venv"
DB_PATH="/opt/life-system/data/life_system.db"

cd "${PROJECT_ROOT}"
source "${VENV_PATH}/bin/activate"

python -m life_system.main --db "${DB_PATH}" reminder due --send

