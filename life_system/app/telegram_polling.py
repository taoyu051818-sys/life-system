from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from life_system.app.services import LifeSystemService
from life_system.infra.db import now_utc_iso
from life_system.infra.repositories import AppStateRepository, UserRepository

_CHECKIN_LEVEL_PATTERN = re.compile(r"^(energy|focus|mood)=(\d+)$", re.IGNORECASE)


def parse_callback_data(data: str) -> tuple[str, int] | None:
    if not data or ":" not in data:
        return None
    action, rid = data.split(":", 1)
    if action not in {"ra", "rz", "rk"}:
        return None
    if not rid.isdigit():
        return None
    return action, int(rid)


def parse_journal_message(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if not stripped:
        return {"kind": "empty", "error": "未识别到可记录内容"}

    if stripped.startswith("/"):
        parts = stripped.split(None, 1)
        cmd = parts[0].split("@", 1)[0].lower()
        payload = parts[1].strip() if len(parts) > 1 else ""

        if cmd not in {"/r", "/w", "/c"}:
            return {"kind": "ignore"}
        if not payload:
            return {"kind": "empty", "error": "未识别到可记录内容"}

        if cmd == "/r":
            return {
                "kind": "entry",
                "entry_type": "reflection",
                "content": payload,
                "energy_level": None,
                "focus_level": None,
                "mood_level": None,
                "ok_text": "已记录为反思",
            }

        if cmd == "/w":
            return {
                "kind": "entry",
                "entry_type": "win",
                "content": payload,
                "energy_level": None,
                "focus_level": None,
                "mood_level": None,
                "ok_text": "已记录为小胜利",
            }

        # /c checkin with optional state values at the start
        tokens = payload.split()
        idx = 0
        levels: dict[str, int | None] = {"energy": None, "focus": None, "mood": None}
        while idx < len(tokens):
            m = _CHECKIN_LEVEL_PATTERN.match(tokens[idx])
            if not m:
                break
            key = m.group(1).lower()
            value = int(m.group(2))
            if value < 1 or value > 5:
                return {"kind": "error", "error": "energy/focus/mood 需要是 1 到 5"}
            levels[key] = value
            idx += 1

        content = " ".join(tokens[idx:]).strip()
        if not content:
            return {"kind": "empty", "error": "未识别到可记录内容"}

        return {
            "kind": "entry",
            "entry_type": "checkin",
            "content": content,
            "energy_level": levels["energy"],
            "focus_level": levels["focus"],
            "mood_level": levels["mood"],
            "ok_text": "已记录为状态记录",
        }

    return {
        "kind": "entry",
        "entry_type": "activity",
        "content": stripped,
        "energy_level": None,
        "focus_level": None,
        "mood_level": None,
        "ok_text": "已记录为活动",
    }


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
        processed_callbacks = 0
        processed_messages = 0
        ignored = 0
        ignored_reasons: dict[str, int] = {}
        max_update_id = None
        seen_update_ids: set[int] = set()

        for update in updates:
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                max_update_id = max(update_id, max_update_id or update_id)
                if update_id in seen_update_ids:
                    ignored += 1
                    ignored_reasons["duplicate_update_id"] = ignored_reasons.get("duplicate_update_id", 0) + 1
                    continue
                seen_update_ids.add(update_id)

            cq = update.get("callback_query")
            if isinstance(cq, dict):
                try:
                    self._process_callback_query(cq)
                    processed += 1
                    processed_callbacks += 1
                except Exception:
                    ignored += 1
                    ignored_reasons["callback_error"] = ignored_reasons.get("callback_error", 0) + 1
                continue

            msg = update.get("message")
            if isinstance(msg, dict):
                try:
                    handled, reason = self._process_message(msg)
                    if handled:
                        processed += 1
                        processed_messages += 1
                    else:
                        ignored += 1
                        ignored_reasons[reason] = ignored_reasons.get(reason, 0) + 1
                except Exception:
                    ignored += 1
                    ignored_reasons["message_error"] = ignored_reasons.get("message_error", 0) + 1
                continue

            ignored += 1
            ignored_reasons["unsupported_update"] = ignored_reasons.get("unsupported_update", 0) + 1

        if max_update_id is not None:
            self._set_offset(max_update_id + 1)

        return {
            "processed": processed,
            "processed_callbacks": processed_callbacks,
            "processed_messages": processed_messages,
            "ignored": ignored,
            "fetched": len(updates),
            "ignored_reasons": ignored_reasons,
        }

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

    def _process_message(self, msg: dict[str, Any]) -> tuple[bool, str]:
        chat = msg.get("chat")
        if not isinstance(chat, dict):
            return False, "missing_chat"

        if chat.get("type") != "private":
            return False, "non_private_chat"

        text = msg.get("text")
        if not isinstance(text, str):
            return False, "non_text_message"

        if "id" not in chat:
            return False, "missing_chat_id"
        chat_id = str(chat["id"])

        user = self.user_repo.get_by_telegram_chat_id(chat_id)
        if user is None:
            return False, "unknown_chat"

        parsed = parse_journal_message(text)
        kind = parsed.get("kind")
        if kind == "ignore":
            return False, "unsupported_command"

        if kind in {"empty", "error"}:
            self._safe_send_message(chat_id, str(parsed.get("error") or "未识别到可记录内容"))
            return False, "empty_payload" if kind == "empty" else "invalid_payload"

        service = LifeSystemService(
            self.conn,
            user_id=user["id"],
            username=user["username"],
            telegram_chat_id=user.get("telegram_chat_id"),
            reminder_sender=self.telegram_sender,
        )
        service.add_journal_entry(
            content=str(parsed["content"]),
            entry_type=str(parsed["entry_type"]),
            energy_level=parsed.get("energy_level"),
            focus_level=parsed.get("focus_level"),
            mood_level=parsed.get("mood_level"),
        )
        self._safe_send_message(chat_id, str(parsed.get("ok_text") or "已记录"))
        return True, "ok"

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

    def _safe_send_message(self, chat_id: str, text: str) -> None:
        try:
            self.telegram_sender.send_message(chat_id, text)
        except Exception:
            # Journal capture should not rollback when reply fails.
            pass
