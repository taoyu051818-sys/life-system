import os
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from life_system.cli.commands import run_cli
from life_system.infra.db import connection_ctx
from life_system.web.app import create_app


def _build_client(db_path: Path) -> TestClient:
    os.environ["LIFE_WEB_PASSWORD"] = "test-pass"
    os.environ["LIFE_WEB_SESSION_SECRET"] = "test-secret"
    os.environ["LIFE_WEB_DEFAULT_USER"] = "xiaoyu"
    app = create_app(str(db_path))
    return TestClient(app)


def _login(client: TestClient) -> None:
    resp = client.post("/login", data={"password": "test-pass"})
    assert resp.status_code in (200, 303)


def test_unauth_redirect_to_login_inbox_tasks_reminders() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "life.db"
        run_cli(["--db", str(db_path), "init-db"])
        client = _build_client(db_path)
        for path in ("/inbox", "/tasks", "/reminders"):
            resp = client.get(path, follow_redirects=False)
            assert resp.status_code == 302
            assert resp.headers["location"] == "/login"


def test_login_then_access_inbox_tasks_reminders() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "life.db"
        run_cli(["--db", str(db_path), "init-db"])
        run_cli(["--db", str(db_path), "--user", "xiaoyu", "capture", "web inbox item"])
        run_cli(["--db", str(db_path), "--user", "xiaoyu", "task", "create", "task1"])
        run_cli(["--db", str(db_path), "--user", "xiaoyu", "reminder", "create", "1", "2026-03-08T12:00:00+00:00"])
        client = _build_client(db_path)
        _login(client)
        assert client.get("/inbox").status_code == 200
        assert client.get("/tasks").status_code == 200
        assert client.get("/reminders").status_code == 200


def test_quick_journal_activity_write() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "life.db"
        run_cli(["--db", str(db_path), "init-db"])
        client = _build_client(db_path)
        _login(client)
        resp = client.post("/quick-journal/activity", data={"content": "写周报"})
        assert resp.status_code == 200
        with connection_ctx(db_path) as conn:
            row = conn.execute(
                "SELECT entry_type, content FROM journal_entries WHERE user_id=1 ORDER BY id DESC LIMIT 1"
            ).fetchone()
            assert row["entry_type"] == "activity"
            assert row["content"] == "写周报"


def test_quick_journal_focus_checkin_write() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "life.db"
        run_cli(["--db", str(db_path), "init-db"])
        client = _build_client(db_path)
        _login(client)
        resp = client.post("/quick-journal/checkin", data={"focus": "4"})
        assert resp.status_code == 200
        with connection_ctx(db_path) as conn:
            row = conn.execute(
                "SELECT entry_type, content, focus_level, energy_level, mood_level FROM journal_entries WHERE user_id=1 ORDER BY id DESC LIMIT 1"
            ).fetchone()
            assert row["entry_type"] == "checkin"
            assert row["content"] == "\u72b6\u6001\u7b7e\u5230"
            assert row["focus_level"] == 4
            assert row["energy_level"] is None
            assert row["mood_level"] is None


def test_inbox_keep_does_not_change_status() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "life.db"
        run_cli(["--db", str(db_path), "init-db"])
        run_cli(["--db", str(db_path), "--user", "xiaoyu", "capture", "keep me"])
        client = _build_client(db_path)
        _login(client)
        resp = client.post("/inbox/1/keep")
        assert resp.status_code == 200
        with connection_ctx(db_path) as conn:
            inbox = conn.execute("SELECT status FROM inbox_items WHERE id=1").fetchone()
            events = conn.execute("SELECT COUNT(*) AS c FROM triage_events WHERE inbox_item_id=1").fetchone()
            assert inbox["status"] == "new"
            assert events["c"] == 0


def test_reminder_ack_action_success() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "life.db"
        run_cli(["--db", str(db_path), "init-db"])
        run_cli(["--db", str(db_path), "--user", "xiaoyu", "task", "create", "t1"])
        run_cli(["--db", str(db_path), "--user", "xiaoyu", "reminder", "create", "1", "2026-03-08T12:00:00+00:00"])
        client = _build_client(db_path)
        _login(client)
        resp = client.post("/reminders/1/ack")
        assert resp.status_code == 200
        with connection_ctx(db_path) as conn:
            row = conn.execute("SELECT status, acked_via FROM reminders WHERE id=1").fetchone()
            assert row["status"] == "acknowledged"
            assert row["acked_via"] == "web"


def test_beijing_time_render_in_reminders() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "life.db"
        run_cli(["--db", str(db_path), "init-db"])
        run_cli(["--db", str(db_path), "--user", "xiaoyu", "task", "create", "t1"])
        run_cli(["--db", str(db_path), "--user", "xiaoyu", "reminder", "create", "1", "2026-03-08T12:00:00+00:00"])
        client = _build_client(db_path)
        _login(client)
        page = client.get("/reminders")
        assert page.status_code == 200
        assert "2026-03-08 20:00" in page.text
