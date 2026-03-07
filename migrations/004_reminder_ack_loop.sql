ALTER TABLE reminders ADD COLUMN requires_ack INTEGER NOT NULL DEFAULT 1;
ALTER TABLE reminders ADD COLUMN ack_at TEXT;
ALTER TABLE reminders ADD COLUMN last_attempt_at TEXT;
ALTER TABLE reminders ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE reminders ADD COLUMN next_retry_at TEXT;
ALTER TABLE reminders ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT 3;
ALTER TABLE reminders ADD COLUMN escalation_level INTEGER NOT NULL DEFAULT 0;
ALTER TABLE reminders ADD COLUMN acked_via TEXT;
ALTER TABLE reminders ADD COLUMN skip_reason TEXT;
ALTER TABLE reminders ADD COLUMN message_ref TEXT;

CREATE TABLE IF NOT EXISTS reminder_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  reminder_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  event_type TEXT NOT NULL,
  event_at TEXT NOT NULL,
  payload TEXT,
  FOREIGN KEY (reminder_id) REFERENCES reminders(id) ON DELETE CASCADE,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_reminder_events_reminder_time ON reminder_events(reminder_id, event_at);
CREATE INDEX IF NOT EXISTS idx_reminder_events_user_time ON reminder_events(user_id, event_at);
CREATE INDEX IF NOT EXISTS idx_reminders_retry ON reminders(status, next_retry_at);

