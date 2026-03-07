CREATE TABLE IF NOT EXISTS journal_entries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  entry_type TEXT NOT NULL,
  content TEXT NOT NULL,
  related_task_id INTEGER,
  related_inbox_id INTEGER,
  energy_level INTEGER,
  focus_level INTEGER,
  mood_level INTEGER,
  tags TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY (related_task_id) REFERENCES tasks(id) ON DELETE SET NULL,
  FOREIGN KEY (related_inbox_id) REFERENCES inbox_items(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_journal_user_created ON journal_entries(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_journal_user_type_created ON journal_entries(user_id, entry_type, created_at);

