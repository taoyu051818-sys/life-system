from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from life_system.app.services import LifeSystemService
from life_system.infra.db import now_utc_iso
from life_system.infra.repositories import AppStateRepository, UserRepository


def parse_callback_data(data: str) -> tuple[str, int] | None:
    if not data or ":" not in data:
        return None
    action, rid = data.split(":", 1)
    if action not in {"ra", "rz", "rk"}:
        return None
    if not rid.isdigit():
        return None
    return action, int(rid)


class TelegramPollingService:
    OFFSET_KEY = "telegram.update_offset"

    def __init__(self, conn: Any, telegram_sender: Any):
        self.conn = conn
        self.telegram_sender = telegram_sender
        self.user_repo = UserRepository(conn)
        self.state_repo = AppStateRepository(conn)

    def poll(self, limit: int = 20) -> dict[str, int]:
        offset = self._get_offset()
        updates = self.telegram_sender.get_updates(offset=offset, limit=limit)
        processed = 0
        ignored = 0
        max_update_id = None

        for update in updates:
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                max_update_id = max(update_id, max_update_id or update_id)

            cq = update.get("callback_query")
            if not isinstance(cq, dict):
                ignored += 1
                continue
            try:
                self._process_callback_query(cq)
                processed += 1
            except Exception:
                ignored += 1
                continue

        if max_update_id is not None:
            self._set_offset(max_update_id + 1)

        return {"processed": processed, "ignored": ignored, "fetched": len(updates)}

    def _process_callback_query(self, cq: dict[str, Any]) -> None:
        callback_id = str(cq.get("id", ""))
        data = str(cq.get("data", ""))
        parsed = parse_callback_data(data)
        if not parsed:
            if callback_id:
                self._safe_answer(callback_id, "无法识别操作")
            return

        action, reminder_id = parsed
        chat_id = self._extract_chat_id(cq)
        if chat_id is None:
            if callback_id:
                self._safe_answer(callback_id, "无法识别用户")
            return

        user = self.user_repo.get_by_telegram_chat_id(chat_id)
        if user is None:
            if callback_id:
                self._safe_answer(callback_id, "未知用户")
            return

        service = LifeSystemService(
            self.conn,
            user_id=user["id"],
            username=user["username"],
            telegram_chat_id=user.get("telegram_chat_id"),
            reminder_sender=self.telegram_sender,
        )

        if action == "ra":
            status = service.ack_reminder(reminder_id, acked_via="telegram")
            text = "已确认" if status == "acknowledged" else "已经处理过了"
        elif action == "rz":
            dt = datetime.now(timezone.utc) + timedelta(minutes=10)
            remind_at = dt.replace(microsecond=0).isoformat()
            status = service.snooze_reminder(reminder_id, remind_at)
            text = "已延后10分钟" if status == "snoozed" else "已经处理过了"
        else:
            status = service.skip_reminder(reminder_id, reason="telegram_skip")
            text = "已跳过今天" if status == "skipped" else "已经处理过了"

        if status == "not_found":
            text = "提醒不存在或无权限"
        if callback_id:
            self._safe_answer(callback_id, text)

    def _extract_chat_id(self, cq: dict[str, Any]) -> str | None:
        msg = cq.get("message")
        if isinstance(msg, dict):
            chat = msg.get("chat")
            if isinstance(chat, dict) and "id" in chat:
                return str(chat["id"])
        frm = cq.get("from")
        if isinstance(frm, dict) and "id" in frm:
            return str(frm["id"])
        return None

    def _get_offset(self) -> int | None:
        value = self.state_repo.get(self.OFFSET_KEY)
        if value is None:
            return None
        if value.isdigit():
            return int(value)
        return None

    def _set_offset(self, value: int) -> None:
        self.state_repo.set(self.OFFSET_KEY, str(value), now_utc_iso())

    def _safe_answer(self, callback_id: str, text: str) -> None:
        try:
            self.telegram_sender.answer_callback_query(callback_id, text)
        except Exception:
            # Do not block business action or polling offset when answerCallbackQuery fails.
            pass
