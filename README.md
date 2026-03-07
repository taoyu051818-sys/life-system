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
python -m life_system.main capture "买维生素"            # alias of inbox capture
python -m life_system.main inbox capture "买维生素"
python -m life_system.main inbox list

# Task
python -m life_system.main task create "背单词"
python -m life_system.main task create "修水杯" --inbox-id 1
python -m life_system.main task list
python -m life_system.main task snooze 1 2026-03-08T09:00:00+08:00
python -m life_system.main task done 1
python -m life_system.main task abandon 2 --reason-code overwhelm --reason-text "拆分太难" --energy-level 2

# Reminder
python -m life_system.main reminder create 1 2026-03-08T10:00:00+08:00
python -m life_system.main reminder due

# Anki Draft
python -m life_system.main anki create manual "番茄钟多长" "25分钟"
python -m life_system.main anki list
```

