import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path

from life_system.cli.commands import run_cli
from life_system.infra.db import connection_ctx


def run_with_output(args: list[str]) -> tuple[int, str]:
    buf = StringIO()
    with redirect_stdout(buf):
        rc = run_cli(args)
    return rc, buf.getvalue()


class TestCliFlows(unittest.TestCase):
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
            self.assertIn("next_retry_at: None", show1)

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
            self.assertIn("next_retry_at: 2026-03-07T00:10:00+00:00", show1)

            run_with_output(["--db", db_path, "reminder", "due", "--send", "--now", "2026-03-07T00:10:00+00:00"])
            _, show2 = run_with_output(["--db", db_path, "reminder", "show", "1"])
            self.assertIn("attempt_count: 2", show2)
            self.assertIn("next_retry_at: 2026-03-07T00:40:00+00:00", show2)

            run_with_output(["--db", db_path, "reminder", "due", "--send", "--now", "2026-03-07T00:40:00+00:00"])
            _, show3 = run_with_output(["--db", db_path, "reminder", "show", "1"])
            self.assertIn("attempt_count: 3", show3)
            self.assertIn("next_retry_at: 2026-03-07T02:40:00+00:00", show3)

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
            self.assertIn("already archived", out2)

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

            _, out = run_with_output(["--db", db_path, "summary", "day", "--date", "2026-03-07"])
            self.assertIn("收件箱:", out)
            self.assertIn("任务:", out)
            self.assertIn("提醒:", out)
            self.assertIn("未闭环事项", out)
            self.assertNotIn("inbox:", out)
            self.assertIn("首次发送=1", out)
            self.assertIn("重试=1", out)


if __name__ == "__main__":
    unittest.main()
