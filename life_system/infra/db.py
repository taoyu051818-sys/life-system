import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

DEFAULT_DB_PATH = Path("data/life_system.db")


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def resolve_db_path(db_path: str | None) -> Path:
    if not db_path:
        return DEFAULT_DB_PATH
    return Path(db_path)


def get_connection(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


@contextmanager
def connection_ctx(db_path: Path) -> Iterator[sqlite3.Connection]:
    conn = get_connection(db_path)
    try:
        yield conn
    finally:
        conn.close()


def ensure_database(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    migrations_dir = Path(__file__).resolve().parents[2] / "migrations"
    with connection_ctx(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              name TEXT NOT NULL UNIQUE,
              applied_at TEXT NOT NULL
            );
            """
        )
        applied = set(_get_applied_migrations(conn))
        migration_files = sorted(migrations_dir.glob("*.sql"))
        for migration in migration_files:
            if migration.name in applied:
                continue
            sql = migration.read_text(encoding="utf-8")
            conn.executescript(sql)
            conn.execute(
                "INSERT INTO schema_migrations(name, applied_at) VALUES(?, ?)",
                (migration.name, now_utc_iso()),
            )
        _ensure_default_users_and_backfill(conn)
        conn.commit()


def _get_applied_migrations(conn: sqlite3.Connection) -> Iterable[str]:
    rows = conn.execute("SELECT name FROM schema_migrations").fetchall()
    return [row["name"] for row in rows]


def _ensure_default_users_and_backfill(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          username TEXT NOT NULL UNIQUE,
          display_name TEXT,
          telegram_chat_id TEXT,
          created_at TEXT NOT NULL
        );
        """
    )

    for username, display_name in (("xiaoyu", "Xiaoyu"), ("partner", "Partner")):
        conn.execute(
            """
            INSERT OR IGNORE INTO users(username, display_name, created_at)
            VALUES(?, ?, ?)
            """,
            (username, display_name, now_utc_iso()),
        )

    row = conn.execute("SELECT id FROM users WHERE username = 'xiaoyu'").fetchone()
    if row is None:
        return
    xiaoyu_id = row["id"]

    for table in ("inbox_items", "tasks", "abandonment_logs", "anki_drafts"):
        conn.execute(f"UPDATE {table} SET user_id = ? WHERE user_id IS NULL", (xiaoyu_id,))
