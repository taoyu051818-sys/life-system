import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class TelegramReminderSender:
    def __init__(self, bot_token: str):
        self._base = f"https://api.telegram.org/bot{bot_token}"

    def send_message(self, chat_id: str, text: str) -> str:
        payload = self._post("sendMessage", {"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"})
        result = payload.get("result", {})
        return str(result.get("message_id", ""))

    def send_message_with_focus_keyboard(self, chat_id: str, text: str) -> str:
        keyboard = {
            "keyboard": [
                [{"text": "1 很难专注"}],
                [{"text": "2 比较分散"}],
                [{"text": "3 一般"}],
                [{"text": "4 比较专注"}],
                [{"text": "5 高度专注"}],
            ],
            "resize_keyboard": True,
            "one_time_keyboard": False,
            "selective": False,
        }
        payload = self._post(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": "true",
                "reply_markup": json.dumps(keyboard, ensure_ascii=False),
            },
        )
        result = payload.get("result", {})
        return str(result.get("message_id", ""))

    def send_reminder(self, chat_id: str, text: str, reminder_id: int) -> str:
        keyboard = {
            "inline_keyboard": [
                [
                    {"text": "完成", "callback_data": f"ra:{reminder_id}"},
                    {"text": "延后10分钟", "callback_data": f"rz:{reminder_id}"},
                    {"text": "跳过今天", "callback_data": f"rk:{reminder_id}"},
                ]
            ]
        }
        payload = self._post(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": "true",
                "reply_markup": json.dumps(keyboard, ensure_ascii=True),
            },
        )
        result = payload.get("result", {})
        return str(result.get("message_id", ""))

    def send_inbox_review_item(self, chat_id: str, inbox_id: int, content: str) -> str:
        keyboard = {
            "inline_keyboard": [
                [
                    {"text": "转任务", "callback_data": f"it:{inbox_id}"},
                    {"text": "归档", "callback_data": f"ia:{inbox_id}"},
                    {"text": "先留着", "callback_data": f"ik:{inbox_id}"},
                ]
            ]
        }
        text = f"【收件箱】\n#{inbox_id}\n{content}"
        payload = self._post(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": "true",
                "reply_markup": json.dumps(keyboard, ensure_ascii=False),
            },
        )
        result = payload.get("result", {})
        return str(result.get("message_id", ""))

    def send_auto_inbox_review_entry(
        self,
        chat_id: str,
        day_yyyymmdd: str,
        count: int,
        strong: bool,
        allow_snooze: bool,
    ) -> str:
        if strong:
            text = f"你今天还有 {count} 条 inbox 未处理，而且已经积压一段时间了。现在开始逐条回顾吗？"
        else:
            text = f"你今天还有 {count} 条 inbox 未处理。现在开始逐条回顾吗？"
        buttons = [{"text": "开始回顾", "callback_data": f"irs:{day_yyyymmdd}"}]
        if allow_snooze:
            buttons.append({"text": "延后半小时", "callback_data": f"irn:{day_yyyymmdd}"})
        buttons.append({"text": "今天跳过", "callback_data": f"irk:{day_yyyymmdd}"})
        keyboard = {"inline_keyboard": [buttons]}
        payload = self._post(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": "true",
                "reply_markup": json.dumps(keyboard, ensure_ascii=False),
            },
        )
        result = payload.get("result", {})
        return str(result.get("message_id", ""))

    def send_manual_inbox_review_prompt(self, chat_id: str, count: int) -> str:
        text = f"你当前还有 {count} 条 inbox 未处理。要现在开始逐条回顾吗？"
        keyboard = {
            "inline_keyboard": [
                [
                    {"text": "开始回顾", "callback_data": "irms"},
                    {"text": "取消", "callback_data": "irmc"},
                ]
            ]
        }
        payload = self._post(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": "true",
                "reply_markup": json.dumps(keyboard, ensure_ascii=False),
            },
        )
        result = payload.get("result", {})
        return str(result.get("message_id", ""))

    def clear_message_inline_keyboard(self, chat_id: str, message_id: int) -> None:
        self._post(
            "editMessageReplyMarkup",
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "reply_markup": json.dumps({}, ensure_ascii=True),
            },
        )

    def get_updates(self, offset: int | None, limit: int) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"timeout": 0, "limit": limit}
        if offset is not None:
            params["offset"] = offset
        payload = self._post("getUpdates", params)
        result = payload.get("result", [])
        if not isinstance(result, list):
            return []
        return result

    def answer_callback_query(self, callback_query_id: str, text: str) -> None:
        self._post("answerCallbackQuery", {"callback_query_id": callback_query_id, "text": text})

    def setup_menu(self) -> dict[str, bool]:
        commands = [
            {"command": "r", "description": "反思"},
            {"command": "w", "description": "小胜利"},
            {"command": "c", "description": "状态签到"},
            {"command": "ir", "description": "收件箱回顾"},
            {"command": "encouragement", "description": "今日鼓励"},
            {"command": "help", "description": "帮助"},
        ]
        self._post("setMyCommands", {"commands": json.dumps(commands, ensure_ascii=False)})

        menu_button_ok = True
        try:
            self._post("setChatMenuButton", {"menu_button": json.dumps({"type": "commands"})})
        except RuntimeError:
            menu_button_ok = False
        return {"commands": True, "menu_button": menu_button_ok}

    def setup_focus_keyboard(self, chat_id: str) -> None:
        self.send_message_with_focus_keyboard(chat_id, "已设置专注状态键盘，可直接点按钮记录状态。")

    def _post(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._base}/{method}"
        data = urllib.parse.urlencode(params).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=12) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            desc = body
            try:
                err = json.loads(body)
                if isinstance(err, dict) and "description" in err:
                    desc = str(err["description"])
            except Exception:
                pass
            raise RuntimeError(f"telegram_http_error {exc.code}: {desc}") from exc
        except Exception as exc:
            raise RuntimeError("telegram_request_failed") from exc
        if not payload.get("ok"):
            desc = payload.get("description", "telegram_api_error")
            raise RuntimeError(f"telegram_api_error: {desc}")
        return payload


