from __future__ import annotations

import sqlite3
from typing import Any


class UserRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def get_by_username(self, username: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT id, username, display_name, created_at
            FROM users
            WHERE username = ?
            """,
            (username,),
        ).fetchone()
        if row is None:
            return None
        return dict(row)

    def list_all(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT id, username, display_name, created_at
            FROM users
            ORDER BY username ASC
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def add(self, username: str, display_name: str | None, created_at: str) -> int | None:
        try:
            cur = self.conn.execute(
                """
                INSERT INTO users(username, display_name, created_at)
                VALUES(?, ?, ?)
                """,
                (username, display_name, created_at),
            )
            self.conn.commit()
            return int(cur.lastrowid)
        except sqlite3.IntegrityError:
            return None


class InboxRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create(self, user_id: int, content: str, source: str, created_at: str) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO inbox_items(user_id, content, source, status, created_at)
            VALUES(?, ?, ?, 'new', ?)
            """,
            (user_id, content, source, created_at),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def list(self, user_id: int, status: str | None, limit: int, include_archived: bool = False) -> list[dict[str, Any]]:
        if status:
            rows = self.conn.execute(
                """
                SELECT id, content, source, status, created_at
                FROM inbox_items
                WHERE user_id = ? AND status = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (user_id, status, limit),
            ).fetchall()
        else:
            if include_archived:
                rows = self.conn.execute(
                    """
                    SELECT id, content, source, status, created_at
                    FROM inbox_items
                    WHERE user_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (user_id, limit),
                ).fetchall()
            else:
                rows = self.conn.execute(
                    """
                    SELECT id, content, source, status, created_at
                    FROM inbox_items
                    WHERE user_id = ? AND status != 'archived'
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (user_id, limit),
                ).fetchall()
        return [dict(row) for row in rows]

    def get(self, user_id: int, inbox_item_id: int) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT id, content, source, status, created_at, triaged_at
            FROM inbox_items
            WHERE id = ? AND user_id = ?
            """,
            (inbox_item_id, user_id),
        ).fetchone()
        if row is None:
            return None
        return dict(row)

    def mark_triaged(self, user_id: int, inbox_item_id: int, triaged_at: str) -> int:
        cur = self.conn.execute(
            """
            UPDATE inbox_items
            SET status = 'triaged', triaged_at = ?
            WHERE id = ? AND user_id = ?
            """,
            (triaged_at, inbox_item_id, user_id),
        )
        self.conn.commit()
        return cur.rowcount

    def mark_archived(self, user_id: int, inbox_item_id: int) -> int:
        cur = self.conn.execute(
            """
            UPDATE inbox_items
            SET status = 'archived'
            WHERE id = ? AND user_id = ?
            """,
            (inbox_item_id, user_id),
        )
        self.conn.commit()
        return cur.rowcount


class TaskRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create(
        self,
        user_id: int,
        title: str,
        notes: str | None,
        priority: int,
        due_at: str | None,
        inbox_item_id: int | None,
        created_at: str,
    ) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO tasks(
              user_id, title, notes, status, priority, due_at, inbox_item_id, created_at, updated_at
            )
            VALUES(?, ?, ?, 'open', ?, ?, ?, ?, ?)
            """,
            (user_id, title, notes, priority, due_at, inbox_item_id, created_at, created_at),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def list(self, user_id: int, status: str | None, limit: int) -> list[dict[str, Any]]:
        if status:
            rows = self.conn.execute(
                """
                SELECT id, title, notes, status, priority, due_at, snooze_until, created_at, updated_at
                FROM tasks
                WHERE user_id = ? AND status = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (user_id, status, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT id, title, notes, status, priority, due_at, snooze_until, created_at, updated_at
                FROM tasks
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_done(self, user_id: int, task_id: int, now: str) -> int:
        cur = self.conn.execute(
            """
            UPDATE tasks
            SET status = 'done', completed_at = ?, snooze_until = NULL, updated_at = ?
            WHERE id = ? AND user_id = ?
            """,
            (now, now, task_id, user_id),
        )
        self.conn.commit()
        return cur.rowcount

    def mark_abandoned(self, user_id: int, task_id: int, now: str) -> int:
        cur = self.conn.execute(
            """
            UPDATE tasks
            SET status = 'abandoned', abandoned_at = ?, snooze_until = NULL, updated_at = ?
            WHERE id = ? AND user_id = ?
            """,
            (now, now, task_id, user_id),
        )
        self.conn.commit()
        return cur.rowcount

    def mark_snoozed(self, user_id: int, task_id: int, snooze_until: str, now: str) -> int:
        cur = self.conn.execute(
            """
            UPDATE tasks
            SET status = 'snoozed', snooze_until = ?, updated_at = ?
            WHERE id = ? AND user_id = ?
            """,
            (snooze_until, now, task_id, user_id),
        )
        self.conn.commit()
        return cur.rowcount

    def get(self, user_id: int, task_id: int) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT id, user_id, title, status
            FROM tasks
            WHERE id = ? AND user_id = ?
            """,
            (task_id, user_id),
        ).fetchone()
        if row is None:
            return None
        return dict(row)


class ReminderRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create(self, task_id: int, remind_at: str, channel: str, created_at: str) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO reminders(task_id, remind_at, channel, status, created_at)
            VALUES(?, ?, ?, 'pending', ?)
            """,
            (task_id, remind_at, channel, created_at),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def due(self, user_id: int, now: str, limit: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT r.id, r.task_id, r.remind_at, r.channel, r.status, t.title AS task_title
            FROM reminders r
            JOIN tasks t ON t.id = r.task_id
            WHERE r.status = 'pending' AND r.remind_at <= ? AND t.user_id = ?
            ORDER BY r.remind_at ASC, r.id ASC
            LIMIT ?
            """,
            (now, user_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]


class AbandonmentLogRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create(
        self,
        user_id: int,
        task_id: int,
        reason_code: str | None,
        reason_text: str | None,
        energy_level: int | None,
        created_at: str,
    ) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO abandonment_logs(user_id, task_id, reason_code, reason_text, energy_level, created_at)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (user_id, task_id, reason_code, reason_text, energy_level, created_at),
        )
        self.conn.commit()
        return int(cur.lastrowid)


class AnkiDraftRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create(
        self,
        user_id: int,
        source_type: str,
        source_id: int | None,
        deck_name: str,
        front: str,
        back: str,
        tags: str | None,
        created_at: str,
    ) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO anki_drafts(
              user_id, source_type, source_id, deck_name, front, back, tags, status, created_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, 'draft', ?)
            """,
            (user_id, source_type, source_id, deck_name, front, back, tags, created_at),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def list(self, user_id: int, status: str | None, limit: int) -> list[dict[str, Any]]:
        if status:
            rows = self.conn.execute(
                """
                SELECT id, source_type, source_id, deck_name, front, back, tags, status, created_at
                FROM anki_drafts
                WHERE user_id = ? AND status = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (user_id, status, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT id, source_type, source_id, deck_name, front, back, tags, status, created_at
                FROM anki_drafts
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_all(self, user_id: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT id, source_type, source_id, deck_name, front, back, tags, status, created_at
            FROM anki_drafts
            WHERE user_id = ?
            ORDER BY id ASC
            """,
            (user_id,),
        ).fetchall()
        return [dict(row) for row in rows]
