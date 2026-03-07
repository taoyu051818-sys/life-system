import json
import sqlite3
from csv import DictWriter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from life_system.domain.ports import EventLogger, NullEventLogger
from life_system.infra.db import now_utc_iso
from life_system.infra.repositories import (
    AbandonmentLogRepository,
    AnkiDraftRepository,
    InboxRepository,
    JournalRepository,
    ReminderEventRepository,
    ReminderRepository,
    TaskRepository,
)

RETRY_MINUTES = [10, 30, 120]
CST = timezone(timedelta(hours=8), name="Asia/Shanghai")


class LifeSystemService:
    def __init__(
        self,
        conn: sqlite3.Connection,
        user_id: int,
        username: str,
        telegram_chat_id: str | None = None,
        reminder_sender: Any | None = None,
        event_logger: EventLogger | None = None,
    ):
        self.user_id = user_id
        self.username = username
        self.telegram_chat_id = telegram_chat_id
        self.reminder_sender = reminder_sender
        self.inbox_repo = InboxRepository(conn)
        self.task_repo = TaskRepository(conn)
        self.reminder_repo = ReminderRepository(conn)
        self.reminder_event_repo = ReminderEventRepository(conn)
        self.abandon_repo = AbandonmentLogRepository(conn)
        self.anki_repo = AnkiDraftRepository(conn)
        self.journal_repo = JournalRepository(conn)
        self.event_logger = event_logger or NullEventLogger()

    def capture_inbox(self, content: str, source: str = "cli") -> int:
        item_id = self.inbox_repo.create(
            user_id=self.user_id,
            content=content,
            source=source,
            created_at=now_utc_iso(),
        )
        self.event_logger.log("inbox_captured", {"inbox_item_id": item_id, "username": self.username})
        return item_id

    def list_inbox(
        self,
        status: str | None = None,
        limit: int = 50,
        include_archived: bool = False,
    ) -> list[dict[str, Any]]:
        return self.inbox_repo.list(
            user_id=self.user_id,
            status=status,
            limit=limit,
            include_archived=include_archived,
        )

    def triage_inbox_to_task(self, inbox_item_id: int) -> int | None:
        item = self.inbox_repo.get(user_id=self.user_id, inbox_item_id=inbox_item_id)
        if item is None:
            return None
        task_id = self.create_task(title=item["content"], inbox_item_id=inbox_item_id)
        if task_id is None:
            return None
        self.event_logger.log("inbox_triaged_to_task", {"inbox_item_id": inbox_item_id, "task_id": task_id})
        return task_id

    def triage_inbox_to_anki(self, inbox_item_id: int) -> int | None:
        item = self.inbox_repo.get(user_id=self.user_id, inbox_item_id=inbox_item_id)
        if item is None:
            return None
        draft_id = self.create_anki_draft(
            source_type="inbox",
            source_id=inbox_item_id,
            front=item["content"],
            back="",
        )
        self.inbox_repo.mark_triaged(user_id=self.user_id, inbox_item_id=inbox_item_id, triaged_at=now_utc_iso())
        self.event_logger.log("inbox_triaged_to_anki", {"inbox_item_id": inbox_item_id, "draft_id": draft_id})
        return draft_id

    def archive_inbox(self, inbox_item_id: int) -> str:
        item = self.inbox_repo.get(user_id=self.user_id, inbox_item_id=inbox_item_id)
        if item is None:
            return "not_found"
        if item["status"] == "archived":
            return "already_archived"
        updated = self.inbox_repo.mark_archived(user_id=self.user_id, inbox_item_id=inbox_item_id)
        if not updated:
            return "not_found"
        self.event_logger.log("inbox_archived", {"inbox_item_id": inbox_item_id})
        return "archived"

    def create_task(
        self,
        title: str,
        notes: str | None = None,
        priority: int = 3,
        due_at: str | None = None,
        inbox_item_id: int | None = None,
    ) -> int | None:
        if inbox_item_id is not None:
            item = self.inbox_repo.get(user_id=self.user_id, inbox_item_id=inbox_item_id)
            if item is None:
                return None
        task_id = self.task_repo.create(
            user_id=self.user_id,
            title=title,
            notes=notes,
            priority=priority,
            due_at=due_at,
            inbox_item_id=inbox_item_id,
            created_at=now_utc_iso(),
        )
        if inbox_item_id is not None:
            self.inbox_repo.mark_triaged(user_id=self.user_id, inbox_item_id=inbox_item_id, triaged_at=now_utc_iso())
        self.event_logger.log("task_created", {"task_id": task_id, "username": self.username})
        return task_id

    def list_tasks(self, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        return self.task_repo.list(user_id=self.user_id, status=status, limit=limit)

    def done_task(self, task_id: int) -> bool:
        updated = self.task_repo.mark_done(user_id=self.user_id, task_id=task_id, now=now_utc_iso())
        if updated:
            self.event_logger.log("task_done", {"task_id": task_id})
        return bool(updated)

    def snooze_task(self, task_id: int, snooze_until: str) -> bool:
        updated = self.task_repo.mark_snoozed(
            user_id=self.user_id,
            task_id=task_id,
            snooze_until=snooze_until,
            now=now_utc_iso(),
        )
        if updated:
            self.event_logger.log("task_snoozed", {"task_id": task_id, "snooze_until": snooze_until})
        return bool(updated)

    def abandon_task(
        self,
        task_id: int,
        reason_code: str | None = None,
        reason_text: str | None = None,
        energy_level: int | None = None,
    ) -> bool:
        now = now_utc_iso()
        updated = self.task_repo.mark_abandoned(user_id=self.user_id, task_id=task_id, now=now)
        if not updated:
            return False
        self.abandon_repo.create(
            user_id=self.user_id,
            task_id=task_id,
            reason_code=reason_code,
            reason_text=reason_text,
            energy_level=energy_level,
            created_at=now,
        )
        self.event_logger.log("task_abandoned", {"task_id": task_id, "reason_code": reason_code})
        return True

    def create_reminder(self, task_id: int, remind_at: str, channel: str = "cli") -> int | None:
        task = self.task_repo.get(user_id=self.user_id, task_id=task_id)
        if task is None:
            return None
        reminder_id = self.reminder_repo.create(
            task_id=task_id,
            remind_at=remind_at,
            channel=channel,
            created_at=now_utc_iso(),
        )
        self._log_reminder_event(reminder_id, "created", {"task_id": task_id, "channel": channel})
        self.event_logger.log("reminder_created", {"reminder_id": reminder_id, "task_id": task_id})
        return reminder_id

    def due_reminders(self, now: str | None = None, limit: int = 50, send: bool = False) -> list[dict[str, Any]]:
        pivot_iso = now or now_utc_iso()
        pivot_dt = self._parse_iso(pivot_iso)
        candidates = self.reminder_repo.list_due_candidates(user_id=self.user_id, limit=limit * 5)
        due: list[dict[str, Any]] = []
        for item in candidates:
            is_due, parse_error = self._is_due_with_error(item, pivot_dt)
            if parse_error:
                if send:
                    self.reminder_repo.mark_failed(item["id"], reason=parse_error)
                    self._log_reminder_event(item["id"], "failed", {"reason": parse_error})
                continue
            if is_due:
                due.append(item)
        due = due[:limit]
        if not send:
            self.event_logger.log("reminder_due_checked", {"now": pivot_iso, "count": len(due)})
            return due

        result = self.send_due_reminders(now=now, limit=limit)
        return result["items"]

    def send_due_reminders(self, now: str | None = None, limit: int = 50) -> dict[str, Any]:
        pivot_iso = now or now_utc_iso()
        pivot_dt = self._parse_iso(pivot_iso)
        candidates = self.reminder_repo.list_due_candidates(user_id=self.user_id, limit=limit * 5)
        due: list[dict[str, Any]] = []
        for item in candidates:
            is_due, parse_error = self._is_due_with_error(item, pivot_dt)
            if parse_error:
                self.reminder_repo.mark_failed(item["id"], reason=parse_error)
                self._log_reminder_event(item["id"], "failed", {"reason": parse_error})
                continue
            if is_due:
                due.append(item)
        due = due[:limit]

        if self.telegram_chat_id and self.reminder_sender is None:
            return {"error": "missing_telegram_token", "items": [], "processed": 0, "failed": len(due)}

        result: list[dict[str, Any]] = []
        failed = 0
        for item in due:
            processed = self._deliver_and_update(item, pivot_dt)
            if processed is None:
                failed += 1
            else:
                result.append(processed)
        self.event_logger.log("reminder_due_sent", {"now": pivot_iso, "count": len(result), "failed": failed})
        return {"error": None, "items": result, "processed": len(result), "failed": failed}

    def list_pending_ack_reminders(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.reminder_repo.list_pending_ack(user_id=self.user_id, limit=limit)

    def ack_reminder(self, reminder_id: int, acked_via: str = "cli") -> str:
        item = self.reminder_repo.get_for_user(user_id=self.user_id, reminder_id=reminder_id)
        if item is None:
            return "not_found"
        if item["status"] == "acknowledged":
            return "already_acknowledged"
        now = now_utc_iso()
        updated = self.reminder_repo.mark_acknowledged(reminder_id=reminder_id, ack_at=now, acked_via=acked_via)
        if not updated:
            return "not_found"
        self._log_reminder_event(reminder_id, "acknowledged", {"acked_via": acked_via})
        return "acknowledged"

    def snooze_reminder(self, reminder_id: int, remind_at: str) -> str:
        item = self.reminder_repo.get_for_user(user_id=self.user_id, reminder_id=reminder_id)
        if item is None:
            return "not_found"
        if item["status"] == "snoozed" and item["remind_at"] == remind_at:
            return "already_snoozed_same"
        updated = self.reminder_repo.mark_snoozed(reminder_id=reminder_id, remind_at=remind_at)
        if not updated:
            return "not_found"
        self._log_reminder_event(reminder_id, "snoozed", {"remind_at": remind_at})
        return "snoozed"

    def skip_reminder(self, reminder_id: int, reason: str | None = None) -> str:
        item = self.reminder_repo.get_for_user(user_id=self.user_id, reminder_id=reminder_id)
        if item is None:
            return "not_found"
        if item["status"] == "skipped":
            return "already_skipped"
        updated = self.reminder_repo.mark_skipped(reminder_id=reminder_id, skip_reason=reason)
        if not updated:
            return "not_found"
        self._log_reminder_event(reminder_id, "skipped", {"reason": reason})
        return "skipped"

    def show_reminder(self, reminder_id: int) -> dict[str, Any] | None:
        return self.reminder_repo.get_for_user(user_id=self.user_id, reminder_id=reminder_id)

    def reminder_history(self, reminder_id: int) -> list[dict[str, Any]] | None:
        item = self.reminder_repo.get_for_user(user_id=self.user_id, reminder_id=reminder_id)
        if item is None:
            return None
        return self.reminder_event_repo.list_for_user(user_id=self.user_id, reminder_id=reminder_id)

    def create_anki_draft(
        self,
        source_type: str,
        source_id: int | None,
        front: str,
        back: str,
        deck_name: str = "inbox",
        tags: str | None = None,
    ) -> int:
        draft_id = self.anki_repo.create(
            user_id=self.user_id,
            source_type=source_type,
            source_id=source_id,
            deck_name=deck_name,
            front=front,
            back=back,
            tags=tags,
            created_at=now_utc_iso(),
        )
        self.event_logger.log("anki_draft_created", {"draft_id": draft_id, "source_type": source_type})
        return draft_id

    def list_anki_drafts(self, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        return self.anki_repo.list(user_id=self.user_id, status=status, limit=limit)

    def export_anki_drafts_csv(self, output_path: str) -> int:
        rows = self.anki_repo.list_all(user_id=self.user_id)
        path = Path(output_path)
        if path.parent != Path("."):
            path.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = [
            "id",
            "source_type",
            "source_id",
            "deck_name",
            "front",
            "back",
            "tags",
            "status",
            "created_at",
        ]
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        self.anki_repo.mark_exported_for_user(user_id=self.user_id, exported_at=now_utc_iso())
        self.event_logger.log("anki_drafts_exported_csv", {"path": str(path), "count": len(rows)})
        return len(rows)

    def add_journal_entry(
        self,
        content: str,
        entry_type: str,
        related_task_id: int | None = None,
        related_inbox_id: int | None = None,
        energy_level: int | None = None,
        focus_level: int | None = None,
        mood_level: int | None = None,
        tags: str | None = None,
    ) -> int:
        entry_id = self.journal_repo.create(
            user_id=self.user_id,
            entry_type=entry_type,
            content=content,
            related_task_id=related_task_id,
            related_inbox_id=related_inbox_id,
            energy_level=energy_level,
            focus_level=focus_level,
            mood_level=mood_level,
            tags=tags,
            created_at=now_utc_iso(),
        )
        self.event_logger.log("journal_added", {"entry_id": entry_id, "entry_type": entry_type})
        return entry_id

    def list_journal(self, limit: int = 50, entry_type: str | None = None) -> list[dict[str, Any]]:
        return self.journal_repo.list(user_id=self.user_id, limit=limit, entry_type=entry_type)

    def today_journal(self, limit: int = 50, entry_type: str | None = None) -> list[dict[str, Any]]:
        day_prefix = datetime.now(timezone.utc).date().isoformat()
        return self.journal_repo.today(
            user_id=self.user_id,
            day_prefix=day_prefix,
            limit=limit,
            entry_type=entry_type,
        )

    def build_day_summary(self, day: str) -> dict[str, Any]:
        start_utc, end_utc = self._cst_day_to_utc_range(day)
        overview = {
            "inbox_captured": self.inbox_repo.count_captured_in_range(self.user_id, start_utc, end_utc),
            "inbox_triaged": self.inbox_repo.count_triaged_in_range(self.user_id, start_utc, end_utc),
            "inbox_archived": self.inbox_repo.count_archived_in_range(self.user_id, start_utc, end_utc),
            "tasks_created": self.task_repo.count_created_in_range(self.user_id, start_utc, end_utc),
            "tasks_done": self.task_repo.count_done_in_range(self.user_id, start_utc, end_utc),
            "tasks_snoozed": self.task_repo.count_snoozed_in_range(self.user_id, start_utc, end_utc),
            "tasks_abandoned": self.task_repo.count_abandoned_in_range(self.user_id, start_utc, end_utc),
            "reminders_sent": self.reminder_event_repo.count_in_range_and_type(self.user_id, start_utc, end_utc, "sent"),
            "reminders_retried": self.reminder_event_repo.count_in_range_and_type(
                self.user_id, start_utc, end_utc, "retried"
            ),
            "reminders_acknowledged": self.reminder_event_repo.count_in_range_and_type(
                self.user_id, start_utc, end_utc, "acknowledged"
            ),
            "reminders_skipped": self.reminder_event_repo.count_in_range_and_type(
                self.user_id, start_utc, end_utc, "skipped"
            ),
            "reminders_expired": self.reminder_event_repo.count_in_range_and_type(
                self.user_id, start_utc, end_utc, "expired"
            ),
            "anki_created": self.anki_repo.count_created_in_range(self.user_id, start_utc, end_utc),
            "anki_exported": self.anki_repo.count_exported_in_range(self.user_id, start_utc, end_utc),
            "journal_count": self.journal_repo.count_in_range(self.user_id, start_utc, end_utc),
        }

        journal_rows = self.journal_repo.list_in_range(self.user_id, start_utc, end_utc, limit=8)
        grouped: dict[str, list[dict[str, Any]]] = {"activity": [], "reflection": [], "win": [], "checkin": []}
        for row in journal_rows:
            et = row["entry_type"]
            if et in grouped and len(grouped[et]) < 2:
                grouped[et].append(row)

        state = self.journal_repo.avg_state_in_range(self.user_id, start_utc, end_utc)
        state_snapshot = {
            "avg_energy": state.get("avg_energy"),
            "avg_focus": state.get("avg_focus"),
            "avg_mood": state.get("avg_mood"),
        }

        open_loops = {
            "open_tasks": self.task_repo.count_by_status(self.user_id, "open"),
            "snoozed_tasks": self.task_repo.count_by_status(self.user_id, "snoozed"),
            "pending_ack": len(self.reminder_repo.list_pending_ack(self.user_id, limit=10000)),
        }

        note = self._build_summary_note(overview, open_loops)

        return {
            "day": day,
            "overview": overview,
            "journal_grouped": grouped,
            "state_snapshot": state_snapshot,
            "open_loops": open_loops,
            "note": note,
        }

    def build_today_summary(self) -> dict[str, Any]:
        day = datetime.now(CST).date().isoformat()
        return self.build_day_summary(day)

    def _deliver_and_update(self, item: dict[str, Any], now_dt: datetime) -> dict[str, Any] | None:
        reminder_id = item["id"]
        status = item["status"]
        attempt_count = int(item.get("attempt_count") or 0)
        max_attempts = int(item.get("max_attempts") or 3)
        requires_ack = bool(item.get("requires_ack"))

        if status == "sent" and requires_ack and attempt_count >= max_attempts:
            self.reminder_repo.mark_expired(reminder_id)
            self._log_reminder_event(reminder_id, "expired", {"attempt_count": attempt_count})
            return self.reminder_repo.get_for_user(self.user_id, reminder_id)

        try:
            message_ref = self._deliver_reminder_message(item)
        except Exception:
            self._log_reminder_event(reminder_id, "failed", {"reason": "delivery_failed"})
            return None

        new_attempt = attempt_count + 1
        next_retry_at = None
        if requires_ack:
            retry_minutes = RETRY_MINUTES[min(new_attempt - 1, len(RETRY_MINUTES) - 1)]
            next_retry_at = self._to_iso(now_dt + timedelta(minutes=retry_minutes))

        self.reminder_repo.update_delivery(
            reminder_id=reminder_id,
            status="sent",
            last_attempt_at=self._to_iso(now_dt),
            attempt_count=new_attempt,
            next_retry_at=next_retry_at,
            message_ref=message_ref,
        )

        event_type = "retried" if status == "sent" else "sent"
        self._log_reminder_event(
            reminder_id,
            event_type,
            {"attempt_count": new_attempt, "next_retry_at": next_retry_at},
        )
        return self.reminder_repo.get_for_user(self.user_id, reminder_id)

    def _deliver_reminder_message(self, item: dict[str, Any]) -> str:
        message = (
            f"提醒：{item['task_title']}\n"
            f"用户：{self.username}\n"
            f"提醒时间：{item['remind_at']}\n"
            f"提醒编号：{item['id']}\n"
            "回复仍暂时通过 CLI 处理"
        )
        if self.telegram_chat_id and self.reminder_sender is not None:
            if hasattr(self.reminder_sender, "send_reminder"):
                return self.reminder_sender.send_reminder(self.telegram_chat_id, message, int(item["id"]))
            return self.reminder_sender.send_message(self.telegram_chat_id, message)
        return "cli_fallback"

    def _is_due_with_error(self, item: dict[str, Any], now_dt: datetime) -> tuple[bool, str | None]:
        status = item["status"]
        try:
            if status in ("pending", "snoozed"):
                return self._parse_iso(item["remind_at"]) <= now_dt, None

            if status == "sent":
                if not bool(item.get("requires_ack")):
                    return False, None
                if item.get("ack_at"):
                    return False, None
                next_retry = item.get("next_retry_at")
                if not next_retry:
                    return False, None
                return self._parse_iso(next_retry) <= now_dt, None
        except ValueError:
            return False, "invalid_datetime_in_reminder"

        return False, None

    def _log_reminder_event(self, reminder_id: int, event_type: str, payload: dict[str, Any]) -> None:
        self.reminder_event_repo.create(
            reminder_id=reminder_id,
            user_id=self.user_id,
            event_type=event_type,
            event_at=now_utc_iso(),
            payload=json.dumps(payload, ensure_ascii=True),
        )

    def _parse_iso(self, value: str) -> datetime:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    def _to_iso(self, value: datetime) -> str:
        return value.replace(microsecond=0).isoformat()

    def _cst_day_to_utc_range(self, day: str) -> tuple[str, str]:
        local_start = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=CST)
        local_end = local_start + timedelta(days=1)
        utc_start = local_start.astimezone(timezone.utc).replace(microsecond=0).isoformat()
        utc_end = local_end.astimezone(timezone.utc).replace(microsecond=0).isoformat()
        return utc_start, utc_end

    def _build_summary_note(self, overview: dict[str, int], open_loops: dict[str, int]) -> str:
        done = overview["tasks_done"]
        journal_count = overview["journal_count"]
        pending_ack = open_loops["pending_ack"]
        if done > 0 and journal_count > 0:
            return "今天有持续记录，也有实际推进，可以继续保持这种小步前进。"
        if done > 0:
            return "今天有真实完成项，节奏是稳定的。"
        if journal_count > 0:
            return "今天留下了清晰的活动和状态证据，说明你没有脱离系统。"
        if pending_ack > 0:
            return "今天虽然正式完成项不多，但有真实记录和闭环动作。"
        return "今天证据还不多，先补一条简短记录会更稳。"
