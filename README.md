# life-system

Low-resource, CLI-first personal life system (Python + SQLite).

## Run

```bash
python -m life_system.main init-db
python -m life_system.main --help
```

## Multi-User Scope

- Global argument: `--user <username>`
- Default user if omitted: `xiaoyu`
- Demo users auto-created by `init-db`: `xiaoyu`, `partner`

```bash
python -m life_system.main --user xiaoyu capture "review words"
python -m life_system.main --user partner task list
python -m life_system.main user list
python -m life_system.main user add alice --display-name "Alice"
```

## Core Commands

```bash
# Inbox
python -m life_system.main capture "buy vitamins"             # alias of inbox capture
python -m life_system.main --user xiaoyu capture "buy vitamins"
python -m life_system.main inbox capture "buy vitamins"
python -m life_system.main inbox list
python -m life_system.main inbox list --all
python -m life_system.main inbox list --status archived
python -m life_system.main inbox triage 1 task
python -m life_system.main inbox triage 2 anki
python -m life_system.main inbox triage 3 archive

# Task
python -m life_system.main task create "word review"
python -m life_system.main --user partner task create "pay bills"
python -m life_system.main task create "fix cup" --inbox-id 1
python -m life_system.main task list
python -m life_system.main task snooze 1 2026-03-08T09:00:00+08:00
python -m life_system.main task done 1
python -m life_system.main task abandon 2 --reason-code overwhelm --reason-text "too large" --energy-level 2

# Reminder
python -m life_system.main reminder create 1 2026-03-08T10:00:00+08:00
python -m life_system.main reminder due
python -m life_system.main reminder due --send
python -m life_system.main reminder pending-ack
python -m life_system.main reminder ack 1
python -m life_system.main reminder snooze 1 2026-03-08T12:00:00+08:00
python -m life_system.main reminder skip 1 --reason "not needed"
python -m life_system.main reminder show 1
python -m life_system.main reminder history 1

# Anki Draft
python -m life_system.main anki create manual "What is next action?" "A concrete next step"
python -m life_system.main anki list
python -m life_system.main anki export-csv data/anki_drafts.csv
python -m life_system.main --user partner anki export-csv data/partner_anki.csv

# Journal
python -m life_system.main journal add "finished review" --type activity --energy 4 --focus 3 --mood 5 --tags study,english
python -m life_system.main journal add "small win today" --type win
python -m life_system.main journal list --limit 20
python -m life_system.main journal list --type reflection
python -m life_system.main journal today

# Summary
python -m life_system.main summary today
python -m life_system.main summary day --date 2026-03-07

# User Telegram
python -m life_system.main user set-telegram xiaoyu 123456789
python -m life_system.main user clear-telegram xiaoyu
```

## Datetime Validation

- `task snooze` and `reminder create` require strict ISO-8601 datetime.
- `reminder snooze` also requires strict ISO-8601 datetime.
- Valid examples:
  - `2026-03-08T09:00:00+08:00`
  - `2026-03-07T00:00:00+00:00`

## Reminder Statuses

- pending
- sent
- acknowledged
- snoozed
- skipped
- failed
- expired

## Journal Entry Types

- activity
- reflection
- win
- checkin

## CLI Output Notes

- `list` commands use compact scan-friendly rows.
- `show` commands use key/value blocks.
- `history` commands use time-ordered event lines.
- Repeated state changes return status-aware feedback (for example `already acknowledged`, `already archived`).

## Summary Output

- `summary today` / `summary day` output is Chinese by default.
- Summary day boundaries are hardcoded to Asia/Shanghai (鍖椾含鏃堕棿).
- Summary timestamps in highlights are displayed in Beijing time.
- Summary is evidence-first and user-scoped.
- Includes overview counts, journal highlights, state snapshot, open loops, and a short conservative note.
- Reminder overview splits first send and retry separately.
- Journal highlights are capped to a small recent subset for readability.

## Reminder Time Display

- Reminder CLI display (`due`, `pending-ack`, `show`, `history`) uses Beijing time for readability.
- Reminder storage remains UTC ISO-8601 internally.

## Telegram Reminder Setup

1. Use BotFather to create a bot and get a bot token.
2. Export token in shell:
   - `set TELEGRAM_BOT_TOKEN=...` (Windows)
   - `export TELEGRAM_BOT_TOKEN=...` (Linux/macOS)
3. Set chat id for a user:
   - `python -m life_system.main user set-telegram xiaoyu <chat_id>`
4. Setup Telegram command menu:
   - `python -m life_system.main telegram setup-menu`
5. Send due reminders:
   - `python -m life_system.main --user xiaoyu reminder due --send`
6. Poll updates (this pass uses polling, not webhook):
   - `python -m life_system.main telegram poll --limit 20`

Notes:
- If user has no `telegram_chat_id`, `--send` falls back to CLI processing.
- If `telegram_chat_id` exists but token is missing, send fails clearly.
- Telegram reminder buttons:
  - 完成
  - 延后10分钟
  - 跳过今天
- Callback actions are processed via `telegram poll` and then mapped to existing reminder ack/snooze/skip logic.

## Telegram Journal Capture (Polling)

- Still based on `telegram poll` + `getUpdates` (no webhook in this pass).
- Only private chat text messages are handled.
- User mapping is based on `users.telegram_chat_id`.
- Unknown chat id / non-text / group messages are ignored safely.

Message rules:
- Plain text: default to `activity`
- `/r <text>`: `reflection`
- `/w <text>`: `win`
- `/c <text>`: `checkin`
- `/help`: show concise usage help in Chinese
- `/c` supports optional leading state fields:
  - `energy=1..5`
  - `focus=1..5`
  - `mood=1..5`
  - Example: `/c energy=2 focus=2 mood=3 今天状态一般`

Examples:
- `今天完成了背单词`
- `/r 今天启动很难，但开始后还行`
- `/w 今天至少没有脱离系统`
- `/c energy=2 focus=2 mood=3 今天状态一般`

### Activity to Inbox split rules

- Telegram message always writes journal first.
- `reflection` / `win` / `checkin`: journal only, no auto inbox copy.
- `activity`: journal first, then strict inbox copy decision.
- Inbox copy uses source `telegram_auto`.
- If inbox copy fails, journal entry is kept (no rollback).

Polling output includes:
- `inbox_created`
- `inbox_failed`

## Inbox Review Reminder

- Commands:
  - `python -m life_system.main inbox review-due`
  - `python -m life_system.main inbox review-send`
- Uses Asia/Shanghai day and 20:30 window.
- Sends at most once per user per day when unprocessed inbox items exist.
- Escalates when:
  - unprocessed inbox >= 7
  - oldest unprocessed item >= 72 hours
- Delivery:
  - with `telegram_chat_id`: Telegram text message
  - without `telegram_chat_id`: CLI fallback

## Abandonment Reason Presets

- overwhelm
- wrong_timing
- no_value
- impulse
- blocked

## Retries

- Retries are scheduled step-by-step when `reminder due --send` runs.

## Linux Deployment (systemd timers)

Target assumptions:
- project root: `/opt/life-system`
- virtualenv: `/opt/life-system/.venv`
- database: `/opt/life-system/data/life_system.db`

### 1. Install base packages

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv sqlite3 git
```

### 2. Clone project and create venv

```bash
sudo mkdir -p /opt
cd /opt
sudo git clone <your-repo-url> life-system
cd /opt/life-system
sudo python3 -m venv .venv
sudo /opt/life-system/.venv/bin/python -m pip install --upgrade pip
```

### 3. Initialize database

```bash
cd /opt/life-system
sudo /opt/life-system/.venv/bin/python -m life_system.main --db /opt/life-system/data/life_system.db init-db
```

### 4. Create environment file

```bash
sudo mkdir -p /etc/life-system
sudo tee /etc/life-system/life-system.env >/dev/null <<'EOF'
TELEGRAM_BOT_TOKEN=your_bot_token_here
EOF
sudo chmod 600 /etc/life-system/life-system.env
```

### 5. Ensure wrapper scripts are executable

```bash
sudo chmod +x /opt/life-system/scripts/run_reminders.sh
sudo chmod +x /opt/life-system/scripts/run_telegram_poll.sh
sudo chmod +x /opt/life-system/scripts/run_summary_today.sh
```

### 6. Install systemd units

```bash
sudo cp /opt/life-system/deploy/systemd/life-reminders.service /etc/systemd/system/
sudo cp /opt/life-system/deploy/systemd/life-reminders.timer /etc/systemd/system/
sudo cp /opt/life-system/deploy/systemd/life-telegram-poll.service /etc/systemd/system/
sudo cp /opt/life-system/deploy/systemd/life-telegram-poll.timer /etc/systemd/system/
sudo cp /opt/life-system/deploy/systemd/life-summary.service /etc/systemd/system/
sudo cp /opt/life-system/deploy/systemd/life-summary.timer /etc/systemd/system/
```

### 7. Reload and enable timers

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now life-reminders.timer
sudo systemctl enable --now life-telegram-poll.timer
sudo systemctl enable --now life-summary.timer
```

### 8. Verify timers and logs

```bash
sudo systemctl list-timers --all | grep life-
sudo systemctl status life-reminders.timer
sudo systemctl status life-telegram-poll.timer
sudo systemctl status life-summary.timer

sudo journalctl -u life-reminders.service -n 100 --no-pager
sudo journalctl -u life-telegram-poll.service -n 100 --no-pager
sudo journalctl -u life-summary.service -n 100 --no-pager
```

For systemd asset details, see:
- `deploy/systemd/README.md`

