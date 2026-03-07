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
            rc = run_cli(["--db", db_path, "capture", "背单词"])
            self.assertEqual(rc, 0)
            with connection_ctx(Path(db_path)) as conn:
                row = conn.execute("SELECT content, status FROM inbox_items ORDER BY id DESC LIMIT 1").fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(row["content"], "背单词")
                self.assertEqual(row["status"], "new")

    def test_task_snooze_and_inbox_triaged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "life.db")
            run_cli(["--db", db_path, "capture", "收拾桌面"])
            rc = run_cli(["--db", db_path, "task", "create", "收拾桌面", "--inbox-id", "1"])
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


if __name__ == "__main__":
    unittest.main()
