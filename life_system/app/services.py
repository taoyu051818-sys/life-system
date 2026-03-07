import sqlite3
from csv import DictWriter
from pathlib import Path
from typing import Any

from life_system.domain.ports import EventLogger, NullEventLogger
from life_system.infra.db import now_utc_iso
from life_system.infra.repositories import (
    AbandonmentLogRepository,
    AnkiDraftRepository,
    InboxRepository,
    ReminderRepository,
    TaskRepository,
)


class LifeSystemService:
    def __init__(self, conn: sqlite3.Connection, event_logger: EventLogger | None = None):
        self.inbox_repo = InboxRepository(conn)
        self.task_repo = TaskRepository(conn)
        self.reminder_repo = ReminderRepository(conn)
        self.abandon_repo = AbandonmentLogRepository(conn)
        self.anki_repo = AnkiDraftRepository(conn)
        self.event_logger = event_logger or NullEventLogger()

    def capture_inbox(self, content: str, source: str = "cli") -> int:
        item_id = self.inbox_repo.create(content=content, source=source, created_at=now_utc_iso())
        self.event_logger.log("inbox_captured", {"inbox_item_id": item_id, "source": source})
        return item_id

    def list_inbox(self, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        return self.inbox_repo.list(status=status, limit=limit)

    def triage_inbox_to_task(self, inbox_item_id: int) -> int | None:
        item = self.inbox_repo.get(inbox_item_id)
        if item is None:
            return None
        task_id = self.create_task(title=item["content"], inbox_item_id=inbox_item_id)
        self.event_logger.log("inbox_triaged_to_task", {"inbox_item_id": inbox_item_id, "task_id": task_id})
        return task_id

    def triage_inbox_to_anki(self, inbox_item_id: int) -> int | None:
        item = self.inbox_repo.get(inbox_item_id)
        if item is None:
            return None
        draft_id = self.create_anki_draft(
            source_type="inbox",
            source_id=inbox_item_id,
            front=item["content"],
            back="",
        )
        self.inbox_repo.mark_triaged(inbox_item_id=inbox_item_id, triaged_at=now_utc_iso())
        self.event_logger.log("inbox_triaged_to_anki", {"inbox_item_id": inbox_item_id, "draft_id": draft_id})
        return draft_id

    def archive_inbox(self, inbox_item_id: int) -> bool:
        updated = self.inbox_repo.mark_archived(inbox_item_id)
        if updated:
            self.event_logger.log("inbox_archived", {"inbox_item_id": inbox_item_id})
        return bool(updated)

    def create_task(
        self,
        title: str,
        notes: str | None = None,
        priority: int = 3,
        due_at: str | None = None,
        inbox_item_id: int | None = None,
    ) -> int:
        task_id = self.task_repo.create(
            title=title,
            notes=notes,
            priority=priority,
            due_at=due_at,
            inbox_item_id=inbox_item_id,
            created_at=now_utc_iso(),
        )
        if inbox_item_id is not None:
            self.inbox_repo.mark_triaged(inbox_item_id=inbox_item_id, triaged_at=now_utc_iso())
        self.event_logger.log("task_created", {"task_id": task_id})
        return task_id

    def list_tasks(self, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        return self.task_repo.list(status=status, limit=limit)

    def done_task(self, task_id: int) -> bool:
        updated = self.task_repo.mark_done(task_id=task_id, now=now_utc_iso())
        if updated:
            self.event_logger.log("task_done", {"task_id": task_id})
        return bool(updated)

    def snooze_task(self, task_id: int, snooze_until: str) -> bool:
        updated = self.task_repo.mark_snoozed(task_id=task_id, snooze_until=snooze_until, now=now_utc_iso())
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
        updated = self.task_repo.mark_abandoned(task_id=task_id, now=now)
        if not updated:
            return False
        self.abandon_repo.create(
            task_id=task_id,
            reason_code=reason_code,
            reason_text=reason_text,
            energy_level=energy_level,
            created_at=now,
        )
        self.event_logger.log("task_abandoned", {"task_id": task_id, "reason_code": reason_code})
        return True

    def create_reminder(self, task_id: int, remind_at: str, channel: str = "cli") -> int:
        reminder_id = self.reminder_repo.create(
            task_id=task_id,
            remind_at=remind_at,
            channel=channel,
            created_at=now_utc_iso(),
        )
        self.event_logger.log("reminder_created", {"reminder_id": reminder_id, "task_id": task_id})
        return reminder_id

    def due_reminders(self, now: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        pivot = now or now_utc_iso()
        items = self.reminder_repo.due(now=pivot, limit=limit)
        self.event_logger.log("reminder_due_checked", {"now": pivot, "count": len(items)})
        return items

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
        return self.anki_repo.list(status=status, limit=limit)

    def export_anki_drafts_csv(self, output_path: str) -> int:
        rows = self.anki_repo.list_all()
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
        self.event_logger.log("anki_drafts_exported_csv", {"path": str(path), "count": len(rows)})
        return len(rows)
