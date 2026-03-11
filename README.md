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
python -m life_system.main summary week --date 2026-03-10
python -m life_system.main summary month --date 2026-03-10
python -m life_system.main summary quarter --date 2026-03-10
python -m life_system.main summary year --date 2026-03-10

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

- `summary today` / `summary day` / `summary week` / `summary month` / `summary quarter` / `summary year` output is Chinese by default.
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
- `/ir`: inbox review manual entry (confirm first, then start)
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

## Telegram Inbox Light Triage

- Command:
  - `python -m life_system.main --user xiaoyu telegram inbox-review --limit 5`
- Behavior:
  - Sends each `status='new'` inbox item as a separate Telegram message.
  - Inline buttons per item:
    - `转任务` (`it:<inbox_id>`)
    - `归档` (`ia:<inbox_id>`)
    - `先留着` (`ik:<inbox_id>`)
- Scope:
  - Telegram only handles light triage (task/archive/keep).
  - No time setting / reminder setup in Telegram for this pass.

## Inbox Review Reminder

- Commands:
  - `python -m life_system.main inbox review-due`
  - `python -m life_system.main inbox review-send`
- Uses Asia/Shanghai day and 20:30 base due time.
- `review-send` sends Telegram entry message first (not direct item flood), with buttons:
  - `开始回顾`
  - `延后半小时` (max 3 times per day)
  - `今天跳过`
- `开始回顾` then triggers existing `telegram inbox-review` item flow.
- Same due point is idempotent and will not be sent repeatedly.
- Escalates when:
  - unprocessed inbox >= 7
  - oldest unprocessed item >= 72 hours
- Delivery:
  - with `telegram_chat_id`: Telegram text message
  - without `telegram_chat_id`: CLI fallback

## Triage Outcome Logging

- Inbox triage now writes `triage_events` on success:
  - `to_task`
  - `to_anki`
  - `to_archive`
- New commands:
  - `python -m life_system.main inbox history <inbox_id>`
  - `python -m life_system.main inbox triage-history --limit 50`
- If event logging fails, main triage action still succeeds and CLI prints a warning.

## Inbox Feedback Signals

- Commands:
  - `python -m life_system.main --user xiaoyu inbox feedback-scan --now 2026-03-08T12:30:00+00:00`
  - `python -m life_system.main --user xiaoyu inbox feedback-report --limit 50`
- `feedback-scan` only scans current `--user` data and writes idempotent signals.
- Current signal types:
  - `auto_to_task_24h`
  - `auto_to_anki_24h`
  - `auto_to_archive_24h`
  - `auto_pending_72h`
  - `review_led_to_triage_24h`
  - `review_no_triage_24h`

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


## PC Web UI (Phase 0 + Phase 1)

Minimal stack: FastAPI + Jinja2 templates + htmx.
Web layer is a thin adapter and reuses existing service/repository logic.

Run locally:
- `python -m life_system.web`
- or `uvicorn life_system.web.app:create_app --factory --host 0.0.0.0 --port 8080`

Optional env:
- `LIFE_SYSTEM_DB=/abs/path/to/life_system.db`
- `LIFE_WEB_HOST=0.0.0.0`
- `LIFE_WEB_PORT=8080`

Available pages:
- `GET /`
- `GET /health`
- `GET /inbox`

Inbox actions (htmx partial refresh):
- `POST /inbox/{id}/to-task`
- `POST /inbox/{id}/archive`
- `GET /inbox/{id}/history`

## Web Login + Quick Journal (Phase 2)

Environment variables:
- `LIFE_WEB_PASSWORD` (required)
- `LIFE_WEB_SESSION_SECRET` (recommended)
- `LIFE_WEB_DEFAULT_USER` (default: `xiaoyu`)
- `LIFE_SYSTEM_DB` (optional, default `data/life_system.db`)

Behavior:
- Unauthenticated access to `/`, `/inbox`, `/tasks`, `/reminders`, `/journal`, `/anki` redirects to `/login`.
- Login is password-only with optional "remember this device".
- Session cookie is used (thin web auth only, not a public internet auth system).
- Inbox page adds `keep` action (`POST /inbox/{id}/keep`) with Telegram-consistent semantics:
  no status change, no triage event write.
- Home page includes Quick Journal:
  - activity / reflection / win text input
  - focus 1-5 quick checkin (`checkin` + content=`状态签到`)

## Web Tasks + Reminders (Phase 3)

New pages:
- `GET /tasks`
- `GET /reminders`

Tasks page:
- list fields: id/title/status/created_at/due_at/snooze_until
- actions: detail / done / snooze
- actions reuse `LifeSystemService` task methods

Reminders page:
- list fields: id/task_title/status/remind_at/last_event
- actions: ack / snooze / skip
- actions reuse `LifeSystemService` reminder methods

All displayed times remain Beijing time via shared `bj_time` filter.

## Web Journal + Anki (Phase 4)

New pages:
- GET /journal?limit=50`r
- GET /anki?limit=100`r

Journal page:
- list fields: created_at / entry_type / content / energy / focus / mood / related_task_id / related_inbox_id / tags
- default reverse chronological order (latest first)

Anki page:
- list fields: id / front / back / tags / deck / status / created_at / source
- actions: update / archive

Anki JSON import:
- action: POST /anki/import-json`r
- supports either a single object or an array
- required fields: ront, ack`r
- optional fields: 	ags (string or string array), deck (default default)
- import policy: all-or-nothing (any validation error rejects the whole batch)


## Anki Review (SM-2 MVP)

New tables:
- nki_cards`r
- nki_review_events`r

CLI:
- python -m life_system.main anki review-due --limit 20`r
- python -m life_system.main anki review 1 --rate good`r

Web:
- GET /anki/review shows next due card
- rating buttons: gain / hard / good / easy`r



## Web Anki Batch Operations

On `/anki` page:
- Drafts panel supports deck filter + checkbox selection + batch activate (`/anki/batch-activate`)
- Due Cards panel supports checkbox selection + batch review (`/anki/batch-review`)
- Batch activate/review both return summary counts in flash message

CLI remains available:
- `python -m life_system.main anki activate 1 2 3`
- `python -m life_system.main anki review-due --limit 20`
- `python -m life_system.main anki review 1 --rate good`


## Web Anki Review & Stats

- `GET /anki/review`: one-card-at-a-time review session
  - optional filter: `?deck_name=default`
  - reveal answer: `POST /anki/review/reveal`
  - rate card: `POST /anki/review/rate` with `again|hard|good|easy`
- `GET /anki/stats`: lightweight dashboard
  - summary: draft total, non-archived drafts, active cards, due now
  - recent 7 days: drafts created, cards activated, review count
  - rating distribution: again/hard/good/easy
  - deck breakdown: draft and due pressure by deck

## Encouragement (DeepSeek + Telegram)

Environment variables:
- `DEEPSEEK_API_KEY` (preferred) or `APIKEY`
- `DEEPSEEK_BASE_URL` (optional, default: `https://api.deepseek.com`)
- `DEEPSEEK_MODEL` (optional, default: `deepseek-chat`)

CLI:
- `python -m life_system.main --user xiaoyu encouragement today`
- `python -m life_system.main --user xiaoyu encouragement send`
- `python -m life_system.main encouragement send-daily`

Data scope for DeepSeek prompt:
- Uses all journal entries of the selected day (Asia/Shanghai), no fixed 10/50 cap.

Telegram:
- New command: `/encouragement`
- `telegram setup-menu` now includes `/encouragement`

Systemd automation at 20:30 (Asia/Shanghai):
- script: `/opt/life-system/scripts/run_encouragement.sh`
- unit: `deploy/systemd/life-encouragement.service`
- timer: `deploy/systemd/life-encouragement.timer`

Install/update:
```bash
sudo chmod +x /opt/life-system/scripts/run_encouragement.sh
sudo cp /opt/life-system/deploy/systemd/life-encouragement.service /etc/systemd/system/
sudo cp /opt/life-system/deploy/systemd/life-encouragement.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now life-encouragement.timer
sudo systemctl status life-encouragement.timer
```




## Web Anki Share Review Link

- Service API:
  - `create_anki_review_share_link(base_url, ttl_minutes=120, max_uses=1)`
  - `consume_anki_review_share_token(token)`
- Share entry route: `GET /share/anki-review?t=...`
- On successful token validation, web session grants `/anki/review` access for 120 minutes.
- Share session does not unlock other pages (for example `/tasks`, `/inbox`).

## Web Workbench Additions (P0/P1)

This pass adds missing high-frequency web workflows while keeping web as a thin adapter.

New inbox routes:
- `POST /inbox/{id}/to-anki`
- `GET /inbox/review`
- `GET /inbox/triage-history`

New task routes:
- `GET /tasks/new`
- `POST /tasks`
- `POST /tasks/{id}/abandon`
- `POST /tasks/{id}/reminders`

New reminder routes:
- `GET /reminders/pending-ack`
- `GET /reminders/{id}`
- `GET /reminders/{id}/history`

New anki route:
- `GET /anki/{id}` (detail page)

New summary route:
- `GET /summary/today`

Journal additions:
- `GET /journal/today`
- `GET /journal?type=<entry_type>&limit=<n>&view=cards|timeline`

Notes:
- Inbox triage web actions now support task / anki / archive / keep on both inbox list and inbox review pages.
- Task detail page includes a minimal reminder creation form.
- All existing business logic remains in service layer; web handlers only orchestrate request/response and rendering.
