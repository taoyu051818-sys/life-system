import json
import tempfile
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from life_system.app.telegram_polling import parse_journal_message
from life_system.cli.commands import run_cli


def run_with_output(args: list[str]) -> tuple[int, str]:
    buf = StringIO()
    with redirect_stdout(buf):
        rc = run_cli(args)
    return rc, buf.getvalue()


def test_parse_encouragement_command() -> None:
    parsed = parse_journal_message("/encouragement")
    assert parsed["kind"] == "encouragement"


def test_telegram_poll_handles_encouragement_message() -> None:
    class FakeSender:
        def __init__(self, updates: list[dict]):
            self.updates = updates
            self.sent: list[tuple[str, str]] = []

        def get_updates(self, offset: int | None, limit: int) -> list[dict]:
            del offset
            del limit
            out = self.updates
            self.updates = []
            return out

        def send_message(self, chat_id: str, text: str) -> str:
            self.sent.append((chat_id, text))
            return "m1"

        def send_message_with_focus_keyboard(self, chat_id: str, text: str) -> str:
            self.sent.append((chat_id, text))
            return "m2"

        def answer_callback_query(self, callback_query_id: str, text: str) -> None:
            del callback_query_id
            del text

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "life.db"
        run_with_output(["--db", str(db_path), "user", "set-telegram", "xiaoyu", "1001"])
        fake = FakeSender(
            [{"update_id": 1, "message": {"chat": {"id": 1001, "type": "private"}, "text": "/encouragement"}}]
        )
        with patch("life_system.cli.commands._build_telegram_sender_from_env", return_value=fake):
            rc, out = run_with_output(["--db", str(db_path), "telegram", "poll"])
        assert rc == 0
        assert "messages=1" in out
        assert len(fake.sent) == 1


def test_cli_encouragement_today_and_send_daily() -> None:
    class FakeSender:
        def __init__(self):
            self.sent: list[tuple[str, str]] = []

        def send_message(self, chat_id: str, text: str) -> str:
            self.sent.append((chat_id, text))
            return "m1"

    class FakeDeepSeek:
        def generate_encouragement(self, prompt: str, system_prompt: str) -> str:
            del prompt
            del system_prompt
            return "今天你有真实记录，继续小步推进。"

    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "life.db")
        run_with_output(["--db", db_path, "user", "set-telegram", "xiaoyu", "1001"])
        run_with_output(["--db", db_path, "journal", "add", "今天有进展", "--type", "win"])
        fake_sender = FakeSender()
        with patch("life_system.cli.commands._build_telegram_sender_from_env", return_value=fake_sender):
            with patch("life_system.cli.commands._build_deepseek_client_from_env", return_value=FakeDeepSeek()):
                rc1, out1 = run_with_output(["--db", db_path, "--user", "xiaoyu", "encouragement", "today"])
                rc2, out2 = run_with_output(["--db", db_path, "encouragement", "send-daily"])
        assert rc1 == 0
        assert "小步推进" in out1
        assert rc2 == 0
        assert "sent=1" in out2
        assert len(fake_sender.sent) == 1


def test_setup_menu_contains_encouragement_command() -> None:
    from life_system.infra.telegram_sender import TelegramReminderSender

    sender = TelegramReminderSender("dummy")
    calls: list[tuple[str, dict]] = []

    def fake_post(method: str, params: dict) -> dict:
        calls.append((method, params))
        return {"ok": True, "result": {"message_id": 1}}

    with patch.object(sender, "_post", side_effect=fake_post):
        sender.setup_menu()

    methods = [m for m, _ in calls]
    assert "setMyCommands" in methods
    cmd_payload = next(params for method, params in calls if method == "setMyCommands")
    commands = json.loads(cmd_payload["commands"])
    command_names = [c["command"] for c in commands]
    assert "encouragement" in command_names


def test_encouragement_prompt_uses_all_today_journals() -> None:
    class CaptureDeepSeek:
        def __init__(self):
            self.prompt = ""

        def generate_encouragement(self, prompt: str, system_prompt: str) -> str:
            del system_prompt
            self.prompt = prompt
            return "ok"

    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "life.db")
        for i in range(1, 12):
            run_with_output(["--db", db_path, "journal", "add", f"entry-{i}", "--type", "activity"])

        fake = CaptureDeepSeek()
        with patch("life_system.cli.commands._build_deepseek_client_from_env", return_value=fake):
            rc, _ = run_with_output(["--db", db_path, "--user", "xiaoyu", "encouragement", "today"])

        assert rc == 0
        assert "entry-11" in fake.prompt
        assert "entry-1" in fake.prompt
