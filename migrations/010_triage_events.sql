CREATE TABLE IF NOT EXISTS triage_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  inbox_item_id INTEGER NOT NULL,
  action TEXT NOT NULL,
  target_type TEXT,
  target_id INTEGER,
  created_at TEXT NOT NULL,
  created_by TEXT NOT NULL,
  source_rule_name TEXT,
  source_rule_version TEXT,
  payload TEXT,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY (inbox_item_id) REFERENCES inbox_items(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_triage_events_user_inbox_created
ON triage_events(user_id, inbox_item_id, created_at);

CREATE INDEX IF NOT EXISTS idx_triage_events_user_created
ON triage_events(user_id, created_at);
