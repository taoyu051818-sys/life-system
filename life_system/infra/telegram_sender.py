import json
import urllib.parse
import urllib.request


class TelegramReminderSender:
    def __init__(self, bot_token: str):
        self._url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    def send_message(self, chat_id: str, text: str) -> str:
        data = urllib.parse.urlencode(
            {
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": "true",
            }
        ).encode("utf-8")
        req = urllib.request.Request(self._url, data=data, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            raise RuntimeError("telegram_request_failed") from exc
        if not payload.get("ok"):
            raise RuntimeError("telegram_api_error")
        result = payload.get("result", {})
        return str(result.get("message_id", ""))

