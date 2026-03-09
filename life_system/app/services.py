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
    AppStateRepository,
    InboxFeedbackSignalRepository,
    InboxRepository,
    JournalRepository,
    ReminderEventRepository,
    ReminderRepository,
    TaskRepository,
    TriageEventRepository,
    UserRepository,
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
        self.triage_event_repo = TriageEventRepository(conn)
        self.feedback_repo = InboxFeedbackSignalRepository(conn)
        self.state_repo = AppStateRepository(conn)
        self.event_logger = event_logger or NullEventLogger()
        self._nonfatal_warnings: list[str] = []

    def capture_inbox(
        self,
        content: str,
        source: str = "cli",
        source_journal_entry_id: int | None = None,
        created_by: str | None = "manual",
        rule_name: str | None = None,
        rule_version: str | None = None,
    ) -> int:
        item_id = self.inbox_repo.create(
            user_id=self.user_id,
            content=content,
            source=source,
            created_at=now_utc_iso(),
            source_journal_entry_id=source_journal_entry_id,
            created_by=created_by,
            rule_name=rule_name,
            rule_version=rule_version,
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

    def triage_inbox_to_task(self, inbox_item_id: int, created_by: str = "manual") -> int | None:
        item = self.inbox_repo.get(user_id=self.user_id, inbox_item_id=inbox_item_id)
        if item is None:
            return None
        if not self._is_inbox_triage_allowed(item):
            return None
        task_id = self.create_task(title=item["content"], inbox_item_id=inbox_item_id)
        if task_id is None:
            return None
        self._record_triage_event(
            inbox_item_id=inbox_item_id,
            action="to_task",
            target_type="task",
            target_id=task_id,
            created_by=created_by,
            source_rule_name=item.get("rule_name"),
            source_rule_version=item.get("rule_version"),
        )
        self.event_logger.log("inbox_triaged_to_task", {"inbox_item_id": inbox_item_id, "task_id": task_id})
        return task_id

    def triage_inbox_to_anki(self, inbox_item_id: int, created_by: str = "manual") -> int | None:
        item = self.inbox_repo.get(user_id=self.user_id, inbox_item_id=inbox_item_id)
        if item is None:
            return None
        if not self._is_inbox_triage_allowed(item):
            return None
        draft_id = self.create_anki_draft(
            source_type="inbox",
            source_id=inbox_item_id,
            front=item["content"],
            back="",
        )
        self.inbox_repo.mark_triaged(user_id=self.user_id, inbox_item_id=inbox_item_id, triaged_at=now_utc_iso())
        self._record_triage_event(
            inbox_item_id=inbox_item_id,
            action="to_anki",
            target_type="anki",
            target_id=draft_id,
            created_by=created_by,
            source_rule_name=item.get("rule_name"),
            source_rule_version=item.get("rule_version"),
        )
        self.event_logger.log("inbox_triaged_to_anki", {"inbox_item_id": inbox_item_id, "draft_id": draft_id})
        return draft_id

    def archive_inbox(self, inbox_item_id: int, created_by: str = "manual") -> str:
        item = self.inbox_repo.get(user_id=self.user_id, inbox_item_id=inbox_item_id)
        if item is None:
            return "not_found"
        if item["status"] == "archived":
            return "already_archived"
        if item.get("status") != "new" or item.get("triaged_at"):
            return "already_triaged"
        updated = self.inbox_repo.mark_archived(user_id=self.user_id, inbox_item_id=inbox_item_id)
        if not updated:
            return "not_found"
        self._record_triage_event(
            inbox_item_id=inbox_item_id,
            action="to_archive",
            target_type="archive",
            target_id=None,
            created_by=created_by,
            source_rule_name=item.get("rule_name"),
            source_rule_version=item.get("rule_version"),
        )
        self.event_logger.log("inbox_archived", {"inbox_item_id": inbox_item_id})
        return "archived"

    def list_new_inbox_oldest(self, limit: int = 5) -> list[dict[str, Any]]:
        return self.inbox_repo.list_new_oldest(user_id=self.user_id, limit=limit)

    def inbox_history(self, inbox_item_id: int) -> list[dict[str, Any]] | None:
        item = self.inbox_repo.get(user_id=self.user_id, inbox_item_id=inbox_item_id)
        if item is None:
            return None
        return self.triage_event_repo.list_for_inbox(user_id=self.user_id, inbox_item_id=inbox_item_id)

    def triage_history(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.triage_event_repo.list_recent(user_id=self.user_id, limit=limit)

    def feedback_scan(self, now: str | None = None) -> dict[str, int]:
        now_iso = now or now_utc_iso()
        now_dt = self._parse_iso(now_iso)
        stats = {
            "scanned_auto_inbox": 0,
            "scanned_review_sends": 0,
            "created_signals": 0,
            "skipped_existing": 0,
            "failed": 0,
        }

        auto_items = self.inbox_repo.list_auto_created(user_id=self.user_id)
        for item in auto_items:
            stats["scanned_auto_inbox"] += 1
            subject_key = f"inbox:{item['id']}"
            try:
                first = self.triage_event_repo.first_for_inbox(user_id=self.user_id, inbox_item_id=int(item["id"]))
                created_dt = self._parse_iso(str(item["created_at"]))
                within_24_end = created_dt + timedelta(hours=24)
                if first:
                    triage_dt = self._parse_iso(str(first["created_at"]))
                    if triage_dt <= within_24_end:
                        target_type = str(first.get("target_type") or "")
                        action = str(first.get("action") or "")
                        signal_type = None
                        if target_type == "task" or action == "to_task":
                            signal_type = "auto_to_task_24h"
                        elif target_type == "anki" or action == "to_anki":
                            signal_type = "auto_to_anki_24h"
                        elif target_type == "archive" or action == "to_archive":
                            signal_type = "auto_to_archive_24h"
                        if signal_type:
                            delay_hours = max(0, int((triage_dt - created_dt).total_seconds() // 3600))
                            payload = json.dumps(
                                {
                                    "inbox_item_id": item["id"],
                                    "first_triage_event_id": first["id"],
                                    "first_target_type": first.get("target_type"),
                                    "first_target_id": first.get("target_id"),
                                    "delay_hours": delay_hours,
                                },
                                ensure_ascii=True,
                            )
                            self._create_feedback_signal(
                                stats=stats,
                                subject_type="auto_inbox",
                                subject_key=subject_key,
                                signal_type=signal_type,
                                window_hours=24,
                                source_rule_name=item.get("rule_name"),
                                source_rule_version=item.get("rule_version"),
                                payload=payload,
                                created_at=now_iso,
                            )
                else:
                    if str(item.get("status")) == "new" and now_dt >= (created_dt + timedelta(hours=72)):
                        payload = json.dumps(
                            {
                                "inbox_item_id": item["id"],
                                "note": "still pending after 72h",
                            },
                            ensure_ascii=True,
                        )
                        self._create_feedback_signal(
                            stats=stats,
                            subject_type="auto_inbox",
                            subject_key=subject_key,
                            signal_type="auto_pending_72h",
                            window_hours=72,
                            source_rule_name=item.get("rule_name"),
                            source_rule_version=item.get("rule_version"),
                            payload=payload,
                            created_at=now_iso,
                        )
            except Exception:
                stats["failed"] += 1
                continue

        review_rows = self.state_repo.list_prefix(f"inbox_review_sent:{self.user_id}:")
        for row in review_rows:
            stats["scanned_review_sends"] += 1
            key = str(row["key"])
            try:
                sent_at = self._parse_iso(str(row["updated_at"]))
                end_at = sent_at + timedelta(hours=24)
                first_triage = self.triage_event_repo.first_in_window(
                    user_id=self.user_id,
                    start_at=self._to_iso(sent_at),
                    end_at=self._to_iso(end_at),
                )
                if first_triage:
                    delay_hours = max(0, int((self._parse_iso(str(first_triage["created_at"])) - sent_at).total_seconds() // 3600))
                    payload = json.dumps(
                        {
                            "review_sent_at": self._to_iso(sent_at),
                            "triage_happened_at": first_triage["created_at"],
                            "delay_hours": delay_hours,
                            "first_triage_event_id": first_triage["id"],
                        },
                        ensure_ascii=True,
                    )
                    self._create_feedback_signal(
                        stats=stats,
                        subject_type="inbox_review",
                        subject_key=key,
                        signal_type="review_led_to_triage_24h",
                        window_hours=24,
                        source_rule_name=None,
                        source_rule_version=None,
                        payload=payload,
                        created_at=now_iso,
                    )
                elif now_dt >= end_at:
                    payload = json.dumps(
                        {
                            "review_sent_at": self._to_iso(sent_at),
                            "note": "no triage in 24h",
                        },
                        ensure_ascii=True,
                    )
                    self._create_feedback_signal(
                        stats=stats,
                        subject_type="inbox_review",
                        subject_key=key,
                        signal_type="review_no_triage_24h",
                        window_hours=24,
                        source_rule_name=None,
                        source_rule_version=None,
                        payload=payload,
                        created_at=now_iso,
                    )
            except Exception:
                stats["failed"] += 1
                continue

        return stats

    def feedback_report(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.feedback_repo.list_recent(user_id=self.user_id, limit=limit)

    def pop_nonfatal_warnings(self) -> list[str]:
        out = self._nonfatal_warnings[:]
        self._nonfatal_warnings.clear()
        return out

    def inbox_triage_status(self, inbox_item_id: int) -> str:
        item = self.inbox_repo.get(user_id=self.user_id, inbox_item_id=inbox_item_id)
        if item is None:
            return "not_found"
        if item["status"] == "archived":
            return "already_archived"
        if not self._is_inbox_triage_allowed(item):
            return "already_triaged"
        return "ok"

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


    def get_task_detail(self, task_id: int) -> dict[str, Any] | None:
        rows = self.task_repo.list(user_id=self.user_id, status=None, limit=1000)
        for row in rows:
            if int(row["id"]) == task_id:
                return row
        return None

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


    def list_reminders(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.reminder_repo.list_for_user(user_id=self.user_id, limit=limit)

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

    def show_anki_draft(self, draft_id: int) -> dict[str, Any] | None:
        return self.anki_repo.get_with_trace(user_id=self.user_id, draft_id=draft_id)

    def archive_anki_draft(self, draft_id: int) -> str:
        status = self.anki_repo.archive(user_id=self.user_id, draft_id=draft_id)
        if status == "archived":
            self.event_logger.log("anki_draft_archived", {"draft_id": draft_id})
        return status

    def update_anki_draft(
        self,
        draft_id: int,
        front: str | None = None,
        back: str | None = None,
        tags: str | None = None,
        deck_name: str | None = None,
    ) -> str:
        status = self.anki_repo.update_fields(
            user_id=self.user_id,
            draft_id=draft_id,
            front=front,
            back=back,
            tags=tags,
            deck_name=deck_name,
        )
        if status == "updated":
            changed = [k for k, v in {"front": front, "back": back, "tags": tags, "deck_name": deck_name}.items() if v is not None]
            self.event_logger.log("anki_draft_updated", {"draft_id": draft_id, "fields": changed})
        return status

    def import_anki_json(self, raw_json: str) -> dict[str, Any]:
        text = raw_json.strip()
        if not text:
            return {"ok": False, "created": 0, "errors": [{"index": None, "reason": "empty_json"}], "ids": []}
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            return {
                "ok": False,
                "created": 0,
                "errors": [{"index": None, "reason": f"invalid_json: {exc.msg}"}],
                "ids": [],
            }

        cards = data if isinstance(data, list) else [data]
        errors: list[dict[str, Any]] = []
        normalized: list[dict[str, Any]] = []

        for idx, item in enumerate(cards, start=1):
            if not isinstance(item, dict):
                errors.append({"index": idx, "reason": "item_not_object"})
                continue

            front = str(item.get("front", "")).strip()
            back = str(item.get("back", "")).strip()
            if not front:
                errors.append({"index": idx, "reason": "missing_front"})
            if not back:
                errors.append({"index": idx, "reason": "missing_back"})
            if not front or not back:
                continue

            deck = str(item.get("deck", "default") or "default").strip() or "default"
            tags_obj = item.get("tags")
            tags: str | None = None
            if isinstance(tags_obj, list):
                if not all(isinstance(t, str) for t in tags_obj):
                    errors.append({"index": idx, "reason": "invalid_tags_array"})
                    continue
                tags = ",".join([t.strip() for t in tags_obj if t.strip()])
            elif isinstance(tags_obj, str):
                tags = tags_obj.strip() or None
            elif tags_obj is None:
                tags = None
            else:
                errors.append({"index": idx, "reason": "invalid_tags_type"})
                continue

            normalized.append({"front": front, "back": back, "deck": deck, "tags": tags})

        if errors:
            return {"ok": False, "created": 0, "errors": errors, "ids": []}

        ids: list[int] = []
        for card in normalized:
            draft_id = self.create_anki_draft(
                source_type="manual",
                source_id=None,
                front=card["front"],
                back=card["back"],
                deck_name=card["deck"],
                tags=card["tags"],
            )
            ids.append(draft_id)
        return {"ok": True, "created": len(ids), "errors": [], "ids": ids}

    def export_anki_drafts_csv(self, output_path: str, only_new: bool = False) -> int:
        rows = self.anki_repo.list_all(user_id=self.user_id, only_new=only_new)
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
            "exported_at",
        ]
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        draft_ids = [int(row["id"]) for row in rows if str(row.get("status")) in {"draft", "ready", "failed"}]
        exported_at = now_utc_iso()
        self.anki_repo.mark_exported_by_ids(user_id=self.user_id, draft_ids=draft_ids, exported_at=exported_at)
        self.event_logger.log(
            "anki_drafts_exported_csv",
            {"path": str(path), "count": len(rows), "only_new": only_new, "marked_exported": len(draft_ids)},
        )
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


    def _record_triage_event(
        self,
        inbox_item_id: int,
        action: str,
        target_type: str | None,
        target_id: int | None,
        created_by: str,
        source_rule_name: str | None,
        source_rule_version: str | None,
    ) -> None:
        try:
            self.triage_event_repo.create(
                user_id=self.user_id,
                inbox_item_id=inbox_item_id,
                action=action,
                target_type=target_type,
                target_id=target_id,
                created_at=now_utc_iso(),
                created_by=created_by,
                source_rule_name=source_rule_name,
                source_rule_version=source_rule_version,
                payload=None,
            )
        except Exception:
            self._nonfatal_warnings.append(f"triage_event_write_failed inbox_id={inbox_item_id} action={action}")

    def _is_inbox_triage_allowed(self, item: dict[str, Any]) -> bool:
        if item.get("status") != "new":
            return False
        if item.get("triaged_at"):
            return False
        return True

    def _create_feedback_signal(
        self,
        stats: dict[str, int],
        subject_type: str,
        subject_key: str,
        signal_type: str,
        window_hours: int | None,
        source_rule_name: str | None,
        source_rule_version: str | None,
        payload: str | None,
        created_at: str,
    ) -> None:
        try:
            created = self.feedback_repo.create_if_absent(
                user_id=self.user_id,
                subject_type=subject_type,
                subject_key=subject_key,
                signal_type=signal_type,
                window_hours=window_hours,
                created_at=created_at,
                source_rule_name=source_rule_name,
                source_rule_version=source_rule_version,
                payload=payload,
            )
        except Exception:
            stats["failed"] += 1
            return
        if created:
            stats["created_signals"] += 1
        else:
            stats["skipped_existing"] += 1


class InboxReviewService:
    REVIEW_WINDOW_HOUR = 20
    REVIEW_WINDOW_MINUTE = 30
    MAX_SNOOZE_PER_DAY = 3

    def __init__(self, conn: sqlite3.Connection, telegram_sender: Any | None = None):
        self.conn = conn
        self.telegram_sender = telegram_sender
        self.user_repo = UserRepository(conn)
        self.inbox_repo = InboxRepository(conn)
        self.state_repo = AppStateRepository(conn)

    def review_due(self, now: str | None = None) -> dict[str, Any]:
        return self._run(now=now, send=False)

    def review_send(self, now: str | None = None) -> dict[str, Any]:
        return self._run(now=now, send=True)

    def send_inbox_review_items_for_user(self, user_id: int, limit: int = 5) -> int:
        user = self.user_repo.get_by_id(user_id)
        if user is None:
            return 0
        items = self.inbox_repo.list_new_oldest(user_id=user_id, limit=limit)
        if not items:
            return 0

        chat_id = user.get("telegram_chat_id")
        sent = 0
        for item in items:
            if chat_id and self.telegram_sender is not None and hasattr(self.telegram_sender, "send_inbox_review_item"):
                try:
                    self.telegram_sender.send_inbox_review_item(str(chat_id), int(item["id"]), str(item["content"]))
                    sent += 1
                except Exception:
                    continue
            else:
                print(f"[{user['username']}] 【收件箱】#{item['id']} {item['content']}")
                sent += 1
        return sent

    def handle_session_action(
        self,
        user_id: int,
        day: str,
        action: str,
        now: str | None = None,
        review_limit: int = 5,
    ) -> dict[str, Any]:
        now_iso = now or now_utc_iso()
        session = self._load_session(user_id=user_id, day=day)
        if session is None:
            return {"ok": False, "message": "今日会话不存在或已失效"}

        status = str(session.get("status") or "pending")
        if action == "start":
            if status == "skipped":
                return {"ok": False, "message": "今天已跳过"}
            sent = self.send_inbox_review_items_for_user(user_id=user_id, limit=review_limit)
            if sent <= 0:
                return {"ok": False, "message": "当前没有可回顾 inbox"}
            session["status"] = "started"
            session["started_at"] = now_iso
            session["last_action"] = "start_review"
            self._save_session(user_id=user_id, day=day, session=session, updated_at=now_iso)
            return {"ok": True, "message": "已开始回顾", "sent": sent}

        if action == "snooze":
            if status in {"started", "skipped"}:
                return {"ok": False, "message": "已经处理过了"}
            snooze_count = int(session.get("snooze_count") or 0)
            if snooze_count >= self.MAX_SNOOZE_PER_DAY:
                return {"ok": False, "message": "今天延后次数已达上限"}
            due_at = self._parse_iso(str(session["due_at"])) + timedelta(minutes=30)
            session["due_at"] = self._to_iso(due_at)
            session["snooze_count"] = snooze_count + 1
            session["status"] = "snoozed"
            session["last_action"] = "snooze_30m"
            self._save_session(user_id=user_id, day=day, session=session, updated_at=now_iso)
            return {"ok": True, "message": "已延后半小时", "due_at": session["due_at"]}

        if action == "skip":
            if status == "skipped":
                return {"ok": False, "message": "今天已跳过"}
            session["status"] = "skipped"
            session["skipped_at"] = now_iso
            session["last_action"] = "skip_today"
            self._save_session(user_id=user_id, day=day, session=session, updated_at=now_iso)
            return {"ok": True, "message": "今天已跳过"}

        return {"ok": False, "message": "无法识别操作"}

    def _run(self, now: str | None, send: bool) -> dict[str, Any]:
        now_iso = now or now_utc_iso()
        now_dt = self._parse_iso(now_iso)
        now_cst = now_dt.astimezone(CST)
        day_cst = now_cst.date().isoformat()
        base_due_dt = self._session_base_due(day_cst)

        stats: dict[str, Any] = {
            "checked_users": 0,
            "sent": 0,
            "skipped_empty": 0,
            "skipped_already_sent": 0,
            "escalated": 0,
            "fallback_cli": 0,
            "failed": 0,
            "skipped_before_window": 0,
        }

        users = self.user_repo.list_all()
        for user in users:
            stats["checked_users"] += 1
            user_id = int(user["id"])
            pending = self.inbox_repo.count_unprocessed(user_id=user_id)
            if pending <= 0:
                stats["skipped_empty"] += 1
                continue

            session = self._load_or_create_session(user_id=user_id, day=day_cst, now_iso=now_iso, base_due_dt=base_due_dt)
            status = str(session.get("status") or "pending")
            if status in {"started", "skipped"}:
                stats["skipped_already_sent"] += 1
                continue

            due_at = self._parse_iso(str(session["due_at"]))
            if now_dt < due_at:
                stats["skipped_before_window"] += 1
                continue

            if session.get("last_offered_due_at") == session.get("due_at"):
                stats["skipped_already_sent"] += 1
                continue

            oldest_created_at = self.inbox_repo.oldest_unprocessed_created_at(user_id=user_id)
            oldest_hours = self._oldest_age_hours(now_dt, oldest_created_at)
            escalated = pending >= 7 or oldest_hours >= 72
            if escalated:
                stats["escalated"] += 1

            if not send:
                continue

            delivered = False
            message_id = None
            chat_id = user.get("telegram_chat_id")
            allow_snooze = int(session.get("snooze_count") or 0) < self.MAX_SNOOZE_PER_DAY
            if chat_id:
                if self.telegram_sender is None:
                    stats["failed"] += 1
                else:
                    try:
                        if hasattr(self.telegram_sender, "send_auto_inbox_review_entry"):
                            day_compact = day_cst.replace("-", "")
                            message_id = self.telegram_sender.send_auto_inbox_review_entry(
                                str(chat_id),
                                day_compact,
                                pending,
                                escalated,
                                allow_snooze,
                            )
                        else:
                            msg = self._build_legacy_message(
                                username=str(user["username"]),
                                pending=pending,
                                oldest_hours=oldest_hours,
                                escalated=escalated,
                            )
                            message_id = self.telegram_sender.send_message(str(chat_id), msg)
                        delivered = True
                    except Exception:
                        stats["failed"] += 1
            else:
                msg = self._build_legacy_message(
                    username=str(user["username"]),
                    pending=pending,
                    oldest_hours=oldest_hours,
                    escalated=escalated,
                )
                print(f"[{user['username']}] {msg}")
                stats["fallback_cli"] += 1
                delivered = True

            if delivered:
                stats["sent"] += 1
                session["status"] = "offered"
                session["sent_at"] = now_iso
                session["last_action"] = "offer"
                session["last_offered_due_at"] = session["due_at"]
                session["offered_message_id"] = message_id
                self._save_session(user_id=user_id, day=day_cst, session=session, updated_at=now_iso)
                self._mark_review_sent_compat(user_id=user_id, day=day_cst, now_iso=now_iso)

        return stats

    def _session_key(self, user_id: int, day: str) -> str:
        return f"inbox_review_session:{user_id}:{day}"

    def _load_session(self, user_id: int, day: str) -> dict[str, Any] | None:
        value = self.state_repo.get(self._session_key(user_id, day))
        if not value:
            return None
        try:
            data = json.loads(value)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        return data

    def _new_session(self, day: str, now_iso: str, base_due_dt: datetime) -> dict[str, Any]:
        due_iso = self._to_iso(base_due_dt)
        return {
            "date": day,
            "timezone": "Asia/Shanghai",
            "base_due_at": due_iso,
            "due_at": due_iso,
            "status": "pending",
            "sent_at": None,
            "started_at": None,
            "skipped_at": None,
            "snooze_count": 0,
            "last_action": "create_session",
            "last_offered_due_at": None,
            "offered_message_id": None,
            "created_at": now_iso,
        }

    def _load_or_create_session(self, user_id: int, day: str, now_iso: str, base_due_dt: datetime) -> dict[str, Any]:
        session = self._load_session(user_id=user_id, day=day)
        if session is not None:
            return session
        session = self._new_session(day=day, now_iso=now_iso, base_due_dt=base_due_dt)
        self._save_session(user_id=user_id, day=day, session=session, updated_at=now_iso)
        return session

    def _save_session(self, user_id: int, day: str, session: dict[str, Any], updated_at: str) -> None:
        self.state_repo.set(
            self._session_key(user_id, day),
            json.dumps(session, ensure_ascii=True),
            updated_at,
        )

    def _mark_review_sent_compat(self, user_id: int, day: str, now_iso: str) -> None:
        key = f"inbox_review_sent:{user_id}:{day}"
        if self.state_repo.get(key) is None:
            self.state_repo.set(key, now_iso, now_iso)

    def _session_base_due(self, day: str) -> datetime:
        return datetime.strptime(day, "%Y-%m-%d").replace(
            hour=self.REVIEW_WINDOW_HOUR,
            minute=self.REVIEW_WINDOW_MINUTE,
            second=0,
            microsecond=0,
            tzinfo=CST,
        ).astimezone(timezone.utc)

    def _oldest_age_hours(self, now_dt: datetime, created_at: str | None) -> int:
        if not created_at:
            return 0
        try:
            dt = self._parse_iso(created_at)
        except ValueError:
            return 0
        delta = now_dt - dt
        if delta.total_seconds() < 0:
            return 0
        return int(delta.total_seconds() // 3600)

    def _build_legacy_message(self, username: str, pending: int, oldest_hours: int, escalated: bool) -> str:
        if escalated:
            return f"【收件箱强提醒】{username}，未处理 {pending} 条，最老约 {oldest_hours} 小时，请尽快 triage。"
        return f"【收件箱提醒】{username}，还有 {pending} 条未处理，抽 2 分钟做一个 triage。"

    def _parse_iso(self, value: str) -> datetime:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    def _to_iso(self, value: datetime) -> str:
        return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()






