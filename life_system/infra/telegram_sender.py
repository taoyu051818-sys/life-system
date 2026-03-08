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
            {"command": "help", "description": "帮助"},
        ]
        self._post("setMyCommands", {"commands": json.dumps(commands, ensure_ascii=False)})

        menu_button_ok = True
        try:
            self._post("setChatMenuButton", {"menu_button": json.dumps({"type": "commands"})})
        except RuntimeError:
            menu_button_ok = False
        return {"commands": True, "menu_button": menu_button_ok}

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
