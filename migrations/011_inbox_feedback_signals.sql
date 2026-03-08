CREATE TABLE IF NOT EXISTS inbox_feedback_signals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  subject_type TEXT NOT NULL,
  subject_key TEXT NOT NULL,
  signal_type TEXT NOT NULL,
  window_hours INTEGER,
  created_at TEXT NOT NULL,
  source_rule_name TEXT,
  source_rule_version TEXT,
  payload TEXT,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_feedback_user_created
ON inbox_feedback_signals(user_id, created_at);

CREATE INDEX IF NOT EXISTS idx_feedback_subject
ON inbox_feedback_signals(user_id, subject_type, subject_key);

CREATE UNIQUE INDEX IF NOT EXISTS uq_feedback_signal
ON inbox_feedback_signals(user_id, subject_type, subject_key, signal_type);
