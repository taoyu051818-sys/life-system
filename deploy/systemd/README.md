# systemd deployment files

Copy these files to `/etc/systemd/system/`:

- `life-reminders.service`
- `life-reminders.timer`
- `life-telegram-poll.service`
- `life-telegram-poll.timer`
- `life-summary.service`
- `life-summary.timer`

Wrapper scripts expected at:

- `/opt/life-system/scripts/run_reminders.sh`
- `/opt/life-system/scripts/run_telegram_poll.sh`
- `/opt/life-system/scripts/run_summary_today.sh`

Environment file required at:

- `/etc/life-system/life-system.env`

At minimum:

```bash
TELEGRAM_BOT_TOKEN=...
```

