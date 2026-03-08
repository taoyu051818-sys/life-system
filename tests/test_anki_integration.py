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


class TestAnkiIntegration(unittest.TestCase):
    def test_anki_show_trace_for_inbox_triage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "life.db"
            run_with_output(["--db", str(db_path), "init-db"])
            with connection_ctx(db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO journal_entries(
                      user_id, entry_type, content, created_at
                    ) VALUES(1, 'activity', 'source journal', '2026-03-08T00:00:00+00:00')
                    """
                )
                conn.execute(
                    """
                    INSERT INTO inbox_items(
                      user_id, content, source, status, created_at, source_journal_entry_id, created_by, rule_name, rule_version
                    ) VALUES(1, 'anki from inbox', 'telegram_auto', 'new', '2026-03-08T01:00:00+00:00', 1, 'telegram_auto', 'time_plus_action', 'inbox_v1')
                    """
                )
                conn.commit()

            rc_triage, _ = run_with_output(["--db", str(db_path), "inbox", "triage", "1", "anki"])
            self.assertEqual(rc_triage, 0)
            rc_show, out_show = run_with_output(["--db", str(db_path), "anki", "show", "1"])
            self.assertEqual(rc_show, 0)
            self.assertIn("source_inbox_item_id: 1", out_show)
            self.assertIn("source_journal_entry_id: 1", out_show)
            self.assertIn("source_inbox_created_by: telegram_auto", out_show)
            self.assertIn("source_inbox_rule_name: time_plus_action", out_show)
            self.assertIn("source_inbox_rule_version: inbox_v1", out_show)
            self.assertIn("source_triage_created_by: manual", out_show)

            with connection_ctx(db_path) as conn:
                ev = conn.execute(
                    """
                    SELECT action, target_type, target_id
                    FROM triage_events
                    WHERE user_id = 1 AND inbox_item_id = 1
                    ORDER BY id DESC
                    LIMIT 1
                    """
                ).fetchone()
                self.assertEqual(ev["action"], "to_anki")
                self.assertEqual(ev["target_type"], "anki")
                self.assertEqual(ev["target_id"], 1)

    def test_anki_archive_idempotent_and_export_only_new_skips_archived(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "life.db"
            out_csv = Path(tmp) / "anki.csv"
            run_with_output(["--db", str(db_path), "anki", "create", "manual", "Q", "A"])

            rc1, out1 = run_with_output(["--db", str(db_path), "anki", "archive", "1"])
            rc2, out2 = run_with_output(["--db", str(db_path), "anki", "archive", "1"])
            self.assertEqual(rc1, 0)
            self.assertEqual(rc2, 0)
            self.assertIn("anki draft archived", out1)
            self.assertIn("anki draft already archived", out2)

            rc_export, out_export = run_with_output(["--db", str(db_path), "anki", "export-csv", str(out_csv), "--only-new"])
            self.assertEqual(rc_export, 0)
            self.assertIn("count=0", out_export)
            lines = out_csv.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 1)

            with connection_ctx(db_path) as conn:
                row = conn.execute("SELECT status, exported_at FROM anki_drafts WHERE id = 1").fetchone()
                self.assertEqual(row["status"], "archived")
                self.assertIsNone(row["exported_at"])

    def test_anki_export_only_new_marks_exported_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "life.db"
            out_csv_1 = Path(tmp) / "anki1.csv"
            out_csv_2 = Path(tmp) / "anki2.csv"
            run_with_output(["--db", str(db_path), "anki", "create", "manual", "Q1", "A1"])
            run_with_output(["--db", str(db_path), "anki", "create", "manual", "Q2", "A2"])

            rc1, out1 = run_with_output(["--db", str(db_path), "anki", "export-csv", str(out_csv_1), "--only-new"])
            rc2, out2 = run_with_output(["--db", str(db_path), "anki", "export-csv", str(out_csv_2), "--only-new"])
            self.assertEqual(rc1, 0)
            self.assertEqual(rc2, 0)
            self.assertIn("count=2", out1)
            self.assertIn("count=0", out2)

            text1 = out_csv_1.read_text(encoding="utf-8")
            text2 = out_csv_2.read_text(encoding="utf-8")
            self.assertIn("Q1", text1)
            self.assertIn("Q2", text1)
            self.assertNotIn("Q1", text2)
            self.assertNotIn("Q2", text2)

            with connection_ctx(db_path) as conn:
                rows = conn.execute(
                    "SELECT status, exported_at FROM anki_drafts WHERE user_id = 1 ORDER BY id ASC"
                ).fetchall()
                self.assertEqual([row["status"] for row in rows], ["exported", "exported"])
                self.assertTrue(all(row["exported_at"] is not None for row in rows))



    def test_anki_update_all_fields_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "life.db"
            run_with_output(["--db", str(db_path), "anki", "create", "manual", "old front", "old back", "--deck-name", "old", "--tags", "t1"])

            rc, out = run_with_output(
                [
                    "--db",
                    str(db_path),
                    "anki",
                    "update",
                    "1",
                    "--front",
                    "new front",
                    "--back",
                    "new back",
                    "--tags",
                    "t2",
                    "--deck",
                    "newdeck",
                ]
            )
            self.assertEqual(rc, 0)
            self.assertIn("anki draft updated", out)

            with connection_ctx(db_path) as conn:
                row = conn.execute(
                    "SELECT front, back, tags, deck_name FROM anki_drafts WHERE id = 1 AND user_id = 1"
                ).fetchone()
                self.assertEqual(row["front"], "new front")
                self.assertEqual(row["back"], "new back")
                self.assertEqual(row["tags"], "t2")
                self.assertEqual(row["deck_name"], "newdeck")

    def test_anki_update_single_field_does_not_overwrite_others(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "life.db"
            run_with_output(["--db", str(db_path), "anki", "create", "manual", "front", "back", "--deck-name", "deck-a", "--tags", "tag-a"])

            rc, out = run_with_output(["--db", str(db_path), "anki", "update", "1", "--front", "front-b"])
            self.assertEqual(rc, 0)
            self.assertIn("anki draft updated", out)

            with connection_ctx(db_path) as conn:
                row = conn.execute(
                    "SELECT front, back, tags, deck_name FROM anki_drafts WHERE id = 1 AND user_id = 1"
                ).fetchone()
                self.assertEqual(row["front"], "front-b")
                self.assertEqual(row["back"], "back")
                self.assertEqual(row["tags"], "tag-a")
                self.assertEqual(row["deck_name"], "deck-a")

    def test_anki_update_archived_draft_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "life.db"
            run_with_output(["--db", str(db_path), "anki", "create", "manual", "front", "back"])
            run_with_output(["--db", str(db_path), "anki", "archive", "1"])

            rc, out = run_with_output(["--db", str(db_path), "anki", "update", "1", "--back", "back-updated"])
            self.assertEqual(rc, 0)
            self.assertIn("anki draft updated", out)

            with connection_ctx(db_path) as conn:
                row = conn.execute(
                    "SELECT status, back FROM anki_drafts WHERE id = 1 AND user_id = 1"
                ).fetchone()
                self.assertEqual(row["status"], "archived")
                self.assertEqual(row["back"], "back-updated")

    def test_anki_update_then_export_csv_uses_new_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "life.db"
            out_csv = Path(tmp) / "anki.csv"
            run_with_output(["--db", str(db_path), "anki", "create", "manual", "front-old", "back-old"])
            run_with_output(["--db", str(db_path), "anki", "update", "1", "--front", "front-new", "--back", "back-new"])

            rc, out = run_with_output(["--db", str(db_path), "anki", "export-csv", str(out_csv), "--only-new"])
            self.assertEqual(rc, 0)
            self.assertIn("count=1", out)

            text = out_csv.read_text(encoding="utf-8")
            self.assertIn("front-new", text)
            self.assertIn("back-new", text)
            self.assertNotIn("front-old", text)
            self.assertNotIn("back-old", text)

    def test_anki_update_not_found_and_no_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "life.db"
            run_with_output(["--db", str(db_path), "init-db"])

            rc1, out1 = run_with_output(["--db", str(db_path), "anki", "update", "999", "--front", "x"])
            self.assertEqual(rc1, 1)
            self.assertIn("anki draft not found", out1)

            run_with_output(["--db", str(db_path), "anki", "create", "manual", "f", "b"])
            rc2, out2 = run_with_output(["--db", str(db_path), "anki", "update", "1"])
            self.assertEqual(rc2, 1)
            self.assertIn("no fields to update", out2)
if __name__ == "__main__":
    unittest.main()
