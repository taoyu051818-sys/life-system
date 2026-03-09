import json
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


def test_unauth_redirect_to_login_core_pages() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "life.db"
        run_cli(["--db", str(db_path), "init-db"])
        client = _build_client(db_path)
        for path in ("/inbox", "/tasks", "/reminders", "/journal", "/anki"):
            resp = client.get(path, follow_redirects=False)
            assert resp.status_code == 302
            assert resp.headers["location"] == "/login"


def test_login_then_access_pages() -> None:
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
        assert client.get("/journal").status_code == 200
        assert client.get("/anki").status_code == 200



def test_base_uses_local_htmx_and_security_headers() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "life.db"
        run_cli(["--db", str(db_path), "init-db"])
        client = _build_client(db_path)
        _login(client)

        page = client.get("/")
        assert page.status_code == 200
        assert '/static/js/htmx.min.js' in page.text
        assert "https://unpkg.com" not in page.text
        assert "Content-Security-Policy" in page.headers
        assert "script-src 'self'" in page.headers["Content-Security-Policy"]
        assert page.headers.get("Cache-Control") == "no-store"

        static_resp = client.get("/static/app.css")
        assert static_resp.status_code == 200
        assert static_resp.headers.get("Cache-Control") == "public, max-age=3600"
def test_quick_journal_activity_write() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "life.db"
        run_cli(["--db", str(db_path), "init-db"])
        client = _build_client(db_path)
        _login(client)
        resp = client.post("/quick-journal/activity", data={"content": "write report"})
        assert resp.status_code == 200
        with connection_ctx(db_path) as conn:
            row = conn.execute(
                "SELECT entry_type, content FROM journal_entries WHERE user_id=1 ORDER BY id DESC LIMIT 1"
            ).fetchone()
            assert row["entry_type"] == "activity"
            assert row["content"] == "write report"


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
            assert row["content"] == "状态签到"
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


def test_journal_history_page_shows_entries() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "life.db"
        run_cli(["--db", str(db_path), "init-db"])
        run_cli(["--db", str(db_path), "journal", "add", "first note", "--type", "activity"])
        run_cli(["--db", str(db_path), "journal", "add", "second note", "--type", "reflection"])
        client = _build_client(db_path)
        _login(client)
        page = client.get("/journal?limit=50")
        assert page.status_code == 200
        assert "first note" in page.text
        assert "second note" in page.text


def test_anki_page_shows_existing_draft() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "life.db"
        run_cli(["--db", str(db_path), "init-db"])
        run_cli(["--db", str(db_path), "anki", "create", "manual", "q1", "a1", "--deck-name", "default", "--tags", "tag1"])
        client = _build_client(db_path)
        _login(client)
        page = client.get("/anki")
        assert page.status_code == 200
        assert "q1" in page.text
        assert "a1" in page.text


def test_anki_import_json_single_success() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "life.db"
        run_cli(["--db", str(db_path), "init-db"])
        client = _build_client(db_path)
        _login(client)
        payload = {
            "front": "photosynthesis definition",
            "back": "process by which green plants convert light energy",
            "tags": ["biology", "plants"],
            "deck": "default",
        }
        resp = client.post("/anki/import-json", data={"raw_json": json.dumps(payload)})
        assert resp.status_code == 200
        with connection_ctx(db_path) as conn:
            row = conn.execute("SELECT front, back, tags, deck_name FROM anki_drafts WHERE user_id=1 ORDER BY id DESC LIMIT 1").fetchone()
            assert row["front"] == payload["front"]
            assert row["back"] == payload["back"]
            assert row["tags"] == "biology,plants"
            assert row["deck_name"] == "default"


def test_anki_import_json_array_success() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "life.db"
        run_cli(["--db", str(db_path), "init-db"])
        client = _build_client(db_path)
        _login(client)
        payload = [
            {"front": "mitosis?", "back": "cell division", "tags": ["biology"], "deck": "default"},
            {"front": "GDP meaning", "back": "Gross Domestic Product", "tags": "economics"},
        ]
        resp = client.post("/anki/import-json", data={"raw_json": json.dumps(payload)})
        assert resp.status_code == 200
        with connection_ctx(db_path) as conn:
            c = conn.execute("SELECT COUNT(*) AS c FROM anki_drafts WHERE user_id=1").fetchone()["c"]
            assert c == 2


def test_anki_import_json_invalid_shows_error_and_rejects_all() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "life.db"
        run_cli(["--db", str(db_path), "init-db"])
        client = _build_client(db_path)
        _login(client)
        payload = [
            {"front": "ok", "back": "ok"},
            {"front": "missing back"},
        ]
        resp = client.post("/anki/import-json", data={"raw_json": json.dumps(payload)})
        assert resp.status_code == 200
        assert "import failed" in resp.text
        with connection_ctx(db_path) as conn:
            c = conn.execute("SELECT COUNT(*) AS c FROM anki_drafts WHERE user_id=1").fetchone()["c"]
            assert c == 0


def test_anki_update_and_archive() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "life.db"
        run_cli(["--db", str(db_path), "init-db"])
        run_cli(["--db", str(db_path), "anki", "create", "manual", "q1", "a1", "--deck-name", "default", "--tags", "tag1"])
        client = _build_client(db_path)
        _login(client)

        up = client.post("/anki/1/update", data={"front": "q1-upd", "back": "a1-upd", "tags": "t2", "deck_name": "deck2"})
        assert up.status_code == 200

        with connection_ctx(db_path) as conn:
            row = conn.execute("SELECT front, back, tags, deck_name, status FROM anki_drafts WHERE id=1").fetchone()
            assert row["front"] == "q1-upd"
            assert row["back"] == "a1-upd"
            assert row["tags"] == "t2"
            assert row["deck_name"] == "deck2"
            assert row["status"] != "archived"

        ar = client.post("/anki/1/archive")
        assert ar.status_code == 200
        with connection_ctx(db_path) as conn:
            status = conn.execute("SELECT status FROM anki_drafts WHERE id=1").fetchone()["status"]
            assert status == "archived"


