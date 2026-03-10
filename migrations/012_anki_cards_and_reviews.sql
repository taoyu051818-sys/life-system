PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS anki_cards (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  draft_id INTEGER,
  front TEXT NOT NULL,
  back TEXT NOT NULL,
  tags TEXT,
  deck TEXT NOT NULL DEFAULT 'default',
  dedupe_key TEXT,
  state TEXT NOT NULL DEFAULT 'new',
  due_at TEXT NOT NULL,
  last_reviewed_at TEXT,
  interval_days INTEGER NOT NULL DEFAULT 0,
  ease_factor REAL NOT NULL DEFAULT 2.5,
  reps INTEGER NOT NULL DEFAULT 0,
  lapses INTEGER NOT NULL DEFAULT 0,
  learning_step INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  archived_at TEXT,
  FOREIGN KEY (draft_id) REFERENCES anki_drafts(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS anki_review_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  card_id INTEGER NOT NULL,
  rating TEXT NOT NULL,
  state_before TEXT NOT NULL,
  state_after TEXT NOT NULL,
  due_before TEXT,
  due_after TEXT,
  interval_before INTEGER,
  interval_after INTEGER,
  ease_before REAL,
  ease_after REAL,
  reviewed_at TEXT NOT NULL,
  FOREIGN KEY (card_id) REFERENCES anki_cards(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_anki_cards_user_due ON anki_cards(user_id, state, due_at);
CREATE INDEX IF NOT EXISTS idx_anki_review_events_user_time ON anki_review_events(user_id, reviewed_at);

INSERT INTO anki_cards(
  user_id, draft_id, front, back, tags, deck, dedupe_key,
  state, due_at, interval_days, ease_factor, reps, lapses, learning_step,
  created_at, updated_at
)
SELECT
  d.user_id,
  d.id,
  d.front,
  d.back,
  d.tags,
  COALESCE(NULLIF(d.deck_name, ''), 'default') AS deck,
  lower(trim(d.front)) || '|' || lower(trim(d.back)) || '|' || lower(trim(COALESCE(NULLIF(d.deck_name, ''), 'default'))) AS dedupe_key,
  CASE WHEN d.status = 'archived' THEN 'archived' ELSE 'new' END AS state,
  d.created_at,
  0,
  2.5,
  0,
  0,
  0,
  d.created_at,
  d.created_at
FROM anki_drafts d
JOIN (
  SELECT
    user_id,
    lower(trim(front)) || '|' || lower(trim(back)) || '|' || lower(trim(COALESCE(NULLIF(deck_name, ''), 'default'))) AS dedupe_key,
    MIN(id) AS min_id
  FROM anki_drafts
  GROUP BY user_id, dedupe_key
) uniq ON uniq.min_id = d.id
WHERE NOT EXISTS (
  SELECT 1 FROM anki_cards c WHERE c.draft_id = d.id
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_anki_cards_user_dedupe ON anki_cards(user_id, dedupe_key)
WHERE dedupe_key IS NOT NULL;
