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

## Abandonment Reason Presets

- overwhelm
- wrong_timing
- no_value
- impulse
- blocked

##retries are scheduled step-by-step when the processor runs
