from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from life_system.app.services import LifeSystemService, TelegramInboxReviewService
from life_system.infra.db import now_utc_iso
from life_system.infra.deepseek_client import DeepSeekClient
from life_system.infra.repositories import AppStateRepository, UserRepository
_CHECKIN_LEVEL_PATTERN = re.compile(r"^(energy|focus|mood)=(\d+)$", re.IGNORECASE)
_TODO_PREFIXES = ("待办", "待办：")
_HELP_TEXT = (
    "用法：\n"
    "普通文本：记录为 activity\n"
    "/r 今天学到了什么\n"
    "/w 今天完成了什么\n"
    "/ir 收件箱回顾入口\n"
    "/encouragement 获取今日鼓励\n"
    "/c energy=4 focus=3 mood=5 今天状态不错"
)
_FOCUS_BUTTON_TO_CMD = {
    "1 很难专注": "/c focus=1",
    "2 比较分散": "/c focus=2",
    "3 一般": "/c focus=3",
    "4 比较专注": "/c focus=4",
    "5 高度专注": "/c focus=5",
}
_ACTION_VERBS = (
    "发",
    "回复",
    "回",
    "买",
    "交",
    "提交",
    "改",
    "联系",
    "预约",
    "安排",
    "处理",
    "准备",
    "确认",
    "付款",
    "缴",
    "订",
    "做",
    "做完",
    "完成",
    "整理",
    "提醒",
    "发送",
)
_DONE_PHRASES = (
    "已经",
    "刚刚",
    "做完了",
    "完成了",
    "跑通了",
    "搞定了",
    "处理完了",
    "交了",
    "发了",
    "买了",
    "回了",
    "提交了",
    "联系了",
    "预约好了",
    "安排好了",
)
_HESITATION_PHRASES = (
    "要不要",
    "是不是",
    "能不能",
    "想不想",
    "也许",
    "可能",
    "或许",
    "考虑",
    "想研究",
    "想了解",
    "想看看",
    "先想想",
    "不确定",
)
_STATE_WORDS = (
    "累",
    "困",
    "烦",
    "焦虑",
    "开心",
    "难过",
    "状态",
    "注意力",
    "精力",
    "心情",
    "没状态",
    "不想动",
)
_EXPLICIT_MEMORY_TRIGGERS = ("提醒我", "帮我记一下", "记一下", "先记着", "先记下", "加入收件箱")
_MEMORY_REQUIRE_ACTION = ("记得", "别忘了")
_TIME_WORDS = (
    "今天",
    "明天",
    "今晚",
    "下午",
    "早上",
    "上午",
    "中午",
    "晚上",
    "周一",
    "周二",
    "周三",
    "周四",
    "周五",
    "周六",
    "周日",
    "这周",
    "下周",
    "本周",
    "月底前",
    "之前",
    "截止",
    "点前",
)


def _contains_any(text: str, words: tuple[str, ...]) -> bool:
    return any(w in text for w in words)


def _contains_action_verb(text: str) -> bool:
    return _contains_any(text, _ACTION_VERBS)


def decide_activity_inbox_rule(original_text: str) -> str | None:
    text = original_text.strip()
    lower_text = text.lower()
    if not text:
        return None

    has_action = _contains_action_verb(text)

    # Step 1: exclusion rules
    if _contains_any(text, _DONE_PHRASES):
        return None
    if _contains_any(text, _HESITATION_PHRASES):
        return None
    if (not has_action) and _contains_any(text, _STATE_WORDS):
        return None

    # Step 2.1: explicit remember/remind phrases
    if _contains_any(text, _EXPLICIT_MEMORY_TRIGGERS):
        return "explicit_remember"
    if _contains_any(text, _MEMORY_REQUIRE_ACTION) and has_action:
        return "explicit_remember"

    # Step 2.2: todo prefixes
    if any(text.startswith(prefix) for prefix in _TODO_PREFIXES):
        return "todo_prefix"
    if lower_text.startswith("todo") or lower_text.startswith("todo:"):
        return "todo_prefix"

    # Step 2.3: time word + action verb
    if _contains_any(text, _TIME_WORDS) and has_action:
        return "time_plus_action"

    # Step 2.4: short explicit action sentence
    starts_with_action = any(text.startswith(verb) for verb in sorted(_ACTION_VERBS, key=len, reverse=True))
    if len(text) <= 18 and has_action and (starts_with_action or text.startswith("给") or text.startswith("去")):
        return "short_action_phrase"

    # Step 3: default no inbox copy
    return None


def parse_callback_data(data: str) -> tuple[str, int] | None:
    if not data or ":" not in data:
        return None
    action, rid = data.split(":", 1)
    if action not in {"ra", "rz", "rk", "it", "ia", "ik"}:
        return None
    if not rid.isdigit():
        return None
    return action, int(rid)


def parse_review_session_callback(data: str) -> tuple[str, str] | None:
    if data in {"irms", "irmc"}:
        return data, ""
    if not data or ":" not in data:
        return None
    action, day = data.split(":", 1)
    if action not in {"irs", "irn", "irk"}:
        return None
    if not re.fullmatch(r"\d{8}", day):
        return None
    return action, day


def parse_journal_message(text: str) -> dict[str, Any]:
    stripped = text.strip()
    stripped = _FOCUS_BUTTON_TO_CMD.get(stripped, stripped)
    if not stripped:
        return {"kind": "empty", "error": "未识别到可记录内容"}

    if stripped.startswith("/"):
        parts = stripped.split(None, 1)
        cmd = parts[0].split("@", 1)[0].lower()
        payload = parts[1].strip() if len(parts) > 1 else ""

        if cmd == "/help":
            return {"kind": "help", "reply": _HELP_TEXT}
        if cmd == "/ir":
            return {"kind": "manual_inbox_review"}
        if cmd == "/encouragement":
            return {"kind": "encouragement"}
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
        has_any_level = any(v is not None for v in levels.values())
        if not content and has_any_level:
            content = "状态签到"
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
    INBOX_REVIEW_LIMIT = 5

    def __init__(self, conn: Any, telegram_sender: Any):
        self.conn = conn
        self.telegram_sender = telegram_sender
        self.user_repo = UserRepository(conn)
        self.state_repo = AppStateRepository(conn)

    def poll(self, limit: int = 20) -> dict[str, Any]:
        offset = self._get_offset()
        updates = self.telegram_sender.get_updates(offset=offset, limit=limit)
        processed = 0
        processed_callbacks = 0
        processed_messages = 0
        inbox_created = 0
        inbox_failed = 0
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
                    msg_result = self._process_message(msg)
                    handled = bool(msg_result.get("handled"))
                    reason = str(msg_result.get("reason", "unknown"))
                    if handled:
                        processed += 1
                        processed_messages += 1
                        inbox_created += int(msg_result.get("inbox_created", 0))
                        inbox_failed += int(msg_result.get("inbox_failed", 0))
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
            "inbox_created": inbox_created,
            "inbox_failed": inbox_failed,
            "ignored": ignored,
            "fetched": len(updates),
            "ignored_reasons": ignored_reasons,
        }

    def _process_callback_query(self, cq: dict[str, Any]) -> None:
        callback_id = str(cq.get("id", ""))
        data = str(cq.get("data", ""))
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
        review_service = TelegramInboxReviewService(self.conn, telegram_sender=self.telegram_sender)

        parsed = parse_callback_data(data)
        if parsed:
            action, target_id = parsed
            if action == "ra":
                status = service.ack_reminder(target_id, acked_via="telegram")
                text = "已确认" if status == "acknowledged" else "已经处理过了"
            elif action == "rz":
                dt = datetime.now(timezone.utc) + timedelta(minutes=10)
                remind_at = dt.replace(microsecond=0).isoformat()
                status = service.snooze_reminder(target_id, remind_at)
                text = "已延后10分钟" if status == "snoozed" else "已经处理过了"
            elif action == "rk":
                status = service.skip_reminder(target_id, reason="telegram_skip")
                text = "已跳过今天" if status == "skipped" else "已经处理过了"
            else:
                text = self._process_inbox_callback(action=action, inbox_id=target_id, service=service)

            if action in {"ra", "rz", "rk"} and status == "not_found":
                text = "提醒不存在或无权限"
            if callback_id:
                self._safe_answer(callback_id, text)
            self._safe_clear_inline_keyboard(cq)
            return

        review_parsed = parse_review_session_callback(data)
        if review_parsed:
            action, day = review_parsed
            text = self._process_review_callback(
                action=action,
                day=day,
                user=user,
                review_service=review_service,
            )
            if callback_id:
                self._safe_answer(callback_id, text)
            self._safe_clear_inline_keyboard(cq)
            return

        if callback_id:
            self._safe_answer(callback_id, "无法识别操作")

    def _process_message(self, msg: dict[str, Any]) -> dict[str, Any]:
        chat = msg.get("chat")
        if not isinstance(chat, dict):
            return {"handled": False, "reason": "missing_chat"}

        if chat.get("type") != "private":
            return {"handled": False, "reason": "non_private_chat"}

        text = msg.get("text")
        if not isinstance(text, str):
            return {"handled": False, "reason": "non_text_message"}

        if "id" not in chat:
            return {"handled": False, "reason": "missing_chat_id"}
        chat_id = str(chat["id"])

        user = self.user_repo.get_by_telegram_chat_id(chat_id)
        if user is None:
            return {"handled": False, "reason": "unknown_chat"}

        parsed = parse_journal_message(text)
        kind = parsed.get("kind")
        if kind == "help":
            self._safe_send_message(chat_id, str(parsed.get("reply") or _HELP_TEXT), with_keyboard=True)
            return {"handled": True, "reason": "help"}
        if kind == "manual_inbox_review":
            return self._handle_manual_inbox_review(chat_id=chat_id, user=user)
        if kind == "encouragement":
            return self._handle_encouragement(chat_id=chat_id, user=user)
        if kind == "ignore":
            return {"handled": False, "reason": "unsupported_command"}

        if kind in {"empty", "error"}:
            self._safe_send_message(chat_id, str(parsed.get("error") or "未识别到可记录内容"), with_keyboard=True)
            return {"handled": False, "reason": "empty_payload" if kind == "empty" else "invalid_payload"}

        service = LifeSystemService(
            self.conn,
            user_id=user["id"],
            username=user["username"],
            telegram_chat_id=user.get("telegram_chat_id"),
            reminder_sender=self.telegram_sender,
        )
        journal_entry_id = service.add_journal_entry(
            content=str(parsed["content"]),
            entry_type=str(parsed["entry_type"]),
            energy_level=parsed.get("energy_level"),
            focus_level=parsed.get("focus_level"),
            mood_level=parsed.get("mood_level"),
        )
        inbox_created = 0
        inbox_failed = 0
        ok_text = str(parsed.get("ok_text") or "已记录")
        if str(parsed.get("entry_type")) == "activity":
            rule_name = decide_activity_inbox_rule(str(parsed["content"]))
        else:
            rule_name = None
        if rule_name:
            try:
                service.capture_inbox(
                    content=str(parsed["content"]),
                    source="telegram_auto",
                    source_journal_entry_id=journal_entry_id,
                    created_by="telegram_auto",
                    rule_name=rule_name,
                    rule_version="inbox_v1",
                )
                inbox_created = 1
                ok_text = "已记录，并已加入收件箱"
            except Exception:
                inbox_failed = 1
        self._safe_send_message(chat_id, ok_text, with_keyboard=True)
        return {"handled": True, "reason": "ok", "inbox_created": inbox_created, "inbox_failed": inbox_failed}

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

    def _safe_send_message(self, chat_id: str, text: str, with_keyboard: bool = False) -> None:
        try:
            if with_keyboard and hasattr(self.telegram_sender, "send_message_with_focus_keyboard"):
                self.telegram_sender.send_message_with_focus_keyboard(chat_id, text)
            else:
                self.telegram_sender.send_message(chat_id, text)
        except Exception:
            # Journal capture should not rollback when reply fails.
            pass

    def _safe_clear_inline_keyboard(self, cq: dict[str, Any]) -> None:
        try:
            msg = cq.get("message")
            if not isinstance(msg, dict):
                return
            chat = msg.get("chat")
            if not isinstance(chat, dict):
                return
            chat_id = chat.get("id")
            message_id = msg.get("message_id")
            if chat_id is None or message_id is None:
                return
            if hasattr(self.telegram_sender, "clear_message_inline_keyboard"):
                self.telegram_sender.clear_message_inline_keyboard(str(chat_id), int(message_id))
        except Exception:
            # Do not block action when message edit fails.
            pass

    def _process_inbox_callback(self, action: str, inbox_id: int, service: LifeSystemService) -> str:
        triage_status = service.inbox_triage_status(inbox_id)
        if action == "ik":
            return "先留在收件箱"
        if triage_status in {"already_triaged", "already_archived"}:
            return "已处理过了"
        if triage_status == "not_found":
            return "收件箱不存在或无权限"
        if action == "it":
            task_id = service.triage_inbox_to_task(inbox_id, created_by="telegram_auto_followup")
            return "已转为任务" if task_id is not None else "已处理过了"
        if action == "ia":
            status = service.archive_inbox(inbox_id, created_by="telegram_auto_followup")
            return "已归档" if status == "archived" else "已处理过了"
        return "无法识别操作"

    def _handle_encouragement(self, chat_id: str, user: dict[str, Any]) -> dict[str, Any]:
        service = LifeSystemService(
            self.conn,
            user_id=user["id"],
            username=user["username"],
            telegram_chat_id=user.get("telegram_chat_id"),
            reminder_sender=self.telegram_sender,
        )
        api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("APIKEY")
        deepseek = None
        if api_key:
            deepseek = DeepSeekClient(
                api_key=api_key,
                base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
                model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
            )
        result = service.build_today_encouragement(deepseek_client=deepseek)
        text = str(result["text"])
        self._safe_send_message(chat_id, text, with_keyboard=True)
        return {"handled": True, "reason": "encouragement"}
    def _handle_manual_inbox_review(self, chat_id: str, user: dict[str, Any]) -> dict[str, Any]:
        service = LifeSystemService(
            self.conn,
            user_id=user["id"],
            username=user["username"],
            telegram_chat_id=user.get("telegram_chat_id"),
            reminder_sender=self.telegram_sender,
        )
        count = len(service.list_new_inbox_oldest(limit=10000))
        if count <= 0:
            self._safe_send_message(chat_id, "你当前没有需要回顾的 inbox。", with_keyboard=True)
            return {"handled": True, "reason": "manual_ir_empty"}
        if hasattr(self.telegram_sender, "send_manual_inbox_review_prompt"):
            try:
                self.telegram_sender.send_manual_inbox_review_prompt(chat_id, count)
            except Exception:
                self._safe_send_message(chat_id, f"你当前还有 {count} 条 inbox 未处理。要现在开始逐条回顾吗？", with_keyboard=True)
        else:
            self._safe_send_message(chat_id, f"你当前还有 {count} 条 inbox 未处理。要现在开始逐条回顾吗？", with_keyboard=True)
        return {"handled": True, "reason": "manual_ir_prompt"}

    def _process_review_callback(
        self,
        action: str,
        day: str,
        user: dict[str, Any],
        review_service: TelegramInboxReviewService,
    ) -> str:
        user_id = int(user["id"])
        if action == "irms":
            sent = review_service.send_inbox_review_items_for_user(user_id=user_id, limit=self.INBOX_REVIEW_LIMIT)
            return "已开始回顾" if sent > 0 else "当前没有可回顾 inbox"
        if action == "irmc":
            return "已取消"
        day_fmt = f"{day[0:4]}-{day[4:6]}-{day[6:8]}"
        if action == "irs":
            result = review_service.handle_session_action(
                user_id=user_id,
                day=day_fmt,
                action="start",
                now=now_utc_iso(),
                review_limit=self.INBOX_REVIEW_LIMIT,
            )
            return str(result.get("message", "已开始回顾"))
        if action == "irn":
            result = review_service.handle_session_action(
                user_id=user_id,
                day=day_fmt,
                action="snooze",
                now=now_utc_iso(),
                review_limit=self.INBOX_REVIEW_LIMIT,
            )
            return str(result.get("message", "已延后半小时"))
        if action == "irk":
            result = review_service.handle_session_action(
                user_id=user_id,
                day=day_fmt,
                action="skip",
                now=now_utc_iso(),
                review_limit=self.INBOX_REVIEW_LIMIT,
            )
            return str(result.get("message", "今天已跳过"))
        return "无法识别操作"









