CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE,
  display_name TEXT,
  created_at TEXT NOT NULL
);

ALTER TABLE inbox_items ADD COLUMN user_id INTEGER;
ALTER TABLE tasks ADD COLUMN user_id INTEGER;
ALTER TABLE abandonment_logs ADD COLUMN user_id INTEGER;
ALTER TABLE anki_drafts ADD COLUMN user_id INTEGER;

CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
CREATE INDEX IF NOT EXISTS idx_inbox_user_created ON inbox_items(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_tasks_user_updated ON tasks(user_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_abandon_user_created ON abandonment_logs(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_anki_user_created ON anki_drafts(user_id, created_at);

