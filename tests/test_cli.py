import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
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


if __name__ == "__main__":
    unittest.main()
