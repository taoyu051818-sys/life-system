#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/opt/life-system"
VENV_PATH="/opt/life-system/.venv"
DB_PATH="/opt/life-system/data/life_system.db"
BACKUP_DIR="/opt/life-system/data/backups"
ENV_FILE="/etc/life-system/life-system.env"
USERNAME="xiaoyu"
TELEGRAM_CHAT_ID="8045312073"

log() {
  echo "[$1] $2"
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "缺少命令: $1" >&2
    exit 1
  fi
}

check_timer_waiting() {
  local timer_name="$1"
  local state
  local sub_state
  state="$(systemctl is-active "$timer_name" || true)"
  sub_state="$(systemctl show -p SubState --value "$timer_name" || true)"
  echo "$timer_name => active=$state, sub_state=$sub_state"
  if [[ "$state" != "active" || "$sub_state" != "waiting" ]]; then
    echo "错误: $timer_name 未处于 active(waiting) 状态" >&2
    exit 1
  fi
}

require_cmd python
require_cmd systemctl
require_cmd cp
require_cmd rm
require_cmd date

log "1/11" "进入项目目录并激活虚拟环境..."
cd "$PROJECT_DIR"
source "$VENV_PATH/bin/activate"

log "2/11" "加载环境变量..."
set -a
source "$ENV_FILE"
set +a

log "3/11" "检查 TELEGRAM_BOT_TOKEN..."
if [[ -n "${TELEGRAM_BOT_TOKEN:-}" ]]; then
  echo "TELEGRAM_BOT_TOKEN=SET"
else
  echo "TELEGRAM_BOT_TOKEN=MISSING"
  exit 1
fi

log "4/11" "备份旧数据库（如存在）..."
mkdir -p "$BACKUP_DIR"
if [[ -f "$DB_PATH" ]]; then
  ts="$(date +%Y%m%d_%H%M%S)"
  backup_path="$BACKUP_DIR/life_system_${ts}.db"
  cp "$DB_PATH" "$backup_path"
  echo "已备份: $backup_path"
else
  echo "旧数据库不存在，跳过备份"
fi

log "5/11" "删除旧主库并初始化新数据库..."
rm -f "$DB_PATH"
python -m life_system.main --db "$DB_PATH" init-db

log "6/11" "重新绑定默认用户 Telegram chat_id..."
python -m life_system.main --db "$DB_PATH" user set-telegram "$USERNAME" "$TELEGRAM_CHAT_ID"

log "7/11" "重新加载 Telegram 命令菜单..."
python -m life_system.main --db "$DB_PATH" telegram setup-menu

log "8/11" "重新下发 Telegram focus 键盘..."
keyboard_out="$(python -m life_system.main --db "$DB_PATH" telegram setup-keyboard)"
echo "$keyboard_out"
if [[ "$keyboard_out" == *"pushed=1"* ]]; then
  echo "setup-keyboard 检查: pushed=1"
else
  echo "setup-keyboard 检查: 未看到 pushed=1（请确认 xiaoyu chat_id 是否可达）"
fi

log "9/11" "重启相关 timers..."
systemctl restart life-telegram-poll.timer
systemctl restart life-reminders.timer
systemctl restart life-summary.timer

log "10/11" "检查 timers 状态（必须 active/waiting）..."
check_timer_waiting "life-telegram-poll.timer"
check_timer_waiting "life-reminders.timer"
check_timer_waiting "life-summary.timer"

log "11/11" "打印关键检查结果..."
python - <<'PY'
import sqlite3

db_path = "/opt/life-system/data/life_system.db"
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row

print("== users (id / username / telegram_chat_id) ==")
for row in conn.execute("SELECT id, username, telegram_chat_id FROM users ORDER BY id ASC"):
    print(f"{row['id']}\t{row['username']}\t{row['telegram_chat_id']}")

print("== sqlite tables ==")
for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name ASC"):
    print(row["name"])

conn.close()
PY

echo "完成：数据库已重建，基础 Telegram 配置与 timers 已恢复。"
