ALTER TABLE inbox_items ADD COLUMN source_journal_entry_id INTEGER;
ALTER TABLE inbox_items ADD COLUMN created_by TEXT;
ALTER TABLE inbox_items ADD COLUMN rule_name TEXT;
ALTER TABLE inbox_items ADD COLUMN rule_version TEXT;

UPDATE inbox_items
SET created_by = 'manual'
WHERE created_by IS NULL;

CREATE INDEX IF NOT EXISTS idx_inbox_user_status_created ON inbox_items(user_id, status, created_at);
