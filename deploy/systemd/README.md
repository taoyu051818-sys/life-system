# systemd deployment files

Copy these files to `/etc/systemd/system/`:

- `life-reminders.service`
- `life-reminders.timer`
- `life-telegram-poll.service`
- `life-telegram-poll.timer`
- `life-summary.service`
- `life-summary.timer`
- `life-encouragement.service`
- `life-encouragement.timer`

Wrapper scripts expected at:

- `/opt/life-system/scripts/run_reminders.sh`
- `/opt/life-system/scripts/run_telegram_poll.sh`
- `/opt/life-system/scripts/run_summary_today.sh`
- `/opt/life-system/scripts/run_encouragement.sh`

Environment file required at:

- `/etc/life-system/life-system.env`

At minimum:

```bash
TELEGRAM_BOT_TOKEN=...
DEEPSEEK_API_KEY=... # or APIKEY=...
```
