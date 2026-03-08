from __future__ import annotations

import sqlite3
from typing import Any


class UserRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

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
