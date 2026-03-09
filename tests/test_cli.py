import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from life_system.cli.commands import run_cli
from life_system.app.telegram_polling import parse_callback_data, parse_journal_message
from life_system.infra.db import connection_ctx
from life_system.infra.telegram_sender import TelegramReminderSender


def run_with_output(args: list[str]) -> tuple[int, str]:
    buf = StringIO()
    with redirect_stdout(buf):
        rc = run_cli(args)
    return rc, buf.getvalue()


class TestCliFlows(unittest.TestCase):
    def test_telegram_sender_focus_keyboard_markup(self) -> None:
        sender = TelegramReminderSender("dummy")
        calls: list[tuple[str, dict]] = []

        def fake_post(method: str, params: dict) -> dict:
            calls.append((method, params))
            return {"ok": True, "result": {"message_id": 1}}

        with patch.object(sender, "_post", side_effect=fake_post):
            sender.send_message_with_focus_keyboard("1001", "状态")
        self.assertEqual(calls[0][0], "sendMessage")
        self.assertIn("reply_markup", calls[0][1])
        markup = json.loads(calls[0][1]["reply_markup"])
        buttons = [row[0]["text"] for row in markup["keyboard"]]
        self.assertEqual(
            buttons,
            ["1 很难专注", "2 比较分散", "3 一般", "4 比较专注", "5 高度专注"],
        )

    def test_telegram_sender_inbox_review_inline_buttons(self) -> None:
        sender = TelegramReminderSender("dummy")
        calls: list[tuple[str, dict]] = []

        def fake_post(method: str, params: dict) -> dict:
            calls.append((method, params))
            return {"ok": True, "result": {"message_id": 1}}

        with patch.object(sender, "_post", side_effect=fake_post):
            sender.send_inbox_review_item("1001", 7, "abc")
        self.assertEqual(calls[0][0], "sendMessage")
        self.assertIn("#7", calls[0][1]["text"])
        markup = json.loads(calls[0][1]["reply_markup"])
        btns = [b["callback_data"] for b in markup["inline_keyboard"][0]]
        self.assertEqual(btns, ["it:7", "ia:7", "ik:7"])

    def test_telegram_callback_parsing(self) -> None:
        self.assertEqual(parse_callback_data("ra:12"), ("ra", 12))
        self.assertEqual(parse_callback_data("rz:99"), ("rz", 99))
        self.assertEqual(parse_callback_data("rk:5"), ("rk", 5))
        self.assertEqual(parse_callback_data("it:7"), ("it", 7))
        self.assertEqual(parse_callback_data("ia:8"), ("ia", 8))
        self.assertEqual(parse_callback_data("ik:9"), ("ik", 9))
        self.assertIsNone(parse_callback_data("bad"))
        self.assertIsNone(parse_callback_data("ra:x"))

    def test_telegram_journal_parsing(self) -> None:
        self.assertEqual(parse_journal_message("今天完成了背单词")["entry_type"], "activity")
        self.assertEqual(parse_journal_message("/r 今天启动很难")["entry_type"], "reflection")
        self.assertEqual(parse_journal_message("/w 今天有进展")["entry_type"], "win")
        self.assertEqual(parse_journal_message("/help")["kind"], "help")
        focus_only = parse_journal_message("/c focus=4")
        self.assertEqual(focus_only["entry_type"], "checkin")
        self.assertEqual(focus_only["focus_level"], 4)
        self.assertEqual(focus_only["content"], "状态签到")
        btn = parse_journal_message("4 比较专注")
        self.assertEqual(btn["entry_type"], "checkin")
        self.assertEqual(btn["focus_level"], 4)
        self.assertEqual(btn["content"], "状态签到")
        parsed = parse_journal_message("/c energy=2 focus=3 mood=4 今天状态一般")
        self.assertEqual(parsed["entry_type"], "checkin")
        self.assertEqual(parsed["energy_level"], 2)
        self.assertEqual(parsed["focus_level"], 3)
        self.assertEqual(parsed["mood_level"], 4)
        self.assertEqual(parse_journal_message("/x unknown")["kind"], "ignore")
        self.assertEqual(parse_journal_message("/c energy=9 bad")["kind"], "error")

    def test_init_db_creates_default_users(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "life.db")
            rc, _ = run_with_output(["--db", db_path, "init-db"])
            self.assertEqual(rc, 0)
            with connection_ctx(Path(db_path)) as conn:
                names = [row["username"] for row in conn.execute("SELECT username FROM users ORDER BY username")]
                self.assertEqual(names, ["partner", "xiaoyu"])

    def test_default_user_is_xiaoyu(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "life.db")
            rc, _ = run_with_output(["--db", db_path, "capture", "default user note"])
            self.assertEqual(rc, 0)
            with connection_ctx(Path(db_path)) as conn:
                row = conn.execute(
                    """
                    SELECT i.content, u.username
                    FROM inbox_items i
                    JOIN users u ON u.id = i.user_id
                    ORDER BY i.id DESC
                    LIMIT 1
                    """
                ).fetchone()
                self.assertEqual(row["content"], "default user note")
                self.assertEqual(row["username"], "xiaoyu")

    def test_invalid_user_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "life.db")
            rc, out = run_with_output(["--db", db_path, "--user", "nobody", "capture", "x"])
            self.assertEqual(rc, 1)
            self.assertIn("user not found: nobody", out)

    def test_user_list_and_add(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "life.db")
            rc1, out1 = run_with_output(["--db", db_path, "user", "list"])
            self.assertEqual(rc1, 0)
            self.assertIn("xiaoyu", out1)
            self.assertIn("partner", out1)

            rc2, out2 = run_with_output(["--db", db_path, "user", "add", "alice", "--display-name", "Alice"])
            self.assertEqual(rc2, 0)
            self.assertIn("user added", out2)

            rc3, out3 = run_with_output(["--db", db_path, "user", "add", "alice"])
            self.assertEqual(rc3, 1)
            self.assertIn("username already exists: alice", out3)

            rc4, out4 = run_with_output(["--db", db_path, "user", "list"])
            self.assertEqual(rc4, 0)
            self.assertIn("alice", out4)

    def test_user_set_and_clear_telegram(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "life.db")
            rc1, out1 = run_with_output(["--db", db_path, "user", "set-telegram", "xiaoyu", "123456"])
            self.assertEqual(rc1, 0)
            self.assertIn("telegram chat id set for xiaoyu", out1)
            rc2, out2 = run_with_output(["--db", db_path, "user", "list"])
            self.assertEqual(rc2, 0)
            self.assertIn("Telegram:已配置", out2)
            rc3, out3 = run_with_output(["--db", db_path, "user", "clear-telegram", "xiaoyu"])
            self.assertEqual(rc3, 0)
            self.assertIn("telegram chat id cleared for xiaoyu", out3)
            rc4, out4 = run_with_output(["--db", db_path, "user", "list"])
            self.assertEqual(rc4, 0)
            self.assertIn("Telegram:未配置", out4)

    def test_user_isolation_for_inbox_task_anki_lists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "life.db")
            run_with_output(["--db", db_path, "--user", "xiaoyu", "capture", "x-inbox"])
            run_with_output(["--db", db_path, "--user", "partner", "capture", "p-inbox"])
            run_with_output(["--db", db_path, "--user", "xiaoyu", "task", "create", "x-task"])
            run_with_output(["--db", db_path, "--user", "partner", "task", "create", "p-task"])
            run_with_output(
                ["--db", db_path, "--user", "xiaoyu", "anki", "create", "manual", "x-front", "x-back", "--deck-name", "xdeck"]
            )
            run_with_output(
                ["--db", db_path, "--user", "partner", "anki", "create", "manual", "p-front", "p-back", "--deck-name", "pdeck"]
            )

            _, out_x_inbox = run_with_output(["--db", db_path, "--user", "xiaoyu", "inbox", "list"])
            _, out_p_inbox = run_with_output(["--db", db_path, "--user", "partner", "inbox", "list"])
            self.assertIn("x-inbox", out_x_inbox)
            self.assertNotIn("p-inbox", out_x_inbox)
            self.assertIn("p-inbox", out_p_inbox)
            self.assertNotIn("x-inbox", out_p_inbox)

            _, out_x_task = run_with_output(["--db", db_path, "--user", "xiaoyu", "task", "list"])
            _, out_p_task = run_with_output(["--db", db_path, "--user", "partner", "task", "list"])
            self.assertIn("x-task", out_x_task)
            self.assertNotIn("p-task", out_x_task)
            self.assertIn("p-task", out_p_task)
            self.assertNotIn("x-task", out_p_task)

            _, out_x_anki = run_with_output(["--db", db_path, "--user", "xiaoyu", "anki", "list"])
            _, out_p_anki = run_with_output(["--db", db_path, "--user", "partner", "anki", "list"])
            self.assertIn("xdeck", out_x_anki)
            self.assertNotIn("pdeck", out_x_anki)
            self.assertIn("pdeck", out_p_anki)
            self.assertNotIn("xdeck", out_p_anki)

    def test_export_csv_is_user_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "life.db")
            out_x = Path(tmp) / "xiaoyu.csv"
            out_p = Path(tmp) / "partner.csv"

            run_with_output(["--db", db_path, "--user", "xiaoyu", "anki", "create", "manual", "x-q", "x-a"])
            run_with_output(["--db", db_path, "--user", "partner", "anki", "create", "manual", "p-q", "p-a"])

            rc_x, _ = run_with_output(["--db", db_path, "--user", "xiaoyu", "anki", "export-csv", str(out_x)])
            rc_p, _ = run_with_output(["--db", db_path, "--user", "partner", "anki", "export-csv", str(out_p)])
            self.assertEqual(rc_x, 0)
            self.assertEqual(rc_p, 0)

            text_x = out_x.read_text(encoding="utf-8")
            text_p = out_p.read_text(encoding="utf-8")
            self.assertIn("x-q", text_x)
            self.assertNotIn("p-q", text_x)
            self.assertIn("p-q", text_p)
            self.assertNotIn("x-q", text_p)

    def test_inbox_list_excludes_archived_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "life.db")
            run_with_output(["--db", db_path, "capture", "active note"])
            run_with_output(["--db", db_path, "capture", "to archive"])
            run_with_output(["--db", db_path, "inbox", "triage", "2", "archive"])

            _, out_default = run_with_output(["--db", db_path, "inbox", "list"])
            _, out_archived = run_with_output(["--db", db_path, "inbox", "list", "--status", "archived"])
            _, out_all = run_with_output(["--db", db_path, "inbox", "list", "--all"])
            self.assertIn("active note", out_default)
            self.assertNotIn("to archive", out_default)
            self.assertIn("to archive", out_archived)
            self.assertIn("to archive", out_all)

    def test_datetime_validation_for_reminder_create_and_task_snooze(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "life.db")
            run_with_output(["--db", db_path, "task", "create", "valid task"])

            rc1, out1 = run_with_output(["--db", db_path, "reminder", "create", "1", "bad-date"])
            self.assertEqual(rc1, 1)
            self.assertIn("invalid remind_at", out1)

            rc2, out2 = run_with_output(["--db", db_path, "task", "snooze", "1", "2026/03/08 09:00"])
            self.assertEqual(rc2, 1)
            self.assertIn("invalid snooze_until", out2)

            rc3, _ = run_with_output(["--db", db_path, "reminder", "create", "1", "2026-03-08T09:00:00+08:00"])
            self.assertEqual(rc3, 0)
            rc4, _ = run_with_output(["--db", db_path, "task", "snooze", "1", "2026-03-07T00:00:00+00:00"])
            self.assertEqual(rc4, 0)

            with connection_ctx(Path(db_path)) as conn:
                reminders = conn.execute("SELECT COUNT(*) AS c FROM reminders").fetchone()
                self.assertEqual(reminders["c"], 1)
                task = conn.execute("SELECT status, snooze_until FROM tasks WHERE id = 1").fetchone()
                self.assertEqual(task["status"], "snoozed")
                self.assertEqual(task["snooze_until"], "2026-03-07T00:00:00+00:00")

    def test_reminder_ack_flow_and_pending_ack(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "life.db")
            run_with_output(["--db", db_path, "task", "create", "ack task"])
            run_with_output(["--db", db_path, "reminder", "create", "1", "2026-03-07T00:00:00+00:00"])

            rc1, _ = run_with_output(
                ["--db", db_path, "reminder", "due", "--send", "--now", "2026-03-07T00:00:00+00:00"]
            )
            self.assertEqual(rc1, 0)
            _, pending1 = run_with_output(["--db", db_path, "reminder", "pending-ack"])
            self.assertIn("[1] sent", pending1)

            rc2, _ = run_with_output(["--db", db_path, "reminder", "ack", "1"])
            self.assertEqual(rc2, 0)
            _, pending2 = run_with_output(["--db", db_path, "reminder", "pending-ack"])
            self.assertNotIn("[1] sent", pending2)

            _, show = run_with_output(["--db", db_path, "reminder", "show", "1"])
            self.assertIn("status: acknowledged", show)
            self.assertIn("acked_via: cli", show)

    def test_reminder_snooze_and_skip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "life.db")
            run_with_output(["--db", db_path, "task", "create", "snooze task"])
            run_with_output(["--db", db_path, "reminder", "create", "1", "2026-03-07T00:00:00+00:00"])
            run_with_output(["--db", db_path, "reminder", "due", "--send", "--now", "2026-03-07T00:00:00+00:00"])

            rc1, _ = run_with_output(["--db", db_path, "reminder", "snooze", "1", "2026-03-08T09:00:00+08:00"])
            self.assertEqual(rc1, 0)
            _, show1 = run_with_output(["--db", db_path, "reminder", "show", "1"])
            self.assertIn("status: snoozed", show1)
            self.assertIn("attempt_count: 0", show1)
            self.assertIn("next_retry_at: -", show1)

            run_with_output(["--db", db_path, "task", "create", "skip task"])
            run_with_output(["--db", db_path, "reminder", "create", "2", "2026-03-07T00:00:00+00:00"])
            rc2, _ = run_with_output(["--db", db_path, "reminder", "skip", "2", "--reason", "not needed"])
            self.assertEqual(rc2, 0)
            _, show2 = run_with_output(["--db", db_path, "reminder", "show", "2"])
            self.assertIn("status: skipped", show2)
            self.assertIn("skip_reason: not needed", show2)

    def test_reminder_retry_scheduling_and_expired(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "life.db")
            run_with_output(["--db", db_path, "task", "create", "retry task"])
            run_with_output(["--db", db_path, "reminder", "create", "1", "2026-03-07T00:00:00+00:00"])

            run_with_output(["--db", db_path, "reminder", "due", "--send", "--now", "2026-03-07T00:00:00+00:00"])
            _, show1 = run_with_output(["--db", db_path, "reminder", "show", "1"])
            self.assertIn("attempt_count: 1", show1)
            self.assertIn("next_retry_at: 2026-03-07 08:10:00", show1)

            run_with_output(["--db", db_path, "reminder", "due", "--send", "--now", "2026-03-07T00:10:00+00:00"])
            _, show2 = run_with_output(["--db", db_path, "reminder", "show", "1"])
            self.assertIn("attempt_count: 2", show2)
            self.assertIn("next_retry_at: 2026-03-07 08:40:00", show2)

            run_with_output(["--db", db_path, "reminder", "due", "--send", "--now", "2026-03-07T00:40:00+00:00"])
            _, show3 = run_with_output(["--db", db_path, "reminder", "show", "1"])
            self.assertIn("attempt_count: 3", show3)
            self.assertIn("next_retry_at: 2026-03-07 10:40:00", show3)

            run_with_output(["--db", db_path, "reminder", "due", "--send", "--now", "2026-03-07T02:40:00+00:00"])
            _, show4 = run_with_output(["--db", db_path, "reminder", "show", "1"])
            self.assertIn("status: expired", show4)

    def test_reminder_history_and_cross_user_isolation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "life.db")
            run_with_output(["--db", db_path, "--user", "xiaoyu", "task", "create", "x task"])
            run_with_output(
                ["--db", db_path, "--user", "xiaoyu", "reminder", "create", "1", "2026-03-07T00:00:00+00:00"]
            )
            run_with_output(
                ["--db", db_path, "--user", "xiaoyu", "reminder", "due", "--send", "--now", "2026-03-07T00:00:00+00:00"]
            )

            rc1, out1 = run_with_output(["--db", db_path, "--user", "partner", "reminder", "ack", "1"])
            rc2, out2 = run_with_output(
                ["--db", db_path, "--user", "partner", "reminder", "snooze", "1", "2026-03-08T09:00:00+08:00"]
            )
            rc3, out3 = run_with_output(["--db", db_path, "--user", "partner", "reminder", "skip", "1"])
            self.assertEqual(rc1, 1)
            self.assertEqual(rc2, 1)
            self.assertEqual(rc3, 1)
            self.assertIn("reminder not found", out1)
            self.assertIn("reminder not found", out2)
            self.assertIn("reminder not found", out3)

            _, history = run_with_output(["--db", db_path, "--user", "xiaoyu", "reminder", "history", "1"])
            self.assertIn("created", history)
            self.assertIn("sent", history)
            self.assertIn("task_id=1", history)

    def test_reminder_outputs_display_beijing_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "life.db")
            run_with_output(["--db", db_path, "task", "create", "tz reminder"])
            run_with_output(["--db", db_path, "reminder", "create", "1", "2026-03-07T00:00:00+00:00"])
            _, out_due = run_with_output(["--db", db_path, "reminder", "due", "--now", "2026-03-07T00:00:00+00:00"])
            run_with_output(["--db", db_path, "reminder", "due", "--send", "--now", "2026-03-07T00:00:00+00:00"])

            _, out_pending = run_with_output(["--db", db_path, "reminder", "pending-ack"])
            _, out_show = run_with_output(["--db", db_path, "reminder", "show", "1"])
            _, out_history = run_with_output(["--db", db_path, "reminder", "history", "1"])

            self.assertIn("remind_at=2026-03-07 08:00:00", out_due)
            self.assertIn("retry=2026-03-07 08:10:00", out_pending)
            self.assertIn("remind_at: 2026-03-07 08:00:00", out_show)
            self.assertIn("next_retry_at: 2026-03-07 08:10:00", out_show)
            self.assertIn("2026-03-07", out_history)
            self.assertNotIn("T00:00:00+00:00", out_show)
            self.assertNotIn("+00:00", out_pending)
            self.assertNotIn("+00:00", out_history)

    def test_reminder_storage_remains_utc(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "life.db"
            run_with_output(["--db", str(db_path), "task", "create", "utc storage"])
            run_with_output(["--db", str(db_path), "reminder", "create", "1", "2026-03-07T00:00:00+00:00"])
            run_with_output(["--db", str(db_path), "reminder", "due", "--send", "--now", "2026-03-07T00:00:00+00:00"])
            with connection_ctx(db_path) as conn:
                row = conn.execute(
                    "SELECT remind_at, next_retry_at, last_attempt_at FROM reminders WHERE id = 1"
                ).fetchone()
                self.assertEqual(row["remind_at"], "2026-03-07T00:00:00+00:00")
                self.assertEqual(row["next_retry_at"], "2026-03-07T00:10:00+00:00")
                self.assertEqual(row["last_attempt_at"], "2026-03-07T00:00:00+00:00")

    def test_journal_add_list_today(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "life.db")
            rc1, _ = run_with_output(
                [
                    "--db",
                    db_path,
                    "journal",
                    "add",
                    "finished review",
                    "--type",
                    "activity",
                    "--energy",
                    "4",
                    "--focus",
                    "3",
                    "--mood",
                    "5",
                    "--tags",
                    "study,english",
                ]
            )
            self.assertEqual(rc1, 0)
            _, out_list = run_with_output(["--db", db_path, "journal", "list"])
            self.assertIn("finished review", out_list)
            self.assertIn("[1] activity", out_list)
            self.assertIn("E4 F3 M5", out_list)
            _, out_today = run_with_output(["--db", db_path, "journal", "today"])
            self.assertIn("finished review", out_today)

    def test_journal_list_uses_dash_for_missing_levels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "life.db")
            run_with_output(["--db", db_path, "journal", "add", "no levels", "--type", "checkin"])
            _, out = run_with_output(["--db", db_path, "journal", "list"])
            self.assertIn("E- F- M-", out)
            self.assertNotIn("ENone", out)

    def test_repeated_action_feedback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "life.db")
            run_with_output(["--db", db_path, "capture", "archive me"])
            rc1, out1 = run_with_output(["--db", db_path, "inbox", "triage", "1", "archive"])
            rc2, out2 = run_with_output(["--db", db_path, "inbox", "triage", "1", "archive"])
            self.assertEqual(rc1, 0)
            self.assertEqual(rc2, 0)
            self.assertIn("inbox already archived", out2)

            run_with_output(["--db", db_path, "task", "create", "repeat reminder"])
            run_with_output(["--db", db_path, "reminder", "create", "1", "2026-03-07T00:00:00+00:00"])
            run_with_output(["--db", db_path, "reminder", "due", "--send", "--now", "2026-03-07T00:00:00+00:00"])

            _, ack1 = run_with_output(["--db", db_path, "reminder", "ack", "1"])
            _, ack2 = run_with_output(["--db", db_path, "reminder", "ack", "1"])
            self.assertIn("reminder acknowledged", ack1)
            self.assertIn("already acknowledged", ack2)

            run_with_output(["--db", db_path, "reminder", "create", "1", "2026-03-07T00:00:00+00:00"])
            _, skip1 = run_with_output(["--db", db_path, "reminder", "skip", "2"])
            _, skip2 = run_with_output(["--db", db_path, "reminder", "skip", "2"])
            self.assertIn("reminder skipped", skip1)
            self.assertIn("already skipped", skip2)

            run_with_output(["--db", db_path, "reminder", "create", "1", "2026-03-07T00:00:00+00:00"])
            run_with_output(["--db", db_path, "reminder", "snooze", "3", "2026-03-08T09:00:00+08:00"])
            _, sn2 = run_with_output(["--db", db_path, "reminder", "snooze", "3", "2026-03-08T09:00:00+08:00"])
            self.assertIn("already snoozed to 2026-03-08T09:00:00+08:00", sn2)

    def test_triage_event_to_task_anki_archive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "life.db")
            run_with_output(["--db", db_path, "capture", "to task"])
            run_with_output(["--db", db_path, "capture", "to anki"])
            run_with_output(["--db", db_path, "capture", "to archive"])
            run_with_output(["--db", db_path, "inbox", "triage", "1", "task"])
            run_with_output(["--db", db_path, "inbox", "triage", "2", "anki"])
            run_with_output(["--db", db_path, "inbox", "triage", "3", "archive"])

            with connection_ctx(Path(db_path)) as conn:
                rows = conn.execute(
                    """
                    SELECT inbox_item_id, action, target_type, target_id, created_by
                    FROM triage_events
                    WHERE user_id = 1
                    ORDER BY id ASC
                    """
                ).fetchall()
                self.assertEqual(len(rows), 3)
                self.assertEqual(rows[0]["action"], "to_task")
                self.assertEqual(rows[0]["target_type"], "task")
                self.assertEqual(rows[1]["action"], "to_anki")
                self.assertEqual(rows[1]["target_type"], "anki")
                self.assertEqual(rows[2]["action"], "to_archive")
                self.assertEqual(rows[2]["target_type"], "archive")
                self.assertTrue(all(row["created_by"] == "manual" for row in rows))

    def test_triage_event_source_rule_passthrough(self) -> None:
        class FakeSender:
            def __init__(self, updates: list[dict]):
                self.updates = updates

            def get_updates(self, offset: int | None, limit: int) -> list[dict]:
                del offset
                del limit
                out = self.updates
                self.updates = []
                return out

            def send_message(self, chat_id: str, text: str) -> str:
                del chat_id
                del text
                return "m1"

            def answer_callback_query(self, callback_query_id: str, text: str) -> None:
                del callback_query_id
                del text

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "life.db"
            run_with_output(["--db", str(db_path), "user", "set-telegram", "xiaoyu", "1001"])
            fake = FakeSender(
                [{"update_id": 1, "message": {"chat": {"id": 1001, "type": "private"}, "text": "明天联系房东"}}]
            )
            with patch("life_system.cli.commands._build_telegram_sender_from_env", return_value=fake):
                run_with_output(["--db", str(db_path), "telegram", "poll"])
            run_with_output(["--db", str(db_path), "inbox", "triage", "1", "task"])
            with connection_ctx(db_path) as conn:
                ev = conn.execute(
                    """
                    SELECT source_rule_name, source_rule_version
                    FROM triage_events
                    WHERE user_id = 1 AND inbox_item_id = 1
                    ORDER BY id DESC
                    LIMIT 1
                    """
                ).fetchone()
                self.assertEqual(ev["source_rule_name"], "time_plus_action")
                self.assertEqual(ev["source_rule_version"], "inbox_v1")

    def test_triage_event_multi_user_isolation_and_history_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "life.db")
            run_with_output(["--db", db_path, "--user", "xiaoyu", "capture", "x item"])
            run_with_output(["--db", db_path, "--user", "partner", "capture", "p item"])
            run_with_output(["--db", db_path, "--user", "xiaoyu", "inbox", "triage", "1", "archive"])
            run_with_output(["--db", db_path, "--user", "partner", "inbox", "triage", "2", "archive"])

            _, out_x = run_with_output(["--db", db_path, "--user", "xiaoyu", "inbox", "triage-history", "--limit", "10"])
            _, out_p = run_with_output(["--db", db_path, "--user", "partner", "inbox", "triage-history", "--limit", "10"])
            self.assertIn("inbox=1", out_x)
            self.assertNotIn("inbox=2", out_x)
            self.assertIn("inbox=2", out_p)
            self.assertNotIn("inbox=1", out_p)

            rc_hist, out_hist = run_with_output(["--db", db_path, "--user", "xiaoyu", "inbox", "history", "1"])
            self.assertEqual(rc_hist, 0)
            self.assertIn("action=to_archive", out_hist)

            rc_not_found, out_not_found = run_with_output(
                ["--db", db_path, "--user", "xiaoyu", "inbox", "history", "2"]
            )
            self.assertEqual(rc_not_found, 1)
            self.assertIn("inbox item not found", out_not_found)

    def test_triage_event_write_failure_does_not_rollback_main_action(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "life.db")
            run_with_output(["--db", db_path, "capture", "to archive with fail"])
            with patch("life_system.app.services.TriageEventRepository.create", side_effect=RuntimeError("db write failed")):
                rc, out = run_with_output(["--db", db_path, "inbox", "triage", "1", "archive"])
            self.assertEqual(rc, 0)
            self.assertIn("inbox archived", out)
            self.assertIn("warning: triage_event_write_failed", out)
            with connection_ctx(Path(db_path)) as conn:
                row = conn.execute("SELECT status FROM inbox_items WHERE id = 1").fetchone()
                self.assertEqual(row["status"], "archived")

    def test_duplicate_triage_rejected_after_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "life.db"
            run_with_output(["--db", str(db_path), "capture", "dup triage"])
            rc1, _ = run_with_output(["--db", str(db_path), "inbox", "triage", "1", "task"])
            rc2, out2 = run_with_output(["--db", str(db_path), "inbox", "triage", "1", "task"])
            rc3, out3 = run_with_output(["--db", str(db_path), "inbox", "triage", "1", "anki"])
            rc4, out4 = run_with_output(["--db", str(db_path), "inbox", "triage", "1", "archive"])
            self.assertEqual(rc1, 0)
            self.assertEqual(rc2, 0)
            self.assertEqual(rc3, 0)
            self.assertEqual(rc4, 0)
            self.assertIn("inbox already triaged", out2)
            self.assertIn("inbox already triaged", out3)
            self.assertIn("inbox already triaged", out4)
            with connection_ctx(db_path) as conn:
                t = conn.execute("SELECT COUNT(*) AS c FROM tasks WHERE user_id=1").fetchone()
                a = conn.execute("SELECT COUNT(*) AS c FROM anki_drafts WHERE user_id=1").fetchone()
                e = conn.execute("SELECT COUNT(*) AS c FROM triage_events WHERE user_id=1").fetchone()
                self.assertEqual(t["c"], 1)
                self.assertEqual(a["c"], 0)
                self.assertEqual(e["c"], 1)

    def test_duplicate_triage_rejected_after_archive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "life.db"
            run_with_output(["--db", str(db_path), "capture", "dup archive"])
            rc1, _ = run_with_output(["--db", str(db_path), "inbox", "triage", "1", "archive"])
            rc2, out2 = run_with_output(["--db", str(db_path), "inbox", "triage", "1", "task"])
            self.assertEqual(rc1, 0)
            self.assertEqual(rc2, 0)
            self.assertIn("inbox already archived", out2)
            with connection_ctx(db_path) as conn:
                t = conn.execute("SELECT COUNT(*) AS c FROM tasks WHERE user_id=1").fetchone()
                e = conn.execute("SELECT COUNT(*) AS c FROM triage_events WHERE user_id=1").fetchone()
                self.assertEqual(t["c"], 0)
                self.assertEqual(e["c"], 1)

    def test_feedback_migration_table_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "life.db"
            run_with_output(["--db", str(db_path), "init-db"])
            with connection_ctx(db_path) as conn:
                row = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='inbox_feedback_signals'"
                ).fetchone()
                self.assertIsNotNone(row)

    def test_feedback_scan_idempotent_unique(self) -> None:
        class FakeSender:
            def __init__(self, updates: list[dict]):
                self.updates = updates

            def get_updates(self, offset: int | None, limit: int) -> list[dict]:
                del offset
                del limit
                out = self.updates
                self.updates = []
                return out

            def send_message(self, chat_id: str, text: str) -> str:
                del chat_id
                del text
                return "m1"

            def answer_callback_query(self, callback_query_id: str, text: str) -> None:
                del callback_query_id
                del text

        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "life.db")
            run_with_output(["--db", db_path, "user", "set-telegram", "xiaoyu", "1001"])
            fake = FakeSender([{"update_id": 1, "message": {"chat": {"id": 1001, "type": "private"}, "text": "买耳塞"}}])
            with patch("life_system.cli.commands._build_telegram_sender_from_env", return_value=fake):
                run_with_output(["--db", db_path, "telegram", "poll"])
            run_with_output(["--db", db_path, "inbox", "triage", "1", "task"])
            rc1, out1 = run_with_output(["--db", db_path, "inbox", "feedback-scan", "--now", "2026-03-08T12:30:00+00:00"])
            rc2, out2 = run_with_output(["--db", db_path, "inbox", "feedback-scan", "--now", "2026-03-08T12:31:00+00:00"])
            self.assertEqual(rc1, 0)
            self.assertEqual(rc2, 0)
            self.assertIn("created_signals=1", out1)
            self.assertIn("skipped_existing=1", out2)

    def test_feedback_scan_uses_now_for_signal_created_at(self) -> None:
        class FakeSender:
            def __init__(self, updates: list[dict]):
                self.updates = updates

            def get_updates(self, offset: int | None, limit: int) -> list[dict]:
                del offset
                del limit
                out = self.updates
                self.updates = []
                return out

            def send_message(self, chat_id: str, text: str) -> str:
                del chat_id
                del text
                return "m1"

            def answer_callback_query(self, callback_query_id: str, text: str) -> None:
                del callback_query_id
                del text

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "life.db"
            run_with_output(["--db", str(db_path), "user", "set-telegram", "xiaoyu", "1001"])
            fake = FakeSender([{"update_id": 1, "message": {"chat": {"id": 1001, "type": "private"}, "text": "买耳塞"}}])
            with patch("life_system.cli.commands._build_telegram_sender_from_env", return_value=fake):
                run_with_output(["--db", str(db_path), "telegram", "poll"])
            run_with_output(["--db", str(db_path), "inbox", "triage", "1", "task"])
            run_with_output(["--db", str(db_path), "inbox", "feedback-scan", "--now", "2026-03-08T12:30:00+00:00"])
            with connection_ctx(db_path) as conn:
                row = conn.execute(
                    "SELECT created_at FROM inbox_feedback_signals WHERE user_id=1 ORDER BY id DESC LIMIT 1"
                ).fetchone()
                self.assertEqual(row["created_at"], "2026-03-08T12:30:00+00:00")

    def test_feedback_auto_to_task_anki_archive_24h(self) -> None:
        class FakeSender:
            def __init__(self, updates: list[dict]):
                self.updates = updates

            def get_updates(self, offset: int | None, limit: int) -> list[dict]:
                del offset
                del limit
                out = self.updates
                self.updates = []
                return out

            def send_message(self, chat_id: str, text: str) -> str:
                del chat_id
                del text
                return "m1"

            def answer_callback_query(self, callback_query_id: str, text: str) -> None:
                del callback_query_id
                del text

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "life.db"
            run_with_output(["--db", str(db), "user", "set-telegram", "xiaoyu", "1001"])
            fake = FakeSender(
                [
                    {"update_id": 1, "message": {"chat": {"id": 1001, "type": "private"}, "text": "买耳塞"}},
                    {"update_id": 2, "message": {"chat": {"id": 1001, "type": "private"}, "text": "明天联系房东"}},
                    {"update_id": 3, "message": {"chat": {"id": 1001, "type": "private"}, "text": "待办：改完PPT"}},
                ]
            )
            with patch("life_system.cli.commands._build_telegram_sender_from_env", return_value=fake):
                run_with_output(["--db", str(db), "telegram", "poll"])
            run_with_output(["--db", str(db), "inbox", "triage", "1", "task"])
            run_with_output(["--db", str(db), "inbox", "triage", "2", "anki"])
            run_with_output(["--db", str(db), "inbox", "triage", "3", "archive"])
            run_with_output(["--db", str(db), "inbox", "feedback-scan", "--now", "2026-03-08T12:30:00+00:00"])
            with connection_ctx(db) as conn:
                sigs = conn.execute(
                    "SELECT signal_type FROM inbox_feedback_signals WHERE user_id=1 ORDER BY id ASC"
                ).fetchall()
                types = [row["signal_type"] for row in sigs]
                self.assertIn("auto_to_task_24h", types)
                self.assertIn("auto_to_anki_24h", types)
                self.assertIn("auto_to_archive_24h", types)

    def test_feedback_auto_pending_72h_and_manual_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "life.db"
            run_with_output(["--db", str(db), "init-db"])
            with connection_ctx(db) as conn:
                conn.execute(
                    """
                    INSERT INTO inbox_items(
                      user_id, content, source, status, created_at, created_by, rule_name, rule_version
                    ) VALUES(1, 'auto old', 'telegram_auto', 'new', '2026-03-05T00:00:00+00:00', 'telegram_auto', 'todo_prefix', 'inbox_v1')
                    """
                )
                conn.execute(
                    """
                    INSERT INTO inbox_items(
                      user_id, content, source, status, created_at, created_by
                    ) VALUES(1, 'manual old', 'cli', 'new', '2026-03-05T00:00:00+00:00', 'manual')
                    """
                )
                conn.commit()
            run_with_output(["--db", str(db), "inbox", "feedback-scan", "--now", "2026-03-08T12:30:00+00:00"])
            with connection_ctx(db) as conn:
                rows = conn.execute(
                    "SELECT subject_key, signal_type FROM inbox_feedback_signals WHERE user_id=1 ORDER BY id ASC"
                ).fetchall()
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["signal_type"], "auto_pending_72h")
                self.assertTrue(rows[0]["subject_key"].startswith("inbox:"))

    def test_feedback_review_led_and_no_triage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "life.db"
            run_with_output(["--db", str(db), "init-db"])
            with connection_ctx(db) as conn:
                conn.execute(
                    """
                    INSERT INTO inbox_items(user_id, content, source, status, created_at, created_by)
                    VALUES(1, 'seed inbox', 'cli', 'new', '2026-03-07T12:00:00+00:00', 'manual')
                    """
                )
                conn.execute(
                    "INSERT INTO app_state(key, value, updated_at) VALUES(?, ?, ?)",
                    ("inbox_review_sent:1:2026-03-07", "x", "2026-03-07T12:00:00+00:00"),
                )
                conn.execute(
                    "INSERT INTO app_state(key, value, updated_at) VALUES(?, ?, ?)",
                    ("inbox_review_sent:1:2026-03-08", "x", "2026-03-08T12:00:00+00:00"),
                )
                conn.execute(
                    """
                    INSERT INTO triage_events(
                      user_id, inbox_item_id, action, target_type, target_id, created_at, created_by
                    ) VALUES(1, 1, 'to_task', 'task', 99, '2026-03-07T13:00:00+00:00', 'manual')
                    """
                )
                conn.commit()
            run_with_output(["--db", str(db), "inbox", "feedback-scan", "--now", "2026-03-09T13:00:00+00:00"])
            with connection_ctx(db) as conn:
                rows = conn.execute(
                    """
                    SELECT subject_key, signal_type
                    FROM inbox_feedback_signals
                    WHERE user_id=1 AND subject_type='inbox_review'
                    ORDER BY subject_key ASC
                    """
                ).fetchall()
                mapping = {row["subject_key"]: row["signal_type"] for row in rows}
                self.assertEqual(mapping["inbox_review_sent:1:2026-03-07"], "review_led_to_triage_24h")
                self.assertEqual(mapping["inbox_review_sent:1:2026-03-08"], "review_no_triage_24h")

    def test_feedback_report_user_isolation_and_rule_passthrough(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "life.db"
            run_with_output(["--db", str(db), "init-db"])
            with connection_ctx(db) as conn:
                conn.execute(
                    """
                    INSERT INTO inbox_feedback_signals(
                      user_id, subject_type, subject_key, signal_type, window_hours, created_at, source_rule_name, source_rule_version
                    ) VALUES(1, 'auto_inbox', 'inbox:1', 'auto_to_task_24h', 24, '2026-03-08T00:00:00+00:00', 'explicit_remember', 'inbox_v1')
                    """
                )
                conn.execute(
                    """
                    INSERT INTO inbox_feedback_signals(
                      user_id, subject_type, subject_key, signal_type, window_hours, created_at
                    ) VALUES(2, 'auto_inbox', 'inbox:2', 'auto_to_task_24h', 24, '2026-03-08T00:00:00+00:00')
                    """
                )
                conn.commit()
            rc1, out1 = run_with_output(["--db", str(db), "--user", "xiaoyu", "inbox", "feedback-report", "--limit", "50"])
            rc2, out2 = run_with_output(["--db", str(db), "--user", "partner", "inbox", "feedback-report", "--limit", "50"])
            self.assertEqual(rc1, 0)
            self.assertEqual(rc2, 0)
            self.assertIn("subject=auto_inbox:inbox:1", out1)
            self.assertIn("rule=explicit_remember/inbox_v1", out1)
            self.assertNotIn("subject=auto_inbox:inbox:2", out1)
            self.assertIn("subject=auto_inbox:inbox:2", out2)

    def test_journal_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "life.db")
            rc, out = run_with_output(
                ["--db", db_path, "journal", "add", "bad level", "--type", "win", "--energy", "8"]
            )
            self.assertEqual(rc, 1)
            self.assertIn("invalid energy: must be 1-5", out)

            with self.assertRaises(SystemExit):
                run_cli(["--db", db_path, "journal", "add", "bad type", "--type", "other"])

    def test_journal_multi_user_isolation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "life.db")
            run_with_output(["--db", db_path, "--user", "xiaoyu", "journal", "add", "x entry", "--type", "checkin"])
            run_with_output(["--db", db_path, "--user", "partner", "journal", "add", "p entry", "--type", "reflection"])

            _, out_x = run_with_output(["--db", db_path, "--user", "xiaoyu", "journal", "list"])
            _, out_p = run_with_output(["--db", db_path, "--user", "partner", "journal", "list"])
            self.assertIn("x entry", out_x)
            self.assertNotIn("p entry", out_x)
            self.assertIn("p entry", out_p)
            self.assertNotIn("x entry", out_p)

    def test_migration_backfills_existing_rows_to_xiaoyu(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "life.db"
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE schema_migrations (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, applied_at TEXT)")
            conn.execute(
                "CREATE TABLE inbox_items (id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'cli', status TEXT NOT NULL DEFAULT 'new', created_at TEXT NOT NULL, triaged_at TEXT)"
            )
            conn.execute(
                "CREATE TABLE tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, notes TEXT, status TEXT NOT NULL DEFAULT 'open', priority INTEGER NOT NULL DEFAULT 3, due_at TEXT, inbox_item_id INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, completed_at TEXT, abandoned_at TEXT, snooze_until TEXT)"
            )
            conn.execute(
                "CREATE TABLE abandonment_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER, reason_code TEXT, reason_text TEXT, energy_level INTEGER, created_at TEXT NOT NULL)"
            )
            conn.execute(
                "CREATE TABLE reminders (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL, remind_at TEXT NOT NULL, channel TEXT NOT NULL DEFAULT 'cli', status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL, sent_at TEXT)"
            )
            conn.execute(
                "CREATE TABLE anki_drafts (id INTEGER PRIMARY KEY AUTOINCREMENT, source_type TEXT NOT NULL, source_id INTEGER, deck_name TEXT NOT NULL DEFAULT 'inbox', front TEXT NOT NULL, back TEXT NOT NULL, tags TEXT, status TEXT NOT NULL DEFAULT 'draft', created_at TEXT NOT NULL, exported_at TEXT)"
            )
            conn.execute(
                "INSERT INTO schema_migrations(name, applied_at) VALUES ('001_init.sql', '2026-01-01T00:00:00+00:00')"
            )
            conn.execute(
                "INSERT INTO schema_migrations(name, applied_at) VALUES ('002_task_snooze_until.sql', '2026-01-01T00:00:00+00:00')"
            )
            conn.execute(
                "INSERT INTO inbox_items(content, source, status, created_at) VALUES ('legacy inbox', 'cli', 'new', '2026-01-01T00:00:00+00:00')"
            )
            conn.execute(
                "INSERT INTO tasks(title, status, priority, created_at, updated_at) VALUES ('legacy task', 'open', 3, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
            )
            conn.commit()
            conn.close()

            rc, _ = run_with_output(["--db", str(db_path), "init-db"])
            self.assertEqual(rc, 0)
            with connection_ctx(db_path) as conn2:
                row = conn2.execute("SELECT id FROM users WHERE username = 'xiaoyu'").fetchone()
                self.assertIsNotNone(row)
                xiaoyu_id = row["id"]
                inbox_user = conn2.execute("SELECT user_id FROM inbox_items WHERE id = 1").fetchone()
                task_user = conn2.execute("SELECT user_id FROM tasks WHERE id = 1").fetchone()
                self.assertEqual(inbox_user["user_id"], xiaoyu_id)
                self.assertEqual(task_user["user_id"], xiaoyu_id)

    def test_summary_today_and_day(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "life.db")
            run_with_output(["--db", db_path, "task", "create", "sum task"])
            run_with_output(["--db", db_path, "capture", "sum inbox"])
            run_with_output(["--db", db_path, "inbox", "triage", "1", "archive"])
            run_with_output(["--db", db_path, "anki", "create", "manual", "Q", "A"])
            run_with_output(["--db", db_path, "anki", "export-csv", str(Path(tmp) / "anki.csv")])
            run_with_output(["--db", db_path, "journal", "add", "today activity", "--type", "activity", "--energy", "4"])

            rc1, out1 = run_with_output(["--db", db_path, "summary", "today"])
            self.assertEqual(rc1, 0)
            self.assertIn("每日总结", out1)
            self.assertIn("今日概览", out1)
            self.assertIn("日志条目: 1", out1)

            today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
            rc2, out2 = run_with_output(["--db", db_path, "summary", "day", "--date", today])
            self.assertEqual(rc2, 0)
            self.assertIn(today, out2)

    def test_summary_invalid_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "life.db")
            rc, out = run_with_output(["--db", db_path, "summary", "day", "--date", "2026/03/07"])
            self.assertEqual(rc, 1)
            self.assertIn("invalid date: must be YYYY-MM-DD", out)

    def test_summary_multi_user_isolation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "life.db")
            run_with_output(["--db", db_path, "--user", "xiaoyu", "journal", "add", "x log", "--type", "win"])
            run_with_output(["--db", db_path, "--user", "partner", "journal", "add", "p log", "--type", "win"])

            _, out_x = run_with_output(["--db", db_path, "--user", "xiaoyu", "summary", "today"])
            _, out_p = run_with_output(["--db", db_path, "--user", "partner", "summary", "today"])
            self.assertIn("x log", out_x)
            self.assertNotIn("p log", out_x)
            self.assertIn("p log", out_p)
            self.assertNotIn("x log", out_p)

    def test_summary_state_snapshot_with_and_without_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "life.db")
            run_with_output(["--db", db_path, "journal", "add", "stateful", "--type", "checkin", "--energy", "4", "--focus", "3", "--mood", "5"])
            _, out1 = run_with_output(["--db", db_path, "summary", "today"])
            self.assertIn("平均能量", out1)

        with tempfile.TemporaryDirectory() as tmp2:
            db_path2 = str(Path(tmp2) / "life2.db")
            run_with_output(["--db", db_path2, "journal", "add", "no state", "--type", "activity"])
            _, out2 = run_with_output(["--db", db_path2, "summary", "today"])
            self.assertIn("无状态数据", out2)

    def test_summary_uses_asia_shanghai_day_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "life.db"
            run_with_output(["--db", str(db_path), "init-db"])
            with connection_ctx(db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO tasks(user_id, title, status, priority, created_at, updated_at)
                    VALUES(1, 'boundary task', 'open', 3, '2026-03-06T16:30:00+00:00', '2026-03-06T16:30:00+00:00')
                    """
                )
                conn.commit()

            _, out_cst_day = run_with_output(["--db", str(db_path), "summary", "day", "--date", "2026-03-07"])
            _, out_prev = run_with_output(["--db", str(db_path), "summary", "day", "--date", "2026-03-06"])
            self.assertIn("任务: 新建=1", out_cst_day)
            self.assertIn("任务: 新建=0", out_prev)

    def test_summary_chinese_labels_and_sent_retried_split(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "life.db")
            run_with_output(["--db", db_path, "task", "create", "r task"])
            run_with_output(["--db", db_path, "reminder", "create", "1", "2026-03-07T00:00:00+00:00"])
            run_with_output(["--db", db_path, "reminder", "due", "--send", "--now", "2026-03-07T00:00:00+00:00"])
            run_with_output(["--db", db_path, "reminder", "due", "--send", "--now", "2026-03-07T00:10:00+00:00"])

            today_cst = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
            _, out = run_with_output(["--db", db_path, "summary", "day", "--date", today_cst])
            self.assertIn("收件箱:", out)
            self.assertIn("任务:", out)
            self.assertIn("提醒:", out)
            self.assertIn("未闭环事项", out)
            self.assertNotIn("inbox:", out)
            self.assertIn("首次发送=1", out)
            self.assertIn("重试=1", out)

    def test_summary_journal_time_display_in_beijing_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "life.db"
            run_with_output(["--db", str(db_path), "init-db"])
            with connection_ctx(db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO journal_entries(
                      user_id, entry_type, content, created_at
                    ) VALUES (1, 'activity', 'tz check', '2026-03-07T13:24:00+00:00')
                    """
                )
                conn.commit()

            _, out = run_with_output(["--db", str(db_path), "summary", "day", "--date", "2026-03-07"])
            self.assertIn("2026-03-07 21:24", out)

    def test_summary_journal_highlights_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "life.db")
            for i in range(5):
                run_with_output(["--db", db_path, "journal", "add", f"log-{i}", "--type", "activity"])
            _, out = run_with_output(["--db", db_path, "summary", "today"])
            lines = [line for line in out.splitlines() if line.startswith("- 活动") or line.startswith("- 反思") or line.startswith("- 小胜利") or line.startswith("- 状态记录")]
            self.assertLessEqual(len(lines), 3)

    def test_summary_note_is_stable_chinese_template(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "life.db")
            run_with_output(["--db", db_path, "journal", "add", "only log", "--type", "activity"])
            _, out = run_with_output(["--db", db_path, "summary", "today"])
            allowed = [
                "今天有持续记录，也有实际推进，可以继续保持这种小步前进。",
                "今天有真实完成项，节奏是稳定的。",
                "今天留下了清晰的活动和状态证据，说明你没有脱离系统。",
                "今天虽然正式完成项不多，但有真实记录和闭环动作。",
                "今天证据还不多，先补一条简短记录会更稳。",
            ]
            self.assertTrue(any(note in out for note in allowed))

    def test_reminder_due_send_missing_token_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "life.db")
            run_with_output(["--db", db_path, "user", "set-telegram", "xiaoyu", "123456"])
            run_with_output(["--db", db_path, "task", "create", "tg task"])
            run_with_output(["--db", db_path, "reminder", "create", "1", "2026-03-07T00:00:00+00:00"])

            with patch.dict("os.environ", {}, clear=True):
                rc, out = run_with_output(
                    ["--db", db_path, "reminder", "due", "--send", "--now", "2026-03-07T00:00:00+00:00"]
                )
            self.assertEqual(rc, 1)
            self.assertIn("TELEGRAM_BOT_TOKEN 未设置", out)

    def test_reminder_due_send_fallback_without_chat_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "life.db")
            run_with_output(["--db", db_path, "task", "create", "fallback task"])
            run_with_output(["--db", db_path, "reminder", "create", "1", "2026-03-07T00:00:00+00:00"])
            rc, out = run_with_output(["--db", db_path, "reminder", "due", "--send", "--now", "2026-03-07T00:00:00+00:00"])
            self.assertEqual(rc, 0)
            self.assertIn("reminders processed: 1, failed: 0", out)

    def test_reminder_due_send_success_with_mocked_sender(self) -> None:
        class FakeSender:
            def send_message(self, chat_id: str, text: str) -> str:
                assert chat_id == "999999"
                assert "提醒：" in text
                return "m123"

        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "life.db")
            run_with_output(["--db", db_path, "user", "set-telegram", "xiaoyu", "999999"])
            run_with_output(["--db", db_path, "task", "create", "tg send task"])
            run_with_output(["--db", db_path, "reminder", "create", "1", "2026-03-07T00:00:00+00:00"])

            with patch("life_system.cli.commands._build_telegram_sender_from_env", return_value=FakeSender()):
                rc, out = run_with_output(
                    ["--db", db_path, "reminder", "due", "--send", "--now", "2026-03-07T00:00:00+00:00"]
                )
            self.assertEqual(rc, 0)
            self.assertIn("reminders processed: 1, failed: 0", out)
            _, show = run_with_output(["--db", db_path, "reminder", "show", "1"])
            self.assertIn("message_ref: m123", show)

    def test_no_token_leakage_in_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "life.db")
            run_with_output(["--db", db_path, "user", "set-telegram", "xiaoyu", "123456"])
            run_with_output(["--db", db_path, "task", "create", "secure task"])
            run_with_output(["--db", db_path, "reminder", "create", "1", "2026-03-07T00:00:00+00:00"])
            with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "SECRET_TOKEN_ABC"}):
                with patch("life_system.cli.commands._build_telegram_sender_from_env", return_value=None):
                    rc, out = run_with_output(
                        ["--db", db_path, "reminder", "due", "--send", "--now", "2026-03-07T00:00:00+00:00"]
                    )
            self.assertEqual(rc, 1)
            self.assertNotIn("SECRET_TOKEN_ABC", out)

    def test_telegram_poll_missing_token_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "life.db")
            with patch.dict("os.environ", {}, clear=True):
                rc, out = run_with_output(["--db", db_path, "telegram", "poll"])
            self.assertEqual(rc, 1)
            self.assertIn("TELEGRAM_BOT_TOKEN 未设置", out)

    def test_telegram_poll_ack_snooze_skip_and_already_processed(self) -> None:
        class FakeSender:
            def __init__(self, updates: list[dict]):
                self.updates = updates
                self.answers: list[tuple[str, str]] = []

            def get_updates(self, offset: int | None, limit: int) -> list[dict]:
                del offset
                del limit
                out = self.updates
                self.updates = []
                return out

            def answer_callback_query(self, callback_query_id: str, text: str) -> None:
                self.answers.append((callback_query_id, text))

        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "life.db")
            run_with_output(["--db", db_path, "user", "set-telegram", "xiaoyu", "1001"])
            run_with_output(["--db", db_path, "task", "create", "tg action task"])
            run_with_output(["--db", db_path, "reminder", "create", "1", "2026-03-07T00:00:00+00:00"])
            run_with_output(["--db", db_path, "reminder", "create", "1", "2026-03-07T00:00:00+00:00"])
            run_with_output(["--db", db_path, "reminder", "create", "1", "2026-03-07T00:00:00+00:00"])
            fake = FakeSender(
                [
                    {"update_id": 1, "callback_query": {"id": "c1", "data": "ra:1", "message": {"chat": {"id": 1001}}}},
                    {"update_id": 2, "callback_query": {"id": "c2", "data": "rz:2", "message": {"chat": {"id": 1001}}}},
                    {"update_id": 3, "callback_query": {"id": "c3", "data": "rk:3", "message": {"chat": {"id": 1001}}}},
                    {"update_id": 4, "callback_query": {"id": "c4", "data": "ra:1", "message": {"chat": {"id": 1001}}}},
                ]
            )
            with patch("life_system.cli.commands._build_telegram_sender_from_env", return_value=fake):
                rc, _ = run_with_output(["--db", db_path, "telegram", "poll"])
            self.assertEqual(rc, 0)
            _, s1 = run_with_output(["--db", db_path, "reminder", "show", "1"])
            _, s2 = run_with_output(["--db", db_path, "reminder", "show", "2"])
            _, s3 = run_with_output(["--db", db_path, "reminder", "show", "3"])
            self.assertIn("status: acknowledged", s1)
            self.assertIn("acked_via: telegram", s1)
            self.assertIn("status: snoozed", s2)
            self.assertIn("status: skipped", s3)
            self.assertIn(("c1", "已确认"), fake.answers)
            self.assertIn(("c4", "已经处理过了"), fake.answers)

    def test_telegram_poll_user_isolation(self) -> None:
        class FakeSender:
            def __init__(self, updates: list[dict]):
                self.updates = updates
                self.answers: list[tuple[str, str]] = []

            def get_updates(self, offset: int | None, limit: int) -> list[dict]:
                del offset
                del limit
                out = self.updates
                self.updates = []
                return out

            def answer_callback_query(self, callback_query_id: str, text: str) -> None:
                self.answers.append((callback_query_id, text))

        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "life.db")
            run_with_output(["--db", db_path, "user", "set-telegram", "xiaoyu", "1001"])
            run_with_output(["--db", db_path, "user", "set-telegram", "partner", "2002"])
            run_with_output(["--db", db_path, "--user", "xiaoyu", "task", "create", "x task"])
            run_with_output(["--db", db_path, "--user", "xiaoyu", "reminder", "create", "1", "2026-03-07T00:00:00+00:00"])

            fake = FakeSender(
                [{"update_id": 10, "callback_query": {"id": "c10", "data": "ra:1", "message": {"chat": {"id": 2002}}}}]
            )
            with patch("life_system.cli.commands._build_telegram_sender_from_env", return_value=fake):
                rc, _ = run_with_output(["--db", db_path, "telegram", "poll"])
            self.assertEqual(rc, 0)
            _, show = run_with_output(["--db", db_path, "--user", "xiaoyu", "reminder", "show", "1"])
            self.assertNotIn("status: acknowledged", show)
            self.assertIn(("c10", "提醒不存在或无权限"), fake.answers)

    def test_telegram_poll_offset_persistence(self) -> None:
        class FakeSender:
            def __init__(self):
                self.calls: list[int | None] = []
                self.updates_seq = [
                    [{"update_id": 100, "callback_query": {"id": "c100", "data": "ra:1", "message": {"chat": {"id": 1001}}}}],
                    [],
                ]
                self.answers: list[tuple[str, str]] = []

            def get_updates(self, offset: int | None, limit: int) -> list[dict]:
                del limit
                self.calls.append(offset)
                return self.updates_seq.pop(0) if self.updates_seq else []

            def answer_callback_query(self, callback_query_id: str, text: str) -> None:
                self.answers.append((callback_query_id, text))

        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "life.db")
            run_with_output(["--db", db_path, "user", "set-telegram", "xiaoyu", "1001"])
            run_with_output(["--db", db_path, "task", "create", "offset task"])
            run_with_output(["--db", db_path, "reminder", "create", "1", "2026-03-07T00:00:00+00:00"])
            fake = FakeSender()
            with patch("life_system.cli.commands._build_telegram_sender_from_env", return_value=fake):
                run_with_output(["--db", db_path, "telegram", "poll"])
                run_with_output(["--db", db_path, "telegram", "poll"])
            self.assertEqual(fake.calls[0], None)
            self.assertEqual(fake.calls[1], 101)

    def test_answer_callback_failure_does_not_block_following_callbacks_and_offset(self) -> None:
        class FakeSender:
            def __init__(self):
                self.answers: list[tuple[str, str]] = []
                self.calls: list[int | None] = []
                self.updates = [
                    {"update_id": 201, "callback_query": {"id": "c201", "data": "ra:1", "message": {"chat": {"id": 1001}}}},
                    {"update_id": 202, "callback_query": {"id": "c202", "data": "ra:2", "message": {"chat": {"id": 1001}}}},
                ]

            def get_updates(self, offset: int | None, limit: int) -> list[dict]:
                del limit
                self.calls.append(offset)
                out = self.updates
                self.updates = []
                return out

            def answer_callback_query(self, callback_query_id: str, text: str) -> None:
                if callback_query_id == "c201":
                    raise RuntimeError("telegram_http_error 400: Bad Request: query is too old")
                self.answers.append((callback_query_id, text))

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "life.db"
            run_with_output(["--db", str(db_path), "user", "set-telegram", "xiaoyu", "1001"])
            run_with_output(["--db", str(db_path), "task", "create", "a1"])
            run_with_output(["--db", str(db_path), "reminder", "create", "1", "2026-03-07T00:00:00+00:00"])
            run_with_output(["--db", str(db_path), "reminder", "create", "1", "2026-03-07T00:00:00+00:00"])
            fake = FakeSender()
            with patch("life_system.cli.commands._build_telegram_sender_from_env", return_value=fake):
                rc, out = run_with_output(["--db", str(db_path), "telegram", "poll"])
            self.assertEqual(rc, 0)
            self.assertIn("processed=2", out)
            _, r1 = run_with_output(["--db", str(db_path), "reminder", "show", "1"])
            _, r2 = run_with_output(["--db", str(db_path), "reminder", "show", "2"])
            self.assertIn("status: acknowledged", r1)
            self.assertIn("status: acknowledged", r2)
            self.assertIn(("c202", "已确认"), fake.answers)
            with connection_ctx(db_path) as conn:
                row = conn.execute("SELECT value FROM app_state WHERE key='telegram.update_offset'").fetchone()
                self.assertEqual(row["value"], "203")

    def test_telegram_http_error_description_visible_without_token(self) -> None:
        class BrokenSender:
            def get_updates(self, offset: int | None, limit: int) -> list[dict]:
                del offset
                del limit
                raise RuntimeError("telegram_http_error 400: Bad Request: query is too old")

        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "life.db")
            with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "SECRET_TOKEN_ABC"}):
                with patch("life_system.cli.commands._build_telegram_sender_from_env", return_value=BrokenSender()):
                    rc, out = run_with_output(["--db", db_path, "telegram", "poll"])
            self.assertEqual(rc, 1)
            self.assertIn("telegram poll failed: telegram_http_error 400", out)
            self.assertNotIn("SECRET_TOKEN_ABC", out)

    def test_telegram_poll_plain_text_to_activity(self) -> None:
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

            def answer_callback_query(self, callback_query_id: str, text: str) -> None:
                del callback_query_id
                del text

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "life.db"
            run_with_output(["--db", str(db_path), "user", "set-telegram", "xiaoyu", "1001"])
            fake = FakeSender(
                [{"update_id": 1, "message": {"chat": {"id": 1001, "type": "private"}, "text": "今天完成了背单词"}}]
            )
            with patch("life_system.cli.commands._build_telegram_sender_from_env", return_value=fake):
                rc, out = run_with_output(["--db", str(db_path), "telegram", "poll"])
            self.assertEqual(rc, 0)
            self.assertIn("messages=1", out)
            with connection_ctx(db_path) as conn:
                row = conn.execute(
                    "SELECT entry_type, content, energy_level, focus_level, mood_level FROM journal_entries WHERE user_id=1"
                ).fetchone()
                self.assertEqual(row["entry_type"], "activity")
                self.assertEqual(row["content"], "今天完成了背单词")
                self.assertIsNone(row["energy_level"])
                inbox_count = conn.execute("SELECT COUNT(*) AS c FROM inbox_items WHERE user_id=1").fetchone()
                self.assertEqual(inbox_count["c"], 0)
            self.assertTrue(any("活动" in text for _, text in fake.sent))

    def test_telegram_poll_r_w_c_to_journal_types(self) -> None:
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

            def answer_callback_query(self, callback_query_id: str, text: str) -> None:
                del callback_query_id
                del text

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "life.db"
            run_with_output(["--db", str(db_path), "user", "set-telegram", "xiaoyu", "1001"])
            fake = FakeSender(
                [
                    {"update_id": 1, "message": {"chat": {"id": 1001, "type": "private"}, "text": "/r 今天启动很难"}},
                    {"update_id": 2, "message": {"chat": {"id": 1001, "type": "private"}, "text": "/w 今天至少没脱离系统"}},
                    {
                        "update_id": 3,
                        "message": {
                            "chat": {"id": 1001, "type": "private"},
                            "text": "/c energy=2 focus=2 mood=3 今天状态一般",
                        },
                    },
                ]
            )
            with patch("life_system.cli.commands._build_telegram_sender_from_env", return_value=fake):
                rc, out = run_with_output(["--db", str(db_path), "telegram", "poll"])
            self.assertEqual(rc, 0)
            self.assertIn("messages=3", out)
            with connection_ctx(db_path) as conn:
                rows = conn.execute(
                    "SELECT entry_type, content, energy_level, focus_level, mood_level FROM journal_entries ORDER BY id ASC"
                ).fetchall()
                self.assertEqual(rows[0]["entry_type"], "reflection")
                self.assertEqual(rows[1]["entry_type"], "win")
                self.assertEqual(rows[2]["entry_type"], "checkin")
                self.assertEqual(rows[2]["energy_level"], 2)
                self.assertEqual(rows[2]["focus_level"], 2)
                self.assertEqual(rows[2]["mood_level"], 3)
                inbox_count = conn.execute("SELECT COUNT(*) AS c FROM inbox_items WHERE user_id=1").fetchone()
                self.assertEqual(inbox_count["c"], 0)

    def test_telegram_focus_buttons_write_checkin_with_default_content(self) -> None:
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

            def send_message_with_focus_keyboard(self, chat_id: str, text: str) -> str:
                self.sent.append((chat_id, text))
                return "m1"

            def send_message(self, chat_id: str, text: str) -> str:
                self.sent.append((chat_id, text))
                return "m1"

            def answer_callback_query(self, callback_query_id: str, text: str) -> None:
                del callback_query_id
                del text

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "life.db"
            run_with_output(["--db", str(db_path), "user", "set-telegram", "xiaoyu", "1001"])
            updates = []
            labels = ["1 很难专注", "2 比较分散", "3 一般", "4 比较专注", "5 高度专注"]
            for i, label in enumerate(labels, start=1):
                updates.append({"update_id": i, "message": {"chat": {"id": 1001, "type": "private"}, "text": label}})
            fake = FakeSender(updates)
            with patch("life_system.cli.commands._build_telegram_sender_from_env", return_value=fake):
                rc, out = run_with_output(["--db", str(db_path), "telegram", "poll"])
            self.assertEqual(rc, 0)
            self.assertIn("messages=5", out)
            with connection_ctx(db_path) as conn:
                rows = conn.execute(
                    "SELECT entry_type, content, focus_level, energy_level, mood_level FROM journal_entries ORDER BY id ASC"
                ).fetchall()
                self.assertEqual(len(rows), 5)
                for idx, row in enumerate(rows, start=1):
                    self.assertEqual(row["entry_type"], "checkin")
                    self.assertEqual(row["content"], "状态签到")
                    self.assertEqual(row["focus_level"], idx)
                    self.assertIsNone(row["energy_level"])
                    self.assertIsNone(row["mood_level"])

    def test_telegram_poll_invalid_checkin_values_and_empty_payload(self) -> None:
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

            def answer_callback_query(self, callback_query_id: str, text: str) -> None:
                del callback_query_id
                del text

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "life.db"
            run_with_output(["--db", str(db_path), "user", "set-telegram", "xiaoyu", "1001"])
            fake = FakeSender(
                [
                    {"update_id": 1, "message": {"chat": {"id": 1001, "type": "private"}, "text": "/c energy=9 今天很乱"}},
                    {"update_id": 2, "message": {"chat": {"id": 1001, "type": "private"}, "text": "/r   "}},
                ]
            )
            with patch("life_system.cli.commands._build_telegram_sender_from_env", return_value=fake):
                rc, out = run_with_output(["--db", str(db_path), "telegram", "poll"])
            self.assertEqual(rc, 0)
            self.assertIn("messages=0", out)
            self.assertIn("ignored=2", out)
            self.assertIn("invalid_payload:1", out)
            self.assertIn("empty_payload:1", out)
            with connection_ctx(db_path) as conn:
                row = conn.execute("SELECT COUNT(*) AS c FROM journal_entries").fetchone()
                self.assertEqual(row["c"], 0)
            self.assertTrue(any("1 到 5" in text for _, text in fake.sent))
            self.assertTrue(any("未识别到可记录内容" in text for _, text in fake.sent))

    def test_telegram_poll_chat_id_int_string_match(self) -> None:
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

            def answer_callback_query(self, callback_query_id: str, text: str) -> None:
                del callback_query_id
                del text

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "life.db"
            run_with_output(["--db", str(db_path), "init-db"])
            with connection_ctx(db_path) as conn:
                conn.execute("UPDATE users SET telegram_chat_id = ? WHERE username = 'xiaoyu'", (8045312073,))
                conn.commit()

            fake = FakeSender(
                [{"update_id": 1, "message": {"chat": {"id": 8045312073, "type": "private"}, "text": "hello journal test"}}]
            )
            with patch("life_system.cli.commands._build_telegram_sender_from_env", return_value=fake):
                rc, out = run_with_output(["--db", str(db_path), "telegram", "poll"])
            self.assertEqual(rc, 0)
            self.assertIn("messages=1", out)
            with connection_ctx(db_path) as conn:
                row = conn.execute("SELECT entry_type, content FROM journal_entries WHERE user_id = 1").fetchone()
                self.assertEqual(row["entry_type"], "activity")
                self.assertEqual(row["content"], "hello journal test")

    def test_telegram_poll_unknown_chat_and_non_text_ignored(self) -> None:
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

            def answer_callback_query(self, callback_query_id: str, text: str) -> None:
                del callback_query_id
                del text

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "life.db"
            run_with_output(["--db", str(db_path), "user", "set-telegram", "xiaoyu", "1001"])
            fake = FakeSender(
                [
                    {"update_id": 1, "message": {"chat": {"id": 9999, "type": "private"}, "text": "unknown user"}},
                    {"update_id": 2, "message": {"chat": {"id": 1001, "type": "private"}, "photo": [{"file_id": "x"}]}},
                    {"update_id": 3, "message": {"chat": {"id": 1001, "type": "group"}, "text": "group text"}},
                    {"update_id": 4, "message": {"chat": {"id": 1001, "type": "private"}, "text": "/start"}},
                ]
            )
            with patch("life_system.cli.commands._build_telegram_sender_from_env", return_value=fake):
                rc, out = run_with_output(["--db", str(db_path), "telegram", "poll"])
            self.assertEqual(rc, 0)
            self.assertIn("processed=0", out)
            self.assertIn("ignored=4", out)
            with connection_ctx(db_path) as conn:
                row = conn.execute("SELECT COUNT(*) AS c FROM journal_entries").fetchone()
                self.assertEqual(row["c"], 0)
            self.assertEqual(fake.sent, [])

    def test_telegram_poll_message_reply_failure_does_not_rollback_journal(self) -> None:
        class FakeSender:
            def __init__(self, updates: list[dict]):
                self.updates = updates

            def get_updates(self, offset: int | None, limit: int) -> list[dict]:
                del offset
                del limit
                out = self.updates
                self.updates = []
                return out

            def send_message(self, chat_id: str, text: str) -> str:
                del chat_id
                del text
                raise RuntimeError("telegram_http_error 400: blocked")

            def answer_callback_query(self, callback_query_id: str, text: str) -> None:
                del callback_query_id
                del text

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "life.db"
            run_with_output(["--db", str(db_path), "user", "set-telegram", "xiaoyu", "1001"])
            fake = FakeSender(
                [{"update_id": 1, "message": {"chat": {"id": 1001, "type": "private"}, "text": "只要记下来就好"}}]
            )
            with patch("life_system.cli.commands._build_telegram_sender_from_env", return_value=fake):
                rc, out = run_with_output(["--db", str(db_path), "telegram", "poll"])
            self.assertEqual(rc, 0)
            self.assertIn("messages=1", out)
            with connection_ctx(db_path) as conn:
                row = conn.execute("SELECT entry_type, content FROM journal_entries WHERE user_id=1").fetchone()
                self.assertEqual(row["entry_type"], "activity")
                self.assertEqual(row["content"], "只要记下来就好")

    def test_telegram_poll_offset_with_message_and_callback(self) -> None:
        class FakeSender:
            def __init__(self):
                self.calls: list[int | None] = []
                self.updates_seq = [
                    [
                        {"update_id": 21, "message": {"chat": {"id": 1001, "type": "private"}, "text": "m1"}},
                        {"update_id": 22, "callback_query": {"id": "c22", "data": "ra:1", "message": {"chat": {"id": 1001}}}},
                    ],
                    [],
                ]
                self.answers: list[tuple[str, str]] = []
                self.sent: list[tuple[str, str]] = []

            def get_updates(self, offset: int | None, limit: int) -> list[dict]:
                del limit
                self.calls.append(offset)
                return self.updates_seq.pop(0) if self.updates_seq else []

            def send_message(self, chat_id: str, text: str) -> str:
                self.sent.append((chat_id, text))
                return "m1"

            def answer_callback_query(self, callback_query_id: str, text: str) -> None:
                self.answers.append((callback_query_id, text))

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "life.db"
            run_with_output(["--db", str(db_path), "user", "set-telegram", "xiaoyu", "1001"])
            run_with_output(["--db", str(db_path), "task", "create", "offset mix task"])
            run_with_output(["--db", str(db_path), "reminder", "create", "1", "2026-03-07T00:00:00+00:00"])
            fake = FakeSender()
            with patch("life_system.cli.commands._build_telegram_sender_from_env", return_value=fake):
                run_with_output(["--db", str(db_path), "telegram", "poll"])
                run_with_output(["--db", str(db_path), "telegram", "poll"])
            self.assertEqual(fake.calls[0], None)
            self.assertEqual(fake.calls[1], 23)

    def test_telegram_setup_menu_command(self) -> None:
        class FakeSender:
            def setup_menu(self) -> dict[str, bool]:
                return {"commands": True, "menu_button": True}

        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "life.db")
            with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "TOKEN"}):
                with patch("life_system.cli.commands._build_telegram_sender_from_env", return_value=FakeSender()):
                    rc, out = run_with_output(["--db", db_path, "telegram", "setup-menu"])
            self.assertEqual(rc, 0)
            self.assertIn("/r /w /c /ir /help", out)

    def test_telegram_setup_keyboard_command(self) -> None:
        class FakeSender:
            def __init__(self):
                self.chat_ids: list[str] = []

            def setup_focus_keyboard(self, chat_id: str) -> None:
                self.chat_ids.append(chat_id)

        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "life.db")
            run_with_output(["--db", db_path, "user", "set-telegram", "xiaoyu", "1001"])
            run_with_output(["--db", db_path, "user", "set-telegram", "partner", "2002"])
            fake = FakeSender()
            with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "TOKEN"}):
                with patch("life_system.cli.commands._build_telegram_sender_from_env", return_value=fake):
                    rc, out = run_with_output(["--db", db_path, "telegram", "setup-keyboard"])
            self.assertEqual(rc, 0)
            self.assertIn("pushed=2", out)
            self.assertEqual(sorted(fake.chat_ids), ["1001", "2002"])

    def test_telegram_inbox_review_sends_items_with_limit(self) -> None:
        class FakeSender:
            def __init__(self):
                self.sent: list[tuple[str, int, str]] = []

            def send_inbox_review_item(self, chat_id: str, inbox_id: int, content: str) -> str:
                self.sent.append((chat_id, inbox_id, content))
                return "m1"

        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "life.db")
            run_with_output(["--db", db_path, "user", "set-telegram", "xiaoyu", "1001"])
            run_with_output(["--db", db_path, "--user", "xiaoyu", "capture", "item-1"])
            run_with_output(["--db", db_path, "--user", "xiaoyu", "capture", "item-2"])
            run_with_output(["--db", db_path, "--user", "xiaoyu", "capture", "item-3"])
            fake = FakeSender()
            with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "TOKEN"}):
                with patch("life_system.cli.commands._build_telegram_sender_from_env", return_value=fake):
                    rc, out = run_with_output(["--db", db_path, "--user", "xiaoyu", "telegram", "inbox-review", "--limit", "2"])
            self.assertEqual(rc, 0)
            self.assertIn("sent=2", out)
            self.assertEqual(len(fake.sent), 2)
            self.assertEqual([x[2] for x in fake.sent], ["item-1", "item-2"])

    def test_telegram_inbox_callback_task_archive_keep(self) -> None:
        class FakeSender:
            def __init__(self, updates: list[dict]):
                self.updates = updates
                self.answers: list[tuple[str, str]] = []
                self.cleared: list[tuple[str, int]] = []

            def get_updates(self, offset: int | None, limit: int) -> list[dict]:
                del offset
                del limit
                out = self.updates
                self.updates = []
                return out

            def answer_callback_query(self, callback_query_id: str, text: str) -> None:
                self.answers.append((callback_query_id, text))

            def clear_message_inline_keyboard(self, chat_id: str, message_id: int) -> None:
                self.cleared.append((chat_id, message_id))

            def send_message(self, chat_id: str, text: str) -> str:
                del chat_id
                del text
                return "m1"

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "life.db"
            run_with_output(["--db", str(db_path), "user", "set-telegram", "xiaoyu", "1001"])
            run_with_output(["--db", str(db_path), "--user", "xiaoyu", "capture", "to task"])
            run_with_output(["--db", str(db_path), "--user", "xiaoyu", "capture", "to archive"])
            run_with_output(["--db", str(db_path), "--user", "xiaoyu", "capture", "to keep"])
            fake = FakeSender(
                [
                    {"update_id": 1, "callback_query": {"id": "c1", "data": "it:1", "message": {"chat": {"id": 1001}, "message_id": 11}}},
                    {"update_id": 2, "callback_query": {"id": "c2", "data": "ia:2", "message": {"chat": {"id": 1001}, "message_id": 12}}},
                    {"update_id": 3, "callback_query": {"id": "c3", "data": "ik:3", "message": {"chat": {"id": 1001}, "message_id": 13}}},
                ]
            )
            with patch("life_system.cli.commands._build_telegram_sender_from_env", return_value=fake):
                rc, out = run_with_output(["--db", str(db_path), "telegram", "poll"])
            self.assertEqual(rc, 0)
            self.assertIn("processed=3", out)
            self.assertIn(("c1", "已转为任务"), fake.answers)
            self.assertIn(("c2", "已归档"), fake.answers)
            self.assertIn(("c3", "先留在收件箱"), fake.answers)
            with connection_ctx(db_path) as conn:
                t = conn.execute("SELECT COUNT(*) AS c FROM tasks WHERE user_id=1").fetchone()
                i1 = conn.execute("SELECT status FROM inbox_items WHERE id=1").fetchone()
                i2 = conn.execute("SELECT status FROM inbox_items WHERE id=2").fetchone()
                i3 = conn.execute("SELECT status FROM inbox_items WHERE id=3").fetchone()
                te = conn.execute("SELECT COUNT(*) AS c FROM triage_events WHERE user_id=1").fetchone()
                self.assertEqual(t["c"], 1)
                self.assertEqual(i1["status"], "triaged")
                self.assertEqual(i2["status"], "archived")
                self.assertEqual(i3["status"], "new")
                self.assertEqual(te["c"], 2)

    def test_telegram_inbox_callback_duplicate_and_invalid_and_continue(self) -> None:
        class FakeSender:
            def __init__(self, updates: list[dict]):
                self.updates = updates
                self.answers: list[tuple[str, str]] = []

            def get_updates(self, offset: int | None, limit: int) -> list[dict]:
                del offset
                del limit
                out = self.updates
                self.updates = []
                return out

            def answer_callback_query(self, callback_query_id: str, text: str) -> None:
                self.answers.append((callback_query_id, text))

            def clear_message_inline_keyboard(self, chat_id: str, message_id: int) -> None:
                del chat_id
                del message_id

            def send_message(self, chat_id: str, text: str) -> str:
                del chat_id
                del text
                return "m1"

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "life.db"
            run_with_output(["--db", str(db_path), "user", "set-telegram", "xiaoyu", "1001"])
            run_with_output(["--db", str(db_path), "--user", "xiaoyu", "capture", "dup test"])
            run_with_output(["--db", str(db_path), "--user", "xiaoyu", "inbox", "triage", "1", "task"])
            run_with_output(["--db", str(db_path), "--user", "xiaoyu", "capture", "another"])
            fake = FakeSender(
                [
                    {"update_id": 1, "callback_query": {"id": "c1", "data": "it:1", "message": {"chat": {"id": 1001}, "message_id": 11}}},
                    {"update_id": 2, "callback_query": {"id": "c2", "data": "xx:2", "message": {"chat": {"id": 1001}, "message_id": 12}}},
                    {"update_id": 3, "callback_query": {"id": "c3", "data": "ia:2", "message": {"chat": {"id": 1001}, "message_id": 13}}},
                ]
            )
            with patch("life_system.cli.commands._build_telegram_sender_from_env", return_value=fake):
                rc, out = run_with_output(["--db", str(db_path), "telegram", "poll"])
            self.assertEqual(rc, 0)
            self.assertIn("processed=3", out)
            self.assertIn(("c1", "已处理过了"), fake.answers)
            self.assertIn(("c2", "无法识别操作"), fake.answers)
            self.assertIn(("c3", "已归档"), fake.answers)
            with connection_ctx(db_path) as conn:
                t = conn.execute("SELECT COUNT(*) AS c FROM tasks WHERE user_id=1").fetchone()
                te = conn.execute("SELECT COUNT(*) AS c FROM triage_events WHERE user_id=1").fetchone()
                self.assertEqual(t["c"], 1)
                self.assertEqual(te["c"], 2)

    def test_telegram_inbox_callback_multi_user_isolation(self) -> None:
        class FakeSender:
            def __init__(self, updates: list[dict]):
                self.updates = updates
                self.answers: list[tuple[str, str]] = []

            def get_updates(self, offset: int | None, limit: int) -> list[dict]:
                del offset
                del limit
                out = self.updates
                self.updates = []
                return out

            def answer_callback_query(self, callback_query_id: str, text: str) -> None:
                self.answers.append((callback_query_id, text))

            def clear_message_inline_keyboard(self, chat_id: str, message_id: int) -> None:
                del chat_id
                del message_id

            def send_message(self, chat_id: str, text: str) -> str:
                del chat_id
                del text
                return "m1"

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "life.db"
            run_with_output(["--db", str(db_path), "user", "set-telegram", "xiaoyu", "1001"])
            run_with_output(["--db", str(db_path), "user", "set-telegram", "partner", "2002"])
            run_with_output(["--db", str(db_path), "--user", "xiaoyu", "capture", "x only"])
            fake = FakeSender(
                [{"update_id": 1, "callback_query": {"id": "c1", "data": "ia:1", "message": {"chat": {"id": 2002}, "message_id": 11}}}]
            )
            with patch("life_system.cli.commands._build_telegram_sender_from_env", return_value=fake):
                rc, _ = run_with_output(["--db", str(db_path), "telegram", "poll"])
            self.assertEqual(rc, 0)
            self.assertIn(("c1", "收件箱不存在或无权限"), fake.answers)

    def test_telegram_inbox_callback_rule_passthrough_to_triage_event(self) -> None:
        class FakeSender:
            def __init__(self, updates: list[dict]):
                self.updates = updates
                self.answers: list[tuple[str, str]] = []

            def get_updates(self, offset: int | None, limit: int) -> list[dict]:
                del offset
                del limit
                out = self.updates
                self.updates = []
                return out

            def answer_callback_query(self, callback_query_id: str, text: str) -> None:
                self.answers.append((callback_query_id, text))

            def clear_message_inline_keyboard(self, chat_id: str, message_id: int) -> None:
                del chat_id
                del message_id

            def send_message(self, chat_id: str, text: str) -> str:
                del chat_id
                del text
                return "m1"

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "life.db"
            run_with_output(["--db", str(db_path), "init-db"])
            with connection_ctx(db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO inbox_items(
                      user_id, content, source, status, created_at, created_by, rule_name, rule_version
                    ) VALUES(1, 'auto item', 'telegram_auto', 'new', '2026-03-07T00:00:00+00:00', 'telegram_auto', 'todo_prefix', 'inbox_v1')
                    """
                )
                conn.execute("UPDATE users SET telegram_chat_id='1001' WHERE username='xiaoyu'")
                conn.commit()
            fake = FakeSender(
                [{"update_id": 1, "callback_query": {"id": "c1", "data": "it:1", "message": {"chat": {"id": 1001}, "message_id": 11}}}]
            )
            with patch("life_system.cli.commands._build_telegram_sender_from_env", return_value=fake):
                rc, _ = run_with_output(["--db", str(db_path), "telegram", "poll"])
            self.assertEqual(rc, 0)
            with connection_ctx(db_path) as conn:
                row = conn.execute(
                    """
                    SELECT source_rule_name, source_rule_version, created_by
                    FROM triage_events
                    WHERE user_id=1
                    ORDER BY id DESC
                    LIMIT 1
                    """
                ).fetchone()
                self.assertEqual(row["source_rule_name"], "todo_prefix")
                self.assertEqual(row["source_rule_version"], "inbox_v1")
                self.assertEqual(row["created_by"], "telegram_auto_followup")

    def test_telegram_help_reply(self) -> None:
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

            def answer_callback_query(self, callback_query_id: str, text: str) -> None:
                del callback_query_id
                del text

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "life.db"
            run_with_output(["--db", str(db_path), "user", "set-telegram", "xiaoyu", "1001"])
            fake = FakeSender([{"update_id": 1, "message": {"chat": {"id": 1001, "type": "private"}, "text": "/help"}}])
            with patch("life_system.cli.commands._build_telegram_sender_from_env", return_value=fake):
                rc, out = run_with_output(["--db", str(db_path), "telegram", "poll"])
            self.assertEqual(rc, 0)
            self.assertIn("messages=1", out)
            self.assertTrue(any("普通文本" in text and "/r" in text and "/w" in text and "/c" in text for _, text in fake.sent))

    def test_telegram_activity_plain_text_no_inbox(self) -> None:
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

            def answer_callback_query(self, callback_query_id: str, text: str) -> None:
                del callback_query_id
                del text

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "life.db"
            run_with_output(["--db", str(db_path), "user", "set-telegram", "xiaoyu", "1001"])
            fake = FakeSender(
                [{"update_id": 1, "message": {"chat": {"id": 1001, "type": "private"}, "text": "hello journal fix"}}]
            )
            with patch("life_system.cli.commands._build_telegram_sender_from_env", return_value=fake):
                rc, out = run_with_output(["--db", str(db_path), "telegram", "poll"])
            self.assertEqual(rc, 0)
            self.assertIn("inbox_created=0", out)
            with connection_ctx(db_path) as conn:
                j = conn.execute("SELECT entry_type, content FROM journal_entries WHERE user_id=1").fetchone()
                self.assertEqual(j["entry_type"], "activity")
                self.assertEqual(j["content"], "hello journal fix")
                i = conn.execute("SELECT COUNT(*) AS c FROM inbox_items WHERE user_id=1").fetchone()
                self.assertEqual(i["c"], 0)

    def test_telegram_activity_inbox_strong_signal_cases(self) -> None:
        cases = [
            "记得给老师发邮件",
            "明天联系房东",
            "买耳塞",
        ]

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

            def answer_callback_query(self, callback_query_id: str, text: str) -> None:
                del callback_query_id
                del text

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "life.db"
            run_with_output(["--db", str(db_path), "user", "set-telegram", "xiaoyu", "1001"])
            updates = []
            for idx, msg in enumerate(cases, start=1):
                updates.append({"update_id": idx, "message": {"chat": {"id": 1001, "type": "private"}, "text": msg}})
            fake = FakeSender(updates)
            with patch("life_system.cli.commands._build_telegram_sender_from_env", return_value=fake):
                rc, out = run_with_output(["--db", str(db_path), "telegram", "poll"])
            self.assertEqual(rc, 0)
            self.assertIn("inbox_created=3", out)
            with connection_ctx(db_path) as conn:
                inbox_rows = conn.execute(
                    "SELECT content, source FROM inbox_items WHERE user_id=1 ORDER BY id ASC"
                ).fetchall()
                self.assertEqual(len(inbox_rows), 3)
                self.assertEqual([r["content"] for r in inbox_rows], cases)
                self.assertTrue(all(r["source"] == "telegram_auto" for r in inbox_rows))
            self.assertTrue(any("已加入收件箱" in text for _, text in fake.sent))

    def test_telegram_activity_no_inbox_exclusion_cases(self) -> None:
        cases = [
            "今天很累",
            "我刚刚把作业交了",
            "要不要换个学习方法",
        ]

        class FakeSender:
            def __init__(self, updates: list[dict]):
                self.updates = updates

            def get_updates(self, offset: int | None, limit: int) -> list[dict]:
                del offset
                del limit
                out = self.updates
                self.updates = []
                return out

            def send_message(self, chat_id: str, text: str) -> str:
                del chat_id
                del text
                return "m1"

            def answer_callback_query(self, callback_query_id: str, text: str) -> None:
                del callback_query_id
                del text

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "life.db"
            run_with_output(["--db", str(db_path), "user", "set-telegram", "xiaoyu", "1001"])
            updates = []
            for idx, msg in enumerate(cases, start=1):
                updates.append({"update_id": idx, "message": {"chat": {"id": 1001, "type": "private"}, "text": msg}})
            fake = FakeSender(updates)
            with patch("life_system.cli.commands._build_telegram_sender_from_env", return_value=fake):
                rc, out = run_with_output(["--db", str(db_path), "telegram", "poll"])
            self.assertEqual(rc, 0)
            self.assertIn("messages=3", out)
            self.assertIn("inbox_created=0", out)
            with connection_ctx(db_path) as conn:
                j_count = conn.execute("SELECT COUNT(*) AS c FROM journal_entries WHERE user_id=1").fetchone()
                i_count = conn.execute("SELECT COUNT(*) AS c FROM inbox_items WHERE user_id=1").fetchone()
                self.assertEqual(j_count["c"], 3)
                self.assertEqual(i_count["c"], 0)

    def test_telegram_inbox_create_failure_does_not_rollback_journal(self) -> None:
        class FakeSender:
            def __init__(self, updates: list[dict]):
                self.updates = updates

            def get_updates(self, offset: int | None, limit: int) -> list[dict]:
                del offset
                del limit
                out = self.updates
                self.updates = []
                return out

            def send_message(self, chat_id: str, text: str) -> str:
                del chat_id
                del text
                return "m1"

            def answer_callback_query(self, callback_query_id: str, text: str) -> None:
                del callback_query_id
                del text

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "life.db"
            run_with_output(["--db", str(db_path), "user", "set-telegram", "xiaoyu", "1001"])
            fake = FakeSender([{"update_id": 1, "message": {"chat": {"id": 1001, "type": "private"}, "text": "买耳塞"}}])
            with patch("life_system.app.telegram_polling.LifeSystemService.capture_inbox", side_effect=RuntimeError("db failed")):
                with patch("life_system.cli.commands._build_telegram_sender_from_env", return_value=fake):
                    rc, out = run_with_output(["--db", str(db_path), "telegram", "poll"])
            self.assertEqual(rc, 0)
            self.assertIn("messages=1", out)
            self.assertIn("inbox_created=0", out)
            self.assertIn("inbox_failed=1", out)
            with connection_ctx(db_path) as conn:
                j_count = conn.execute("SELECT COUNT(*) AS c FROM journal_entries WHERE user_id=1").fetchone()
                i_count = conn.execute("SELECT COUNT(*) AS c FROM inbox_items WHERE user_id=1").fetchone()
                self.assertEqual(j_count["c"], 1)
                self.assertEqual(i_count["c"], 0)

    def test_inbox_review_empty_no_send(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "life.db")
            rc, out = run_with_output(["--db", db_path, "inbox", "review-send", "--now", "2026-03-08T12:30:00+00:00"])
            self.assertEqual(rc, 0)
            self.assertIn("checked_users=2", out)
            self.assertIn("sent=0", out)
            self.assertIn("skipped_empty=2", out)

    def test_inbox_review_send_and_no_repeat_same_day(self) -> None:
        class FakeSender:
            def __init__(self):
                self.sent: list[tuple[str, str]] = []

            def send_message(self, chat_id: str, text: str) -> str:
                self.sent.append((chat_id, text))
                return "m1"

        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "life.db")
            run_with_output(["--db", db_path, "user", "set-telegram", "xiaoyu", "1001"])
            run_with_output(["--db", db_path, "--user", "xiaoyu", "capture", "待办：改完PPT"])
            fake = FakeSender()
            with patch("life_system.cli.commands._build_telegram_sender_from_env", return_value=fake):
                rc1, out1 = run_with_output(
                    ["--db", db_path, "inbox", "review-send", "--now", "2026-03-08T12:30:00+00:00"]
                )
                rc2, out2 = run_with_output(
                    ["--db", db_path, "inbox", "review-send", "--now", "2026-03-08T13:00:00+00:00"]
                )
            self.assertEqual(rc1, 0)
            self.assertEqual(rc2, 0)
            self.assertIn("sent=1", out1)
            self.assertIn("escalated=0", out1)
            self.assertIn("skipped_already_sent=1", out2)
            self.assertEqual(len(fake.sent), 1)

    def test_inbox_review_send_uses_now_for_app_state_timestamp(self) -> None:
        class FakeSender:
            def send_message(self, chat_id: str, text: str) -> str:
                del chat_id
                del text
                return "m1"

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "life.db"
            run_with_output(["--db", str(db_path), "user", "set-telegram", "xiaoyu", "1001"])
            run_with_output(["--db", str(db_path), "--user", "xiaoyu", "capture", "pending"])
            with patch("life_system.cli.commands._build_telegram_sender_from_env", return_value=FakeSender()):
                run_with_output(["--db", str(db_path), "inbox", "review-send", "--now", "2026-03-08T12:30:00+00:00"])
            with connection_ctx(db_path) as conn:
                row = conn.execute(
                    "SELECT updated_at, value FROM app_state WHERE key='inbox_review_sent:1:2026-03-08'"
                ).fetchone()
                self.assertEqual(row["updated_at"], "2026-03-08T12:30:00+00:00")
                self.assertEqual(row["value"], "2026-03-08T12:30:00+00:00")

    def test_inbox_review_escalated_by_count(self) -> None:
        class FakeSender:
            def __init__(self):
                self.sent: list[tuple[str, str]] = []

            def send_message(self, chat_id: str, text: str) -> str:
                self.sent.append((chat_id, text))
                return "m1"

        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "life.db")
            run_with_output(["--db", db_path, "user", "set-telegram", "xiaoyu", "1001"])
            for i in range(7):
                run_with_output(["--db", db_path, "--user", "xiaoyu", "capture", f"n-{i}"])
            fake = FakeSender()
            with patch("life_system.cli.commands._build_telegram_sender_from_env", return_value=fake):
                rc, out = run_with_output(["--db", db_path, "inbox", "review-send", "--now", "2026-03-08T12:30:00+00:00"])
            self.assertEqual(rc, 0)
            self.assertIn("escalated=1", out)
            self.assertTrue(any("强提醒" in text for _, text in fake.sent))

    def test_inbox_review_escalated_by_oldest_72h(self) -> None:
        class FakeSender:
            def __init__(self):
                self.sent: list[tuple[str, str]] = []

            def send_message(self, chat_id: str, text: str) -> str:
                self.sent.append((chat_id, text))
                return "m1"

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "life.db"
            run_with_output(["--db", str(db_path), "user", "set-telegram", "xiaoyu", "1001"])
            with connection_ctx(db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO inbox_items(user_id, content, source, status, created_at, created_by)
                    VALUES(1, 'old', 'cli', 'new', '2026-03-05T12:00:00+00:00', 'manual')
                    """
                )
                conn.commit()
            fake = FakeSender()
            with patch("life_system.cli.commands._build_telegram_sender_from_env", return_value=fake):
                rc, out = run_with_output(["--db", str(db_path), "inbox", "review-send", "--now", "2026-03-08T12:30:00+00:00"])
            self.assertEqual(rc, 0)
            self.assertIn("escalated=1", out)
            self.assertTrue(any("强提醒" in text for _, text in fake.sent))

    def test_inbox_review_fallback_cli_without_chat_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "life.db")
            run_with_output(["--db", db_path, "--user", "partner", "capture", "partner pending"])
            rc, out = run_with_output(["--db", db_path, "inbox", "review-send", "--now", "2026-03-08T12:30:00+00:00"])
            self.assertEqual(rc, 0)
            self.assertIn("fallback_cli=1", out)
            self.assertIn("sent=1", out)

    def test_inbox_review_beijing_day_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "life.db")
            run_with_output(["--db", db_path, "--user", "partner", "capture", "boundary pending"])
            rc1, out1 = run_with_output(["--db", db_path, "inbox", "review-send", "--now", "2026-03-08T15:59:00+00:00"])
            rc2, out2 = run_with_output(["--db", db_path, "inbox", "review-send", "--now", "2026-03-09T12:30:00+00:00"])
            self.assertEqual(rc1, 0)
            self.assertEqual(rc2, 0)
            self.assertIn("sent=1", out1)
            self.assertIn("sent=1", out2)

    def test_telegram_auto_inbox_metadata(self) -> None:
        class FakeSender:
            def __init__(self, updates: list[dict]):
                self.updates = updates

            def get_updates(self, offset: int | None, limit: int) -> list[dict]:
                del offset
                del limit
                out = self.updates
                self.updates = []
                return out

            def send_message(self, chat_id: str, text: str) -> str:
                del chat_id
                del text
                return "m1"

            def answer_callback_query(self, callback_query_id: str, text: str) -> None:
                del callback_query_id
                del text

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "life.db"
            run_with_output(["--db", str(db_path), "user", "set-telegram", "xiaoyu", "1001"])
            fake = FakeSender(
                [{"update_id": 1, "message": {"chat": {"id": 1001, "type": "private"}, "text": "明天联系房东"}}]
            )
            with patch("life_system.cli.commands._build_telegram_sender_from_env", return_value=fake):
                rc, out = run_with_output(["--db", str(db_path), "telegram", "poll"])
            self.assertEqual(rc, 0)
            self.assertIn("inbox_created=1", out)
            with connection_ctx(db_path) as conn:
                row = conn.execute(
                    """
                    SELECT source, created_by, rule_name, rule_version, source_journal_entry_id
                    FROM inbox_items
                    WHERE user_id = 1
                    ORDER BY id DESC
                    LIMIT 1
                    """
                ).fetchone()
                self.assertEqual(row["source"], "telegram_auto")
                self.assertEqual(row["created_by"], "telegram_auto")
                self.assertEqual(row["rule_name"], "time_plus_action")
                self.assertEqual(row["rule_version"], "inbox_v1")
                self.assertIsNotNone(row["source_journal_entry_id"])

    def test_setup_menu_output_includes_ir(self) -> None:
        class FakeSender:
            def setup_menu(self) -> dict[str, bool]:
                return {"commands": True, "menu_button": True}

        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "life.db")
            with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "TOKEN"}):
                with patch("life_system.cli.commands._build_telegram_sender_from_env", return_value=FakeSender()):
                    rc, out = run_with_output(["--db", db_path, "telegram", "setup-menu"])
        self.assertEqual(rc, 0)
        self.assertIn("/ir", out)

    def test_inbox_review_send_uses_entry_buttons_and_idempotent_due(self) -> None:
        class FakeSender:
            def __init__(self):
                self.entries: list[tuple[str, str, int, bool, bool]] = []

            def send_auto_inbox_review_entry(
                self, chat_id: str, day_yyyymmdd: str, count: int, strong: bool, allow_snooze: bool
            ) -> str:
                self.entries.append((chat_id, day_yyyymmdd, count, strong, allow_snooze))
                return "m42"

        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "life.db")
            run_with_output(["--db", db_path, "user", "set-telegram", "xiaoyu", "1001"])
            run_with_output(["--db", db_path, "--user", "xiaoyu", "capture", "a"])
            fake = FakeSender()
            with patch("life_system.cli.commands._build_telegram_sender_from_env", return_value=fake):
                rc1, out1 = run_with_output(["--db", db_path, "inbox", "review-send", "--now", "2026-03-08T12:30:00+00:00"])
                rc2, out2 = run_with_output(["--db", db_path, "inbox", "review-send", "--now", "2026-03-08T12:35:00+00:00"])
            self.assertEqual(rc1, 0)
            self.assertEqual(rc2, 0)
            self.assertIn("sent=1", out1)
            self.assertIn("skipped_already_sent=1", out2)
            self.assertEqual(len(fake.entries), 1)
            self.assertEqual(fake.entries[0][1], "20260308")
            with connection_ctx(Path(db_path)) as conn:
                row = conn.execute(
                    "SELECT value, updated_at FROM app_state WHERE key='inbox_review_session:1:2026-03-08'"
                ).fetchone()
                self.assertEqual(row["updated_at"], "2026-03-08T12:30:00+00:00")
                data = json.loads(row["value"])
                self.assertEqual(data["status"], "offered")
                self.assertEqual(data["last_offered_due_at"], data["due_at"])

    def test_inbox_review_snooze_then_send_at_new_due(self) -> None:
        class FakeSender:
            def __init__(self, updates: list[dict]):
                self.updates = updates
                self.entries: list[str] = []
                self.answers: list[tuple[str, str]] = []

            def send_auto_inbox_review_entry(
                self, chat_id: str, day_yyyymmdd: str, count: int, strong: bool, allow_snooze: bool
            ) -> str:
                del chat_id, count, strong, allow_snooze
                self.entries.append(day_yyyymmdd)
                return "m1"

            def get_updates(self, offset: int | None, limit: int) -> list[dict]:
                del offset, limit
                out = self.updates
                self.updates = []
                return out

            def answer_callback_query(self, callback_query_id: str, text: str) -> None:
                self.answers.append((callback_query_id, text))

            def clear_message_inline_keyboard(self, chat_id: str, message_id: int) -> None:
                del chat_id, message_id

            def send_message(self, chat_id: str, text: str) -> str:
                del chat_id, text
                return "m2"

        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "life.db")
            run_with_output(["--db", db_path, "user", "set-telegram", "xiaoyu", "1001"])
            run_with_output(["--db", db_path, "--user", "xiaoyu", "capture", "a"])
            fake = FakeSender(
                [{"update_id": 1, "callback_query": {"id": "c1", "data": "irn:20260308", "message": {"chat": {"id": 1001}, "message_id": 10}}}]
            )
            with patch("life_system.cli.commands._build_telegram_sender_from_env", return_value=fake):
                run_with_output(["--db", db_path, "inbox", "review-send", "--now", "2026-03-08T12:30:00+00:00"])
                run_with_output(["--db", db_path, "telegram", "poll"])
                rc_early, out_early = run_with_output(
                    ["--db", db_path, "inbox", "review-send", "--now", "2026-03-08T12:50:00+00:00"]
                )
                rc_due, out_due = run_with_output(
                    ["--db", db_path, "inbox", "review-send", "--now", "2026-03-08T13:00:00+00:00"]
                )
            self.assertEqual(rc_early, 0)
            self.assertEqual(rc_due, 0)
            self.assertIn("sent=0", out_early)
            self.assertIn("sent=1", out_due)
            self.assertTrue(any(a[0] == "c1" for a in fake.answers))

    def test_manual_ir_confirm_then_start(self) -> None:
        class FakeSender:
            def __init__(self, updates: list[dict]):
                self.updates = updates
                self.prompts: list[int] = []
                self.items: list[int] = []
                self.answers: list[tuple[str, str]] = []

            def get_updates(self, offset: int | None, limit: int) -> list[dict]:
                del offset, limit
                out = self.updates
                self.updates = []
                return out

            def send_manual_inbox_review_prompt(self, chat_id: str, count: int) -> str:
                del chat_id
                self.prompts.append(count)
                return "m1"

            def send_inbox_review_item(self, chat_id: str, inbox_id: int, content: str) -> str:
                del chat_id, content
                self.items.append(inbox_id)
                return "m2"

            def answer_callback_query(self, callback_query_id: str, text: str) -> None:
                self.answers.append((callback_query_id, text))

            def clear_message_inline_keyboard(self, chat_id: str, message_id: int) -> None:
                del chat_id, message_id

            def send_message(self, chat_id: str, text: str) -> str:
                del chat_id, text
                return "m3"

        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "life.db")
            run_with_output(["--db", db_path, "user", "set-telegram", "xiaoyu", "1001"])
            run_with_output(["--db", db_path, "--user", "xiaoyu", "capture", "item-1"])
            run_with_output(["--db", db_path, "--user", "xiaoyu", "capture", "item-2"])
            fake = FakeSender(
                [
                    {"update_id": 1, "message": {"chat": {"id": 1001, "type": "private"}, "text": "/ir"}},
                    {"update_id": 2, "callback_query": {"id": "c2", "data": "irms", "message": {"chat": {"id": 1001}, "message_id": 20}}},
                ]
            )
            with patch("life_system.cli.commands._build_telegram_sender_from_env", return_value=fake):
                rc, out = run_with_output(["--db", db_path, "telegram", "poll"])
            self.assertEqual(rc, 0)
            self.assertIn("processed=2", out)
            self.assertEqual(fake.prompts, [2])
            self.assertEqual(fake.items, [1, 2])
            self.assertTrue(any(a[0] == "c2" for a in fake.answers))

    def test_manual_ir_cancel_does_not_change_daily_session(self) -> None:
        class FakeSender:
            def __init__(self, updates: list[dict]):
                self.updates = updates
                self.answers: list[tuple[str, str]] = []

            def get_updates(self, offset: int | None, limit: int) -> list[dict]:
                del offset, limit
                out = self.updates
                self.updates = []
                return out

            def answer_callback_query(self, callback_query_id: str, text: str) -> None:
                self.answers.append((callback_query_id, text))

            def clear_message_inline_keyboard(self, chat_id: str, message_id: int) -> None:
                del chat_id, message_id

            def send_manual_inbox_review_prompt(self, chat_id: str, count: int) -> str:
                del chat_id, count
                return "m1"

            def send_message(self, chat_id: str, text: str) -> str:
                del chat_id, text
                return "m2"

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "life.db"
            run_with_output(["--db", str(db_path), "user", "set-telegram", "xiaoyu", "1001"])
            run_with_output(["--db", str(db_path), "--user", "xiaoyu", "capture", "item-1"])
            fake = FakeSender(
                [
                    {"update_id": 1, "message": {"chat": {"id": 1001, "type": "private"}, "text": "/ir"}},
                    {"update_id": 2, "callback_query": {"id": "c2", "data": "irmc", "message": {"chat": {"id": 1001}, "message_id": 20}}},
                ]
            )
            with patch("life_system.cli.commands._build_telegram_sender_from_env", return_value=fake):
                rc, out = run_with_output(["--db", str(db_path), "telegram", "poll"])
            self.assertEqual(rc, 0)
            self.assertIn("processed=2", out)
            with connection_ctx(db_path) as conn:
                row = conn.execute(
                    "SELECT COUNT(*) AS c FROM app_state WHERE key LIKE 'inbox_review_session:1:%'"
                ).fetchone()
                self.assertEqual(row["c"], 0)


    def test_anki_review_due_and_review_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "life.db")
            run_with_output(["--db", db_path, "anki", "create", "manual", "Q1", "A1", "--deck-name", "default"])
            rc1, out1 = run_with_output(["--db", db_path, "anki", "review-due", "--limit", "20"])
            self.assertEqual(rc1, 0)
            self.assertIn("Q1", out1)

            rc2, out2 = run_with_output(["--db", db_path, "anki", "review", "1", "--rate", "good"])
            self.assertEqual(rc2, 0)
            self.assertIn("anki card reviewed", out2)

            with connection_ctx(db_path) as conn:
                card = conn.execute("SELECT state, reps FROM anki_cards WHERE id=1").fetchone()
                self.assertEqual(card["state"], "review")
                self.assertEqual(card["reps"], 1)
                event_count = conn.execute(
                    "SELECT COUNT(*) AS c FROM anki_review_events WHERE card_id=1"
                ).fetchone()["c"]
                self.assertEqual(event_count, 1)

    def test_anki_card_dedupe_warning_on_duplicate_draft(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "life.db")
            run_with_output(["--db", db_path, "anki", "create", "manual", "Q1", "A1", "--deck-name", "default"])
            rc, out = run_with_output(["--db", db_path, "anki", "create", "manual", "  Q1  ", "A1", "--deck-name", "default"])
            self.assertEqual(rc, 0)
            self.assertIn("warning: anki_card_duplicate_detected", out)
            with connection_ctx(db_path) as conn:
                c = conn.execute("SELECT COUNT(*) AS c FROM anki_cards").fetchone()["c"]
                self.assertEqual(c, 1)



if __name__ == "__main__":
    unittest.main()
