import tempfile
import unittest
from pathlib import Path

from life_system.cli.commands import run_cli
from life_system.infra.db import connection_ctx


class TestCliFlows(unittest.TestCase):
    def test_init_db_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "life.db")
            rc1 = run_cli(["--db", db_path, "init-db"])
            rc2 = run_cli(["--db", db_path, "init-db"])
            self.assertEqual(rc1, 0)
            self.assertEqual(rc2, 0)
            self.assertTrue(Path(db_path).exists())

    def test_capture_alias_creates_inbox_item(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "life.db")
            rc = run_cli(["--db", db_path, "capture", "word review"])
            self.assertEqual(rc, 0)
            with connection_ctx(Path(db_path)) as conn:
                row = conn.execute("SELECT content, status FROM inbox_items ORDER BY id DESC LIMIT 1").fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(row["content"], "word review")
                self.assertEqual(row["status"], "new")

    def test_task_snooze_and_inbox_triaged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "life.db")
            run_cli(["--db", db_path, "capture", "clean desk"])
            rc = run_cli(["--db", db_path, "task", "create", "clean desk", "--inbox-id", "1"])
            self.assertEqual(rc, 0)
            rc = run_cli(["--db", db_path, "task", "snooze", "1", "2026-03-08T09:00:00+08:00"])
            self.assertEqual(rc, 0)

            with connection_ctx(Path(db_path)) as conn:
                task = conn.execute("SELECT status, snooze_until FROM tasks WHERE id = 1").fetchone()
                inbox = conn.execute("SELECT status, triaged_at FROM inbox_items WHERE id = 1").fetchone()
                self.assertIsNotNone(task)
                self.assertEqual(task["status"], "snoozed")
                self.assertEqual(task["snooze_until"], "2026-03-08T09:00:00+08:00")
                self.assertIsNotNone(inbox)
                self.assertEqual(inbox["status"], "triaged")
                self.assertIsNotNone(inbox["triaged_at"])

    def test_inbox_triage_to_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "life.db")
            run_cli(["--db", db_path, "capture", "plan sprint"])
            rc = run_cli(["--db", db_path, "inbox", "triage", "1", "task"])
            self.assertEqual(rc, 0)
            with connection_ctx(Path(db_path)) as conn:
                task = conn.execute("SELECT title FROM tasks WHERE inbox_item_id = 1").fetchone()
                inbox = conn.execute("SELECT status FROM inbox_items WHERE id = 1").fetchone()
                self.assertIsNotNone(task)
                self.assertEqual(task["title"], "plan sprint")
                self.assertEqual(inbox["status"], "triaged")

    def test_inbox_triage_to_anki(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "life.db")
            run_cli(["--db", db_path, "capture", "what is next action"])
            rc = run_cli(["--db", db_path, "inbox", "triage", "1", "anki"])
            self.assertEqual(rc, 0)
            with connection_ctx(Path(db_path)) as conn:
                draft = conn.execute("SELECT source_type, source_id, front FROM anki_drafts WHERE id = 1").fetchone()
                inbox = conn.execute("SELECT status FROM inbox_items WHERE id = 1").fetchone()
                self.assertIsNotNone(draft)
                self.assertEqual(draft["source_type"], "inbox")
                self.assertEqual(draft["source_id"], 1)
                self.assertEqual(draft["front"], "what is next action")
                self.assertEqual(inbox["status"], "triaged")

    def test_inbox_triage_to_archive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "life.db")
            run_cli(["--db", db_path, "capture", "random note"])
            rc = run_cli(["--db", db_path, "inbox", "triage", "1", "archive"])
            self.assertEqual(rc, 0)
            with connection_ctx(Path(db_path)) as conn:
                inbox = conn.execute("SELECT status FROM inbox_items WHERE id = 1").fetchone()
                self.assertEqual(inbox["status"], "archived")

    def test_abandon_reason_preset_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "life.db")
            run_cli(["--db", db_path, "task", "create", "write report"])
            rc = run_cli(["--db", db_path, "task", "abandon", "1", "--reason-code", "overwhelm"])
            self.assertEqual(rc, 0)
            with connection_ctx(Path(db_path)) as conn:
                row = conn.execute("SELECT reason_code FROM abandonment_logs WHERE task_id = 1").fetchone()
                self.assertEqual(row["reason_code"], "overwhelm")
            with self.assertRaises(SystemExit):
                run_cli(["--db", db_path, "task", "abandon", "1", "--reason-code", "bad_reason"])

    def test_anki_export_csv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "life.db")
            out_path = Path(tmp) / "anki.csv"
            run_cli(["--db", db_path, "anki", "create", "manual", "Q1", "A1"])
            run_cli(["--db", db_path, "anki", "create", "manual", "Q2", "A2"])
            rc = run_cli(["--db", db_path, "anki", "export-csv", str(out_path)])
            self.assertEqual(rc, 0)
            self.assertTrue(out_path.exists())
            text = out_path.read_text(encoding="utf-8")
            self.assertIn("id,source_type,source_id,deck_name,front,back,tags,status,created_at", text)
            self.assertIn("Q1", text)
            self.assertIn("Q2", text)


if __name__ == "__main__":
    unittest.main()

