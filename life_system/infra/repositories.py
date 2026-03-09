from __future__ import annotations

import sqlite3
from typing import Any


class UserRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def get_by_id(self, user_id: int) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT id, username, display_name, created_at, telegram_chat_id
            FROM users
            WHERE id = ?
            """,
            (user_id,),
        ).fetchone()
        if row is None:
            return None
        return dict(row)

    def get_by_username(self, username: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT id, username, display_name, created_at, telegram_chat_id
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
            SELECT id, username, display_name, created_at, telegram_chat_id
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

    def set_telegram_chat_id(self, username: str, chat_id: str) -> int:
        cur = self.conn.execute(
            "UPDATE users SET telegram_chat_id = ? WHERE username = ?",
            (chat_id, username),
        )
        self.conn.commit()
        return cur.rowcount

    def clear_telegram_chat_id(self, username: str) -> int:
        cur = self.conn.execute(
            "UPDATE users SET telegram_chat_id = NULL WHERE username = ?",
            (username,),
        )
        self.conn.commit()
        return cur.rowcount

    def get_by_telegram_chat_id(self, chat_id: str) -> dict[str, Any] | None:
        normalized = str(chat_id).strip()
        int_like = normalized.lstrip("-").isdigit()
        int_value = int(normalized) if int_like else None
        if int_value is not None:
            row = self.conn.execute(
                """
                SELECT id, username, display_name, created_at, telegram_chat_id
                FROM users
                WHERE telegram_chat_id IS NOT NULL
                  AND (
                    TRIM(CAST(telegram_chat_id AS TEXT)) = ?
                    OR CAST(TRIM(CAST(telegram_chat_id AS TEXT)) AS INTEGER) = ?
                  )
                LIMIT 1
                """,
                (normalized, int_value),
            ).fetchone()
        else:
            row = self.conn.execute(
                """
                SELECT id, username, display_name, created_at, telegram_chat_id
                FROM users
                WHERE telegram_chat_id IS NOT NULL
                  AND TRIM(CAST(telegram_chat_id AS TEXT)) = ?
                LIMIT 1
                """,
                (normalized,),
            ).fetchone()
        if row is None:
            return None
        return dict(row)


class InboxRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create(
        self,
        user_id: int,
        content: str,
        source: str,
        created_at: str,
        source_journal_entry_id: int | None = None,
        created_by: str | None = None,
        rule_name: str | None = None,
        rule_version: str | None = None,
    ) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO inbox_items(
              user_id, content, source, status, created_at,
              source_journal_entry_id, created_by, rule_name, rule_version
            )
            VALUES(?, ?, ?, 'new', ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                content,
                source,
                created_at,
                source_journal_entry_id,
                created_by,
                rule_name,
                rule_version,
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def list(self, user_id: int, status: str | None, limit: int, include_archived: bool = False) -> list[dict[str, Any]]:
        if status:
            rows = self.conn.execute(
                """
                SELECT id, content, source, status, created_at, created_by, rule_name
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
                    SELECT id, content, source, status, created_at, created_by, rule_name
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
                    SELECT id, content, source, status, created_at, created_by, rule_name
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
            SELECT
              id, content, source, status, created_at, triaged_at,
              source_journal_entry_id, created_by, rule_name, rule_version
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
            SET status = 'archived', archived_at = ?
            WHERE id = ? AND user_id = ?
            """,
            (created_at_now(), inbox_item_id, user_id),
        )
        self.conn.commit()
        return cur.rowcount

    def count_captured_by_day(self, user_id: int, day: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM inbox_items WHERE user_id = ? AND created_at LIKE ?",
            (user_id, f"{day}%"),
        ).fetchone()
        return int(row["c"])

    def count_captured_in_range(self, user_id: int, start_iso: str, end_iso: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM inbox_items WHERE user_id = ? AND created_at >= ? AND created_at < ?",
            (user_id, start_iso, end_iso),
        ).fetchone()
        return int(row["c"])

    def count_triaged_by_day(self, user_id: int, day: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM inbox_items WHERE user_id = ? AND triaged_at LIKE ?",
            (user_id, f"{day}%"),
        ).fetchone()
        return int(row["c"])

    def count_triaged_in_range(self, user_id: int, start_iso: str, end_iso: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM inbox_items WHERE user_id = ? AND triaged_at >= ? AND triaged_at < ?",
            (user_id, start_iso, end_iso),
        ).fetchone()
        return int(row["c"])

    def count_archived_by_day(self, user_id: int, day: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM inbox_items WHERE user_id = ? AND archived_at LIKE ?",
            (user_id, f"{day}%"),
        ).fetchone()
        return int(row["c"])

    def count_archived_in_range(self, user_id: int, start_iso: str, end_iso: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM inbox_items WHERE user_id = ? AND archived_at >= ? AND archived_at < ?",
            (user_id, start_iso, end_iso),
        ).fetchone()
        return int(row["c"])

    def count_unprocessed(self, user_id: int) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM inbox_items WHERE user_id = ? AND status = 'new'",
            (user_id,),
        ).fetchone()
        return int(row["c"])

    def oldest_unprocessed_created_at(self, user_id: int) -> str | None:
        row = self.conn.execute(
            """
            SELECT created_at
            FROM inbox_items
            WHERE user_id = ? AND status = 'new'
            ORDER BY created_at ASC, id ASC
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()
        if row is None:
            return None
        return str(row["created_at"])

    def list_auto_created(self, user_id: int, limit: int = 10000) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT
              id, user_id, content, source, status, created_at,
              source_journal_entry_id, created_by, rule_name, rule_version
            FROM inbox_items
            WHERE user_id = ? AND created_by = 'telegram_auto'
            ORDER BY id ASC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def list_new_oldest(self, user_id: int, limit: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT
              id, content, source, status, created_at, triaged_at,
              source_journal_entry_id, created_by, rule_name, rule_version
            FROM inbox_items
            WHERE user_id = ? AND status = 'new'
            ORDER BY created_at ASC, id ASC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]


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

    def count_created_by_day(self, user_id: int, day: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM tasks WHERE user_id = ? AND created_at LIKE ?",
            (user_id, f"{day}%"),
        ).fetchone()
        return int(row["c"])

    def count_created_in_range(self, user_id: int, start_iso: str, end_iso: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM tasks WHERE user_id = ? AND created_at >= ? AND created_at < ?",
            (user_id, start_iso, end_iso),
        ).fetchone()
        return int(row["c"])

    def count_done_by_day(self, user_id: int, day: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM tasks WHERE user_id = ? AND completed_at LIKE ?",
            (user_id, f"{day}%"),
        ).fetchone()
        return int(row["c"])

    def count_done_in_range(self, user_id: int, start_iso: str, end_iso: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM tasks WHERE user_id = ? AND completed_at >= ? AND completed_at < ?",
            (user_id, start_iso, end_iso),
        ).fetchone()
        return int(row["c"])

    def count_snoozed_by_day(self, user_id: int, day: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM tasks WHERE user_id = ? AND status = 'snoozed' AND updated_at LIKE ?",
            (user_id, f"{day}%"),
        ).fetchone()
        return int(row["c"])

    def count_snoozed_in_range(self, user_id: int, start_iso: str, end_iso: str) -> int:
        row = self.conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM tasks
            WHERE user_id = ? AND status = 'snoozed' AND updated_at >= ? AND updated_at < ?
            """,
            (user_id, start_iso, end_iso),
        ).fetchone()
        return int(row["c"])

    def count_abandoned_by_day(self, user_id: int, day: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM tasks WHERE user_id = ? AND abandoned_at LIKE ?",
            (user_id, f"{day}%"),
        ).fetchone()
        return int(row["c"])

    def count_abandoned_in_range(self, user_id: int, start_iso: str, end_iso: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM tasks WHERE user_id = ? AND abandoned_at >= ? AND abandoned_at < ?",
            (user_id, start_iso, end_iso),
        ).fetchone()
        return int(row["c"])

    def count_by_status(self, user_id: int, status: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM tasks WHERE user_id = ? AND status = ?",
            (user_id, status),
        ).fetchone()
        return int(row["c"])


class ReminderRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create(self, task_id: int, remind_at: str, channel: str, created_at: str, requires_ack: bool = True) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO reminders(task_id, remind_at, channel, status, created_at, requires_ack)
            VALUES(?, ?, ?, 'pending', ?, ?)
            """,
            (task_id, remind_at, channel, created_at, 1 if requires_ack else 0),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def list_due_candidates(self, user_id: int, limit: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT
              r.id, r.task_id, r.remind_at, r.channel, r.status, r.created_at,
              r.requires_ack, r.ack_at, r.last_attempt_at, r.attempt_count,
              r.next_retry_at, r.max_attempts, r.escalation_level, r.acked_via,
              r.skip_reason, r.message_ref,
              t.title AS task_title
            FROM reminders r
            JOIN tasks t ON t.id = r.task_id
            WHERE t.user_id = ? AND r.status IN ('pending', 'sent', 'snoozed')
            ORDER BY r.remind_at ASC, r.id ASC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def list_pending_ack(self, user_id: int, limit: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT
              r.id, r.task_id, r.remind_at, r.channel, r.status, r.created_at,
              r.requires_ack, r.ack_at, r.last_attempt_at, r.attempt_count,
              r.next_retry_at, r.max_attempts, r.escalation_level, r.acked_via,
              r.skip_reason, r.message_ref,
              t.title AS task_title
            FROM reminders r
            JOIN tasks t ON t.id = r.task_id
            WHERE
              t.user_id = ?
              AND r.status = 'sent'
              AND r.requires_ack = 1
              AND r.ack_at IS NULL
            ORDER BY r.next_retry_at ASC, r.id ASC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_for_user(self, user_id: int, reminder_id: int) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT
              r.id, r.task_id, r.remind_at, r.channel, r.status, r.created_at,
              r.requires_ack, r.ack_at, r.last_attempt_at, r.attempt_count,
              r.next_retry_at, r.max_attempts, r.escalation_level, r.acked_via,
              r.skip_reason, r.message_ref,
              t.title AS task_title
            FROM reminders r
            JOIN tasks t ON t.id = r.task_id
            WHERE r.id = ? AND t.user_id = ?
            """,
            (reminder_id, user_id),
        ).fetchone()
        if row is None:
            return None
        return dict(row)


    def list_for_user(self, user_id: int, limit: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT
              r.id, r.task_id, r.remind_at, r.channel, r.status, r.created_at,
              r.requires_ack, r.ack_at, r.last_attempt_at, r.attempt_count,
              r.next_retry_at, r.max_attempts, r.escalation_level, r.acked_via,
              r.skip_reason, r.message_ref,
              t.title AS task_title,
              e.event_type AS last_event_type,
              e.event_at AS last_event_at
            FROM reminders r
            JOIN tasks t ON t.id = r.task_id
            LEFT JOIN reminder_events e ON e.id = (
              SELECT re.id
              FROM reminder_events re
              WHERE re.user_id = ? AND re.reminder_id = r.id
              ORDER BY re.event_at DESC, re.id DESC
              LIMIT 1
            )
            WHERE t.user_id = ?
            ORDER BY r.remind_at DESC, r.id DESC
            LIMIT ?
            """,
            (user_id, user_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def update_delivery(
        self,
        reminder_id: int,
        status: str,
        last_attempt_at: str | None,
        attempt_count: int,
        next_retry_at: str | None,
        message_ref: str | None = None,
    ) -> int:
        cur = self.conn.execute(
            """
            UPDATE reminders
            SET status = ?, last_attempt_at = ?, attempt_count = ?, next_retry_at = ?, message_ref = ?
            WHERE id = ?
            """,
            (status, last_attempt_at, attempt_count, next_retry_at, message_ref, reminder_id),
        )
        self.conn.commit()
        return cur.rowcount

    def mark_acknowledged(self, reminder_id: int, ack_at: str, acked_via: str) -> int:
        cur = self.conn.execute(
            """
            UPDATE reminders
            SET
              status = 'acknowledged',
              ack_at = ?,
              acked_via = ?,
              next_retry_at = NULL
            WHERE id = ?
            """,
            (ack_at, acked_via, reminder_id),
        )
        self.conn.commit()
        return cur.rowcount

    def mark_snoozed(self, reminder_id: int, remind_at: str) -> int:
        cur = self.conn.execute(
            """
            UPDATE reminders
            SET
              status = 'snoozed',
              remind_at = ?,
              last_attempt_at = NULL,
              attempt_count = 0,
              next_retry_at = NULL
            WHERE id = ?
            """,
            (remind_at, reminder_id),
        )
        self.conn.commit()
        return cur.rowcount

    def mark_skipped(self, reminder_id: int, skip_reason: str | None) -> int:
        cur = self.conn.execute(
            """
            UPDATE reminders
            SET
              status = 'skipped',
              skip_reason = ?,
              next_retry_at = NULL
            WHERE id = ?
            """,
            (skip_reason, reminder_id),
        )
        self.conn.commit()
        return cur.rowcount

    def mark_expired(self, reminder_id: int) -> int:
        cur = self.conn.execute(
            """
            UPDATE reminders
            SET status = 'expired', next_retry_at = NULL
            WHERE id = ?
            """,
            (reminder_id,),
        )
        self.conn.commit()
        return cur.rowcount

    def mark_failed(self, reminder_id: int, reason: str | None = None) -> int:
        cur = self.conn.execute(
            """
            UPDATE reminders
            SET status = 'failed', next_retry_at = NULL, skip_reason = ?
            WHERE id = ?
            """,
            (reason, reminder_id),
        )
        self.conn.commit()
        return cur.rowcount


class ReminderEventRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create(
        self,
        reminder_id: int,
        user_id: int,
        event_type: str,
        event_at: str,
        payload: str | None,
    ) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO reminder_events(reminder_id, user_id, event_type, event_at, payload)
            VALUES(?, ?, ?, ?, ?)
            """,
            (reminder_id, user_id, event_type, event_at, payload),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def list_for_user(self, user_id: int, reminder_id: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT id, reminder_id, user_id, event_type, event_at, payload
            FROM reminder_events
            WHERE user_id = ? AND reminder_id = ?
            ORDER BY event_at ASC, id ASC
            """,
            (user_id, reminder_id),
        ).fetchall()
        return [dict(row) for row in rows]

    def count_by_day_and_type(self, user_id: int, day: str, event_type: str) -> int:
        row = self.conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM reminder_events
            WHERE user_id = ? AND event_type = ? AND event_at LIKE ?
            """,
            (user_id, event_type, f"{day}%"),
        ).fetchone()
        return int(row["c"])

    def count_in_range_and_type(self, user_id: int, start_iso: str, end_iso: str, event_type: str) -> int:
        row = self.conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM reminder_events
            WHERE user_id = ? AND event_type = ? AND event_at >= ? AND event_at < ?
            """,
            (user_id, event_type, start_iso, end_iso),
        ).fetchone()
        return int(row["c"])


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

    def list(
        self,
        user_id: int,
        status: str | None,
        limit: int,
        deck_name: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = """
            SELECT id, source_type, source_id, deck_name, front, back, tags, status, created_at, exported_at
            FROM anki_drafts
            WHERE user_id = ?
        """
        params: list[Any] = [user_id]
        if status:
            sql += " AND status = ?"
            params.append(status)
        if deck_name:
            sql += " AND deck_name = ?"
            params.append(deck_name)
        sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
        params.append(limit)
        rows = self.conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def list_deck_names(self, user_id: int) -> list[str]:
        rows = self.conn.execute(
            """
            SELECT DISTINCT deck_name
            FROM anki_drafts
            WHERE user_id = ? AND deck_name IS NOT NULL AND deck_name != ''
            ORDER BY deck_name ASC
            """,
            (user_id,),
        ).fetchall()
        return [str(r[0]) for r in rows]

    def list_by_ids(self, user_id: int, draft_ids: list[int]) -> list[dict[str, Any]]:
        if not draft_ids:
            return []
        placeholders = ", ".join("?" for _ in draft_ids)
        rows = self.conn.execute(
            f"""
            SELECT id, source_type, source_id, deck_name, front, back, tags, status, created_at, exported_at
            FROM anki_drafts
            WHERE user_id = ? AND id IN ({placeholders})
            ORDER BY created_at DESC, id DESC
            """,
            [user_id, *draft_ids],
        ).fetchall()
        return [dict(row) for row in rows]

    def count_all(self, user_id: int) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM anki_drafts WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        return int(row["c"])

    def count_non_archived(self, user_id: int) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM anki_drafts WHERE user_id = ? AND status != 'archived'",
            (user_id,),
        ).fetchone()
        return int(row["c"])

    def count_created_since(self, user_id: int, start_iso: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM anki_drafts WHERE user_id = ? AND created_at >= ?",
            (user_id, start_iso),
        ).fetchone()
        return int(row["c"])

    def deck_counts(self, user_id: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT
              COALESCE(NULLIF(deck_name, ''), 'default') AS deck_name,
              COUNT(*) AS draft_total,
              SUM(CASE WHEN status != 'archived' THEN 1 ELSE 0 END) AS draft_non_archived
            FROM anki_drafts
            WHERE user_id = ?
            GROUP BY COALESCE(NULLIF(deck_name, ''), 'default')
            ORDER BY deck_name ASC
            """,
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def list_all(self, user_id: int, only_new: bool = False) -> list[dict[str, Any]]:
        if only_new:
            rows = self.conn.execute(
                """
                SELECT id, source_type, source_id, deck_name, front, back, tags, status, created_at, exported_at
                FROM anki_drafts
                WHERE user_id = ? AND status IN ('draft', 'ready', 'failed')
                ORDER BY id ASC
                """,
                (user_id,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT id, source_type, source_id, deck_name, front, back, tags, status, created_at, exported_at
                FROM anki_drafts
                WHERE user_id = ?
                ORDER BY id ASC
                """,
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_with_trace(self, user_id: int, draft_id: int) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT
              d.id, d.user_id, d.source_type, d.source_id, d.deck_name, d.front, d.back, d.tags, d.status,
              d.created_at, d.exported_at,
              i.id AS source_inbox_item_id,
              i.source_journal_entry_id AS source_journal_entry_id,
              i.source AS source_inbox_source,
              i.created_by AS source_inbox_created_by,
              i.rule_name AS source_inbox_rule_name,
              i.rule_version AS source_inbox_rule_version,
              i.created_at AS source_inbox_created_at,
              j.id AS source_journal_id,
              j.entry_type AS source_journal_entry_type,
              j.created_at AS source_journal_created_at,
              te.id AS source_triage_event_id,
              te.created_by AS source_triage_created_by,
              te.created_at AS source_triage_created_at
            FROM anki_drafts d
            LEFT JOIN inbox_items i
              ON d.source_type = 'inbox'
              AND d.source_id = i.id
              AND i.user_id = d.user_id
            LEFT JOIN journal_entries j
              ON i.source_journal_entry_id = j.id
              AND j.user_id = d.user_id
            LEFT JOIN triage_events te
              ON te.user_id = d.user_id
              AND te.inbox_item_id = i.id
              AND te.target_type = 'anki'
              AND te.target_id = d.id
            WHERE d.user_id = ? AND d.id = ?
            ORDER BY te.id DESC
            LIMIT 1
            """,
            (user_id, draft_id),
        ).fetchone()
        if row is None:
            return None
        return dict(row)

    def archive(self, user_id: int, draft_id: int) -> str:
        row = self.conn.execute(
            """
            SELECT status
            FROM anki_drafts
            WHERE user_id = ? AND id = ?
            """,
            (user_id, draft_id),
        ).fetchone()
        if row is None:
            return "not_found"
        if row["status"] == "archived":
            return "already_archived"
        self.conn.execute(
            """
            UPDATE anki_drafts
            SET status = 'archived'
            WHERE user_id = ? AND id = ?
            """,
            (user_id, draft_id),
        )
        self.conn.commit()
        return "archived"

    def update_fields(
        self,
        user_id: int,
        draft_id: int,
        front: str | None = None,
        back: str | None = None,
        tags: str | None = None,
        deck_name: str | None = None,
    ) -> str:
        row = self.conn.execute(
            """
            SELECT id
            FROM anki_drafts
            WHERE user_id = ? AND id = ?
            """,
            (user_id, draft_id),
        ).fetchone()
        if row is None:
            return "not_found"

        updates: dict[str, Any] = {}
        if front is not None:
            updates["front"] = front
        if back is not None:
            updates["back"] = back
        if tags is not None:
            updates["tags"] = tags
        if deck_name is not None:
            updates["deck_name"] = deck_name

        if not updates:
            return "no_fields"

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        params: list[Any] = [*updates.values(), user_id, draft_id]
        self.conn.execute(
            f"""
            UPDATE anki_drafts
            SET {set_clause}
            WHERE user_id = ? AND id = ?
            """,
            params,
        )
        self.conn.commit()
        return "updated"

    def mark_exported_by_ids(self, user_id: int, draft_ids: list[int], exported_at: str) -> int:
        if not draft_ids:
            return 0
        placeholders = ", ".join("?" for _ in draft_ids)
        params: list[Any] = [exported_at, user_id, *draft_ids]
        cur = self.conn.execute(
            f"""
            UPDATE anki_drafts
            SET status = 'exported', exported_at = ?
            WHERE user_id = ? AND id IN ({placeholders}) AND status IN ('draft', 'ready', 'failed')
            """,
            params,
        )
        self.conn.commit()
        return cur.rowcount

    def mark_exported_for_user(self, user_id: int, exported_at: str) -> int:
        cur = self.conn.execute(
            """
            UPDATE anki_drafts
            SET status = 'exported', exported_at = ?
            WHERE user_id = ? AND status IN ('draft', 'ready', 'failed')
            """,
            (exported_at, user_id),
        )
        self.conn.commit()
        return cur.rowcount

    def count_created_by_day(self, user_id: int, day: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM anki_drafts WHERE user_id = ? AND created_at LIKE ?",
            (user_id, f"{day}%"),
        ).fetchone()
        return int(row["c"])

    def count_created_in_range(self, user_id: int, start_iso: str, end_iso: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM anki_drafts WHERE user_id = ? AND created_at >= ? AND created_at < ?",
            (user_id, start_iso, end_iso),
        ).fetchone()
        return int(row["c"])

    def count_exported_by_day(self, user_id: int, day: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM anki_drafts WHERE user_id = ? AND exported_at LIKE ?",
            (user_id, f"{day}%"),
        ).fetchone()
        return int(row["c"])

    def count_exported_in_range(self, user_id: int, start_iso: str, end_iso: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM anki_drafts WHERE user_id = ? AND exported_at >= ? AND exported_at < ?",
            (user_id, start_iso, end_iso),
        ).fetchone()
        return int(row["c"])


class AnkiCardRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def find_by_dedupe_key(self, user_id: int, dedupe_key: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT
              id, user_id, draft_id, front, back, tags, deck, dedupe_key, state, due_at,
              last_reviewed_at, interval_days, ease_factor, reps, lapses, learning_step,
              created_at, updated_at, archived_at
            FROM anki_cards
            WHERE user_id = ? AND dedupe_key = ?
            LIMIT 1
            """,
            (user_id, dedupe_key),
        ).fetchone()
        return dict(row) if row is not None else None

    def create(
        self,
        user_id: int,
        draft_id: int | None,
        front: str,
        back: str,
        tags: str | None,
        deck: str,
        dedupe_key: str,
        state: str,
        due_at: str,
        created_at: str,
    ) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO anki_cards(
              user_id, draft_id, front, back, tags, deck, dedupe_key, state, due_at,
              interval_days, ease_factor, reps, lapses, learning_step, created_at, updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 2.5, 0, 0, 0, ?, ?)
            """,
            (user_id, draft_id, front, back, tags, deck, dedupe_key, state, due_at, created_at, created_at),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def get(self, user_id: int, card_id: int) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT
              id, user_id, draft_id, front, back, tags, deck, dedupe_key, state, due_at,
              last_reviewed_at, interval_days, ease_factor, reps, lapses, learning_step,
              created_at, updated_at, archived_at
            FROM anki_cards
            WHERE user_id = ? AND id = ?
            """,
            (user_id, card_id),
        ).fetchone()
        return dict(row) if row is not None else None

    def list_due(self, user_id: int, now_iso: str, limit: int, deck_name: str | None = None) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT
              id, user_id, draft_id, front, back, tags, deck, dedupe_key, state, due_at,
              last_reviewed_at, interval_days, ease_factor, reps, lapses, learning_step,
              created_at, updated_at, archived_at
            FROM anki_cards
            WHERE user_id = ?
              AND state IN ('new', 'learning', 'review', 'relearning')
              AND due_at <= ?
              AND (? IS NULL OR deck = ?)
            ORDER BY due_at ASC, id ASC
            LIMIT ?
            """,
            (user_id, now_iso, deck_name, deck_name, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def count_all(self, user_id: int) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM anki_cards WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        return int(row["c"])

    def count_active(self, user_id: int) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM anki_cards WHERE user_id = ? AND state != 'archived'",
            (user_id,),
        ).fetchone()
        return int(row["c"])

    def count_due(self, user_id: int, now_iso: str, deck_name: str | None = None) -> int:
        row = self.conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM anki_cards
            WHERE user_id = ?
              AND state IN ('new', 'learning', 'review', 'relearning')
              AND due_at <= ?
              AND (? IS NULL OR deck = ?)
            """,
            (user_id, now_iso, deck_name, deck_name),
        ).fetchone()
        return int(row["c"])

    def count_created_since(self, user_id: int, start_iso: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM anki_cards WHERE user_id = ? AND created_at >= ?",
            (user_id, start_iso),
        ).fetchone()
        return int(row["c"])

    def deck_counts(self, user_id: int, now_iso: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT
              COALESCE(NULLIF(deck, ''), 'default') AS deck_name,
              COUNT(*) AS active_cards,
              SUM(CASE WHEN state IN ('new', 'learning', 'review', 'relearning') AND due_at <= ? THEN 1 ELSE 0 END) AS due_cards
            FROM anki_cards
            WHERE user_id = ? AND state != 'archived'
            GROUP BY COALESCE(NULLIF(deck, ''), 'default')
            ORDER BY deck_name ASC
            """,
            (now_iso, user_id),
        ).fetchall()
        return [dict(r) for r in rows]

    def update_review_state(
        self,
        user_id: int,
        card_id: int,
        state: str,
        due_at: str,
        last_reviewed_at: str,
        interval_days: int,
        ease_factor: float,
        reps: int,
        lapses: int,
        learning_step: int,
        updated_at: str,
    ) -> int:
        cur = self.conn.execute(
            """
            UPDATE anki_cards
            SET
              state = ?,
              due_at = ?,
              last_reviewed_at = ?,
              interval_days = ?,
              ease_factor = ?,
              reps = ?,
              lapses = ?,
              learning_step = ?,
              updated_at = ?
            WHERE user_id = ? AND id = ?
            """,
            (
                state,
                due_at,
                last_reviewed_at,
                interval_days,
                ease_factor,
                reps,
                lapses,
                learning_step,
                updated_at,
                user_id,
                card_id,
            ),
        )
        self.conn.commit()
        return cur.rowcount


class AnkiReviewEventRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create(
        self,
        user_id: int,
        card_id: int,
        rating: str,
        state_before: str,
        state_after: str,
        due_before: str | None,
        due_after: str | None,
        interval_before: int,
        interval_after: int,
        ease_before: float,
        ease_after: float,
        reviewed_at: str,
    ) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO anki_review_events(
              user_id, card_id, rating, state_before, state_after, due_before, due_after,
              interval_before, interval_after, ease_before, ease_after, reviewed_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                card_id,
                rating,
                state_before,
                state_after,
                due_before,
                due_after,
                interval_before,
                interval_after,
                ease_before,
                ease_after,
                reviewed_at,
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)
    def count_since(self, user_id: int, start_iso: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM anki_review_events WHERE user_id = ? AND reviewed_at >= ?",
            (user_id, start_iso),
        ).fetchone()
        return int(row["c"])

    def rating_distribution_since(self, user_id: int, start_iso: str) -> dict[str, int]:
        rows = self.conn.execute(
            """
            SELECT rating, COUNT(*) AS c
            FROM anki_review_events
            WHERE user_id = ? AND reviewed_at >= ?
            GROUP BY rating
            """,
            (user_id, start_iso),
        ).fetchall()
        result = {"again": 0, "hard": 0, "good": 0, "easy": 0}
        for row in rows:
            key = str(row["rating"])
            if key in result:
                result[key] = int(row["c"])
        return result

class JournalRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create(
        self,
        user_id: int,
        entry_type: str,
        content: str,
        related_task_id: int | None,
        related_inbox_id: int | None,
        energy_level: int | None,
        focus_level: int | None,
        mood_level: int | None,
        tags: str | None,
        created_at: str,
    ) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO journal_entries(
              user_id, entry_type, content, related_task_id, related_inbox_id,
              energy_level, focus_level, mood_level, tags, created_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                entry_type,
                content,
                related_task_id,
                related_inbox_id,
                energy_level,
                focus_level,
                mood_level,
                tags,
                created_at,
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def list(self, user_id: int, limit: int, entry_type: str | None = None) -> list[dict[str, Any]]:
        if entry_type:
            rows = self.conn.execute(
                """
                SELECT
                  id, entry_type, content, related_task_id, related_inbox_id,
                  energy_level, focus_level, mood_level, tags, created_at
                FROM journal_entries
                WHERE user_id = ? AND entry_type = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (user_id, entry_type, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT
                  id, entry_type, content, related_task_id, related_inbox_id,
                  energy_level, focus_level, mood_level, tags, created_at
                FROM journal_entries
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def today(self, user_id: int, day_prefix: str, limit: int, entry_type: str | None = None) -> list[dict[str, Any]]:
        if entry_type:
            rows = self.conn.execute(
                """
                SELECT
                  id, entry_type, content, related_task_id, related_inbox_id,
                  energy_level, focus_level, mood_level, tags, created_at
                FROM journal_entries
                WHERE user_id = ? AND entry_type = ? AND created_at LIKE ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (user_id, entry_type, f"{day_prefix}%", limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT
                  id, entry_type, content, related_task_id, related_inbox_id,
                  energy_level, focus_level, mood_level, tags, created_at
                FROM journal_entries
                WHERE user_id = ? AND created_at LIKE ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (user_id, f"{day_prefix}%", limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def count_by_day(self, user_id: int, day: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM journal_entries WHERE user_id = ? AND created_at LIKE ?",
            (user_id, f"{day}%"),
        ).fetchone()
        return int(row["c"])

    def count_in_range(self, user_id: int, start_iso: str, end_iso: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM journal_entries WHERE user_id = ? AND created_at >= ? AND created_at < ?",
            (user_id, start_iso, end_iso),
        ).fetchone()
        return int(row["c"])

    def avg_state_by_day(self, user_id: int, day: str) -> dict[str, Any]:
        row = self.conn.execute(
            """
            SELECT
              AVG(energy_level) AS avg_energy,
              AVG(focus_level) AS avg_focus,
              AVG(mood_level) AS avg_mood
            FROM journal_entries
            WHERE user_id = ? AND created_at LIKE ?
            """,
            (user_id, f"{day}%"),
        ).fetchone()
        return dict(row)

    def avg_state_in_range(self, user_id: int, start_iso: str, end_iso: str) -> dict[str, Any]:
        row = self.conn.execute(
            """
            SELECT
              AVG(energy_level) AS avg_energy,
              AVG(focus_level) AS avg_focus,
              AVG(mood_level) AS avg_mood
            FROM journal_entries
            WHERE user_id = ? AND created_at >= ? AND created_at < ?
            """,
            (user_id, start_iso, end_iso),
        ).fetchone()
        return dict(row)

    def list_by_day(self, user_id: int, day: str, limit: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT
              id, entry_type, content, related_task_id, related_inbox_id,
              energy_level, focus_level, mood_level, tags, created_at
            FROM journal_entries
            WHERE user_id = ? AND created_at LIKE ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_id, f"{day}%", limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def list_in_range(self, user_id: int, start_iso: str, end_iso: str, limit: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT
              id, entry_type, content, related_task_id, related_inbox_id,
              energy_level, focus_level, mood_level, tags, created_at
            FROM journal_entries
            WHERE user_id = ? AND created_at >= ? AND created_at < ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_id, start_iso, end_iso, limit),
        ).fetchall()
        return [dict(row) for row in rows]


def created_at_now() -> str:
    # local import to avoid cyclic dependency
    from life_system.infra.db import now_utc_iso

    return now_utc_iso()


class AppStateRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def get(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        return str(row["value"])

    def set(self, key: str, value: str, updated_at: str) -> None:
        self.conn.execute(
            """
            INSERT INTO app_state(key, value, updated_at)
            VALUES(?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
            """,
            (key, value, updated_at),
        )
        self.conn.commit()

    def list_prefix(self, key_prefix: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT key, value, updated_at
            FROM app_state
            WHERE key LIKE ?
            ORDER BY key ASC
            """,
            (f"{key_prefix}%",),
        ).fetchall()
        return [dict(row) for row in rows]


class TriageEventRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create(
        self,
        user_id: int,
        inbox_item_id: int,
        action: str,
        target_type: str | None,
        target_id: int | None,
        created_at: str,
        created_by: str,
        source_rule_name: str | None,
        source_rule_version: str | None,
        payload: str | None = None,
    ) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO triage_events(
              user_id, inbox_item_id, action, target_type, target_id,
              created_at, created_by, source_rule_name, source_rule_version, payload
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                inbox_item_id,
                action,
                target_type,
                target_id,
                created_at,
                created_by,
                source_rule_name,
                source_rule_version,
                payload,
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def list_for_inbox(self, user_id: int, inbox_item_id: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT
              id, user_id, inbox_item_id, action, target_type, target_id,
              created_at, created_by, source_rule_name, source_rule_version, payload
            FROM triage_events
            WHERE user_id = ? AND inbox_item_id = ?
            ORDER BY created_at ASC, id ASC
            """,
            (user_id, inbox_item_id),
        ).fetchall()
        return [dict(row) for row in rows]

    def list_recent(self, user_id: int, limit: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT
              id, user_id, inbox_item_id, action, target_type, target_id,
              created_at, created_by, source_rule_name, source_rule_version, payload
            FROM triage_events
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def first_for_inbox(self, user_id: int, inbox_item_id: int) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT
              id, user_id, inbox_item_id, action, target_type, target_id,
              created_at, created_by, source_rule_name, source_rule_version, payload
            FROM triage_events
            WHERE user_id = ? AND inbox_item_id = ?
            ORDER BY created_at ASC, id ASC
            LIMIT 1
            """,
            (user_id, inbox_item_id),
        ).fetchone()
        if row is None:
            return None
        return dict(row)

    def first_in_window(self, user_id: int, start_at: str, end_at: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT
              id, user_id, inbox_item_id, action, target_type, target_id,
              created_at, created_by, source_rule_name, source_rule_version, payload
            FROM triage_events
            WHERE user_id = ? AND created_at >= ? AND created_at <= ?
            ORDER BY created_at ASC, id ASC
            LIMIT 1
            """,
            (user_id, start_at, end_at),
        ).fetchone()
        if row is None:
            return None
        return dict(row)


class InboxFeedbackSignalRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create_if_absent(
        self,
        user_id: int,
        subject_type: str,
        subject_key: str,
        signal_type: str,
        window_hours: int | None,
        created_at: str,
        source_rule_name: str | None,
        source_rule_version: str | None,
        payload: str | None,
    ) -> bool:
        try:
            self.conn.execute(
                """
                INSERT INTO inbox_feedback_signals(
                  user_id, subject_type, subject_key, signal_type, window_hours, created_at,
                  source_rule_name, source_rule_version, payload
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    subject_type,
                    subject_key,
                    signal_type,
                    window_hours,
                    created_at,
                    source_rule_name,
                    source_rule_version,
                    payload,
                ),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def list_recent(self, user_id: int, limit: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT
              id, user_id, subject_type, subject_key, signal_type, window_hours,
              created_at, source_rule_name, source_rule_version, payload
            FROM inbox_feedback_signals
            WHERE user_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]




