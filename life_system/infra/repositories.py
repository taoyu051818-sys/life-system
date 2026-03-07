import sqlite3
from typing import Any


class InboxRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create(self, content: str, source: str, created_at: str) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO inbox_items(content, source, status, created_at)
            VALUES(?, ?, 'new', ?)
            """,
            (content, source, created_at),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def list(self, status: str | None, limit: int) -> list[dict[str, Any]]:
        if status:
            rows = self.conn.execute(
                """
                SELECT id, content, source, status, created_at
                FROM inbox_items
                WHERE status = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (status, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT id, content, source, status, created_at
                FROM inbox_items
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_triaged(self, inbox_item_id: int, triaged_at: str) -> int:
        cur = self.conn.execute(
            """
            UPDATE inbox_items
            SET status = 'triaged', triaged_at = ?
            WHERE id = ?
            """,
            (triaged_at, inbox_item_id),
        )
        self.conn.commit()
        return cur.rowcount


class TaskRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create(
        self,
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
              title, notes, status, priority, due_at, inbox_item_id, created_at, updated_at
            )
            VALUES(?, ?, 'open', ?, ?, ?, ?, ?)
            """,
            (title, notes, priority, due_at, inbox_item_id, created_at, created_at),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def list(self, status: str | None, limit: int) -> list[dict[str, Any]]:
        if status:
            rows = self.conn.execute(
                """
                SELECT id, title, notes, status, priority, due_at, snooze_until, created_at, updated_at
                FROM tasks
                WHERE status = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (status, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT id, title, notes, status, priority, due_at, snooze_until, created_at, updated_at
                FROM tasks
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_done(self, task_id: int, now: str) -> int:
        cur = self.conn.execute(
            """
            UPDATE tasks
            SET status = 'done', completed_at = ?, snooze_until = NULL, updated_at = ?
            WHERE id = ?
            """,
            (now, now, task_id),
        )
        self.conn.commit()
        return cur.rowcount

    def mark_abandoned(self, task_id: int, now: str) -> int:
        cur = self.conn.execute(
            """
            UPDATE tasks
            SET status = 'abandoned', abandoned_at = ?, snooze_until = NULL, updated_at = ?
            WHERE id = ?
            """,
            (now, now, task_id),
        )
        self.conn.commit()
        return cur.rowcount

    def mark_snoozed(self, task_id: int, snooze_until: str, now: str) -> int:
        cur = self.conn.execute(
            """
            UPDATE tasks
            SET status = 'snoozed', snooze_until = ?, updated_at = ?
            WHERE id = ?
            """,
            (snooze_until, now, task_id),
        )
        self.conn.commit()
        return cur.rowcount


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

    def due(self, now: str, limit: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT r.id, r.task_id, r.remind_at, r.channel, r.status, t.title AS task_title
            FROM reminders r
            JOIN tasks t ON t.id = r.task_id
            WHERE r.status = 'pending' AND r.remind_at <= ?
            ORDER BY r.remind_at ASC, r.id ASC
            LIMIT ?
            """,
            (now, limit),
        ).fetchall()
        return [dict(row) for row in rows]


class AbandonmentLogRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create(
        self,
        task_id: int,
        reason_code: str | None,
        reason_text: str | None,
        energy_level: int | None,
        created_at: str,
    ) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO abandonment_logs(task_id, reason_code, reason_text, energy_level, created_at)
            VALUES(?, ?, ?, ?, ?)
            """,
            (task_id, reason_code, reason_text, energy_level, created_at),
        )
        self.conn.commit()
        return int(cur.lastrowid)


class AnkiDraftRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create(
        self,
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
              source_type, source_id, deck_name, front, back, tags, status, created_at
            )
            VALUES(?, ?, ?, ?, ?, ?, 'draft', ?)
            """,
            (source_type, source_id, deck_name, front, back, tags, created_at),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def list(self, status: str | None, limit: int) -> list[dict[str, Any]]:
        if status:
            rows = self.conn.execute(
                """
                SELECT id, source_type, source_id, deck_name, front, back, tags, status, created_at
                FROM anki_drafts
                WHERE status = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (status, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT id, source_type, source_id, deck_name, front, back, tags, status, created_at
                FROM anki_drafts
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]
