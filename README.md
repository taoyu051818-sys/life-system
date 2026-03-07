# life-system

Low-resource, CLI-first personal life system (Python + SQLite).

## Run

```bash
python -m life_system.main init-db
python -m life_system.main --help
```

## Core Commands

```bash
# Inbox
python -m life_system.main capture "buy vitamins"             # alias of inbox capture
python -m life_system.main inbox capture "buy vitamins"
python -m life_system.main inbox list
python -m life_system.main inbox triage 1 task
python -m life_system.main inbox triage 2 anki
python -m life_system.main inbox triage 3 archive

# Task
python -m life_system.main task create "word review"
python -m life_system.main task create "fix cup" --inbox-id 1
python -m life_system.main task list
python -m life_system.main task snooze 1 2026-03-08T09:00:00+08:00
python -m life_system.main task done 1
python -m life_system.main task abandon 2 --reason-code overwhelm --reason-text "too large" --energy-level 2

# Reminder
python -m life_system.main reminder create 1 2026-03-08T10:00:00+08:00
python -m life_system.main reminder due

# Anki Draft
python -m life_system.main anki create manual "What is next action?" "A concrete next step"
python -m life_system.main anki list
python -m life_system.main anki export-csv data/anki_drafts.csv
```

## Abandonment Reason Presets

- overwhelm
- wrong_timing
- no_value
- impulse
- blocked

