import json
import os
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from life_system.app.services import LifeSystemService
from life_system.cli.commands import run_cli
from life_system.infra.db import connection_ctx
from life_system.infra.repositories import UserRepository
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



def _create_anki_share_url(db_path: Path, username: str = "xiaoyu") -> str:
    with connection_ctx(db_path) as conn:
        user = UserRepository(conn).get_by_username(username)
        assert user is not None
        service = LifeSystemService(
            conn=conn,
            user_id=int(user["id"]),
            username=str(user["username"]),
            telegram_chat_id=user.get("telegram_chat_id"),
            reminder_sender=None,
        )
        payload = service.create_anki_review_share_link(base_url="http://testserver")
        return str(payload["url"])

def test_unauth_redirect_to_login_core_pages() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "life.db"
        run_cli(["--db", str(db_path), "init-db"])
        client = _build_client(db_path)
        for path in ("/inbox", "/tasks", "/reminders", "/journal", "/anki", "/anki/review"):
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


def test_anki_page_shows_existing_draft_fields() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "life.db"
        run_cli(["--db", str(db_path), "init-db"])
        run_cli(["--db", str(db_path), "anki", "create", "manual", "q1", "a1", "--deck-name", "default", "--tags", "tag1"])
        client = _build_client(db_path)
        _login(client)
        page = client.get("/anki")
        assert page.status_code == 200
        assert "created_at" in page.text
        assert "deck_name" in page.text
        assert "tags" in page.text
        assert "source_type" in page.text
        assert "status" in page.text
        assert "q1" in page.text


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



def test_anki_review_page_and_rate_flow() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "life.db"
        run_cli(["--db", str(db_path), "init-db"])
        run_cli(["--db", str(db_path), "anki", "create", "manual", "Q1", "A1", "--deck-name", "default"])
        run_cli(["--db", str(db_path), "anki", "activate", "1"])
        client = _build_client(db_path)
        _login(client)

        page = client.get("/anki/review")
        assert page.status_code == 200
        assert "Q1" in page.text
        assert "A1" not in page.text

        reveal = client.post("/anki/review/reveal", data={"deck_name": "", "limit": "50"})
        assert reveal.status_code == 200
        assert "A1" in reveal.text
        assert "again" in reveal.text

        resp = client.post("/anki/review/rate", data={"card_id": "1", "rate": "good", "deck_name": "", "limit": "50"})
        assert resp.status_code == 200
        assert "No due cards right now" in resp.text

        with connection_ctx(db_path) as conn:
            card = conn.execute("SELECT state, reps FROM anki_cards WHERE id=1").fetchone()
            assert card["state"] == "review"
            assert card["reps"] == 1
            ev = conn.execute("SELECT COUNT(*) AS c FROM anki_review_events WHERE card_id=1").fetchone()
            assert ev["c"] == 1



def test_create_draft_does_not_auto_create_card() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "life.db"
        run_cli(["--db", str(db_path), "init-db"])
        run_cli(["--db", str(db_path), "anki", "create", "manual", "q1", "a1", "--deck-name", "default"])
        with connection_ctx(db_path) as conn:
            c = conn.execute("SELECT COUNT(*) AS c FROM anki_cards WHERE user_id=1").fetchone()["c"]
            assert c == 0


def test_web_anki_batch_activate_success_and_duplicate_skip() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "life.db"
        run_cli(["--db", str(db_path), "init-db"])
        run_cli(["--db", str(db_path), "anki", "create", "manual", "q1", "a1", "--deck-name", "default"])
        run_cli(["--db", str(db_path), "anki", "create", "manual", "  q1", "a1", "--deck-name", "default"])
        client = _build_client(db_path)
        _login(client)

        resp = client.post(
            "/anki/batch-activate",
            data={"draft_id": ["1", "2"], "deck_filter": "", "limit": "100"},
        )
        assert resp.status_code == 200
        assert "batch activate: activated=1" in resp.text

        with connection_ctx(db_path) as conn:
            c = conn.execute("SELECT COUNT(*) AS c FROM anki_cards WHERE user_id=1").fetchone()["c"]
            assert c == 1


def test_activate_multi_drafts_creates_multiple_cards() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "life.db"
        run_cli(["--db", str(db_path), "init-db"])
        run_cli(["--db", str(db_path), "anki", "create", "manual", "q1", "a1", "--deck-name", "default"])
        run_cli(["--db", str(db_path), "anki", "create", "manual", "q2", "a2", "--deck-name", "default"])
        run_cli(["--db", str(db_path), "anki", "activate", "1", "2"])
        with connection_ctx(db_path) as conn:
            c = conn.execute("SELECT COUNT(*) AS c FROM anki_cards WHERE user_id=1").fetchone()["c"]
            assert c == 2


def test_anki_page_shows_due_cards_list() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "life.db"
        run_cli(["--db", str(db_path), "init-db"])
        run_cli(["--db", str(db_path), "anki", "create", "manual", "due-q", "due-a", "--deck-name", "default"])
        run_cli(["--db", str(db_path), "anki", "activate", "1"])
        client = _build_client(db_path)
        _login(client)
        page = client.get("/anki")
        assert page.status_code == 200
        assert "Due Cards" in page.text
        assert "due-q" in page.text


def test_web_anki_batch_review_success() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "life.db"
        run_cli(["--db", str(db_path), "init-db"])
        run_cli(["--db", str(db_path), "anki", "create", "manual", "q1", "a1", "--deck-name", "default"])
        run_cli(["--db", str(db_path), "anki", "activate", "1"])
        client = _build_client(db_path)
        _login(client)

        resp = client.post(
            "/anki/batch-review",
            data={"card_id": ["1"], "rating": "good", "deck_filter": "", "limit": "100", "due_limit": "50"},
        )
        assert resp.status_code == 200
        assert "batch review: reviewed=1" in resp.text

        with connection_ctx(db_path) as conn:
            ev = conn.execute("SELECT COUNT(*) AS c FROM anki_review_events WHERE card_id=1").fetchone()["c"]
            assert ev == 1


def test_web_anki_batch_actions_no_selection_friendly_message() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "life.db"
        run_cli(["--db", str(db_path), "init-db"])
        run_cli(["--db", str(db_path), "anki", "create", "manual", "q1", "a1", "--deck-name", "default"])
        client = _build_client(db_path)
        _login(client)

        r1 = client.post("/anki/batch-activate", data={"deck_filter": "", "limit": "100", "due_limit": "50"})
        assert r1.status_code == 200
        assert "no draft selected" in r1.text

        r2 = client.post("/anki/batch-review", data={"rating": "good", "deck_filter": "", "limit": "100", "due_limit": "50"})
        assert r2.status_code == 200
        assert "no due card selected" in r2.text


def test_web_anki_batch_activate_under_deck_filter_only_selected_processed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "life.db"
        run_cli(["--db", str(db_path), "init-db"])
        run_cli(["--db", str(db_path), "anki", "create", "manual", "q-default", "a1", "--deck-name", "default"])
        run_cli(["--db", str(db_path), "anki", "create", "manual", "q-eco", "a2", "--deck-name", "economics"])
        client = _build_client(db_path)
        _login(client)

        # filter page still shows only selected deck rows, then activate selected ids only
        page = client.get("/anki?deck=default&limit=100&due_limit=50")
        assert page.status_code == 200
        assert "q-default" in page.text

        resp = client.post(
            "/anki/batch-activate",
            data={"draft_id": ["1"], "deck_filter": "default", "limit": "100", "due_limit": "50"},
        )
        assert resp.status_code == 200
        with connection_ctx(db_path) as conn:
            rows = conn.execute("SELECT draft_id FROM anki_cards ORDER BY id ASC").fetchall()
            ids = [r[0] for r in rows]
            assert ids == [1]


def test_anki_review_empty_state() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "life.db"
        run_cli(["--db", str(db_path), "init-db"])
        client = _build_client(db_path)
        _login(client)
        page = client.get("/anki/review")
        assert page.status_code == 200
        assert "No due cards right now" in page.text


def test_anki_review_deck_filter() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "life.db"
        run_cli(["--db", str(db_path), "init-db"])
        run_cli(["--db", str(db_path), "anki", "create", "manual", "Q-default", "A1", "--deck-name", "default"])
        run_cli(["--db", str(db_path), "anki", "create", "manual", "Q-eco", "A2", "--deck-name", "economics"])
        run_cli(["--db", str(db_path), "anki", "activate", "1", "2"])
        client = _build_client(db_path)
        _login(client)

        page = client.get("/anki/review?deck_name=economics")
        assert page.status_code == 200
        assert "Q-eco" in page.text
        assert "Q-default" not in page.text



def test_anki_review_share_link_allows_review_without_login() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "life.db"
        run_cli(["--db", str(db_path), "init-db"])
        run_cli(["--db", str(db_path), "anki", "create", "manual", "Q-share", "A-share", "--deck-name", "default"])
        run_cli(["--db", str(db_path), "anki", "activate", "1"])
        client = _build_client(db_path)
        share_url = _create_anki_share_url(db_path)
        share_resp = client.get(share_url, follow_redirects=False)
        assert share_resp.status_code == 303
        assert share_resp.headers["location"] == "/anki/review"
        page = client.get("/anki/review")
        assert page.status_code == 200
        assert "Q-share" in page.text
        no_tasks = client.get("/tasks", follow_redirects=False)
        assert no_tasks.status_code == 302
        assert no_tasks.headers["location"] == "/login"


def test_anki_review_share_invalid_token_denied() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "life.db"
        run_cli(["--db", str(db_path), "init-db"])
        client = _build_client(db_path)
        bad = client.get("/share/anki-review?t=bad-token")
        assert bad.status_code == 400
        assert "invalid or expired share token" in bad.text


def test_anki_review_share_token_single_use() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "life.db"
        run_cli(["--db", str(db_path), "init-db"])
        run_cli(["--db", str(db_path), "anki", "create", "manual", "Q-one", "A-one", "--deck-name", "default"])
        run_cli(["--db", str(db_path), "anki", "activate", "1"])
        client = _build_client(db_path)
        share_url = _create_anki_share_url(db_path)
        first = client.get(share_url, follow_redirects=False)
        assert first.status_code == 303
        second = client.get(share_url)
        assert second.status_code == 400
        assert "invalid or expired share token" in second.text

def test_anki_stats_page_with_data() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "life.db"
        run_cli(["--db", str(db_path), "init-db"])
        run_cli(["--db", str(db_path), "anki", "create", "manual", "Q1", "A1", "--deck-name", "default"])
        run_cli(["--db", str(db_path), "anki", "activate", "1"])
        run_cli(["--db", str(db_path), "anki", "review", "1", "--rate", "good"])
        client = _build_client(db_path)
        _login(client)

        page = client.get("/anki/stats")
        assert page.status_code == 200
        assert "Anki Stats" in page.text
        assert "Total drafts" in page.text
        assert "Rating distribution" in page.text
        assert "default" in page.text


def test_anki_stats_page_empty() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "life.db"
        run_cli(["--db", str(db_path), "init-db"])
        client = _build_client(db_path)
        _login(client)

        page = client.get("/anki/stats")
        assert page.status_code == 200
        assert "Anki Stats" in page.text
        assert "0" in page.text


def test_anki_stats_nav_not_highlight_anki_tab() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "life.db"
        run_cli(["--db", str(db_path), "init-db"])
        client = _build_client(db_path)
        _login(client)

        page = client.get("/anki/stats")
        assert page.status_code == 200
        assert 'href="/anki/stats">Anki Stats</a>' in page.text
        assert '<a class="active" href="/anki">Anki</a>' not in page.text


def test_inbox_to_anki_action_creates_draft() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "life.db"
        run_cli(["--db", str(db_path), "init-db"])
        run_cli(["--db", str(db_path), "--user", "xiaoyu", "capture", "turn into anki"])
        client = _build_client(db_path)
        _login(client)

        resp = client.post("/inbox/1/to-anki", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"].startswith("/inbox?")

        with connection_ctx(db_path) as conn:
            draft = conn.execute("SELECT front, source_type FROM anki_drafts WHERE id=1").fetchone()
            inbox = conn.execute("SELECT status FROM inbox_items WHERE id=1").fetchone()
            assert draft["front"] == "turn into anki"
            assert draft["source_type"] == "inbox"
            assert inbox["status"] == "triaged"


def test_tasks_new_create_and_abandon_flow() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "life.db"
        run_cli(["--db", str(db_path), "init-db"])
        client = _build_client(db_path)
        _login(client)

        page = client.get("/tasks/new")
        assert page.status_code == 200
        assert "Create Task" in page.text

        create_resp = client.post(
            "/tasks",
            data={"title": "web created task", "notes": "n1", "priority": "2"},
            follow_redirects=False,
        )
        assert create_resp.status_code == 303
        assert create_resp.headers["location"].startswith("/tasks/1?")

        abandon_resp = client.post(
            "/tasks/1/abandon",
            data={"reason_code": "overwhelm", "reason": "too much"},
            follow_redirects=False,
        )
        assert abandon_resp.status_code == 303

        with connection_ctx(db_path) as conn:
            task = conn.execute("SELECT status FROM tasks WHERE id=1").fetchone()
            log = conn.execute(
                "SELECT reason_code, reason_text FROM abandonment_logs WHERE task_id=1 ORDER BY id DESC LIMIT 1"
            ).fetchone()
            assert task["status"] == "abandoned"
            assert log["reason_code"] == "overwhelm"
            assert log["reason_text"] == "too much"


def test_task_detail_create_reminder_and_reminder_pages() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "life.db"
        run_cli(["--db", str(db_path), "init-db"])
        run_cli(["--db", str(db_path), "task", "create", "task for reminder"])
        client = _build_client(db_path)
        _login(client)

        detail = client.get("/tasks/1")
        assert detail.status_code == 200
        assert "task for reminder" in detail.text

        create = client.post(
            "/tasks/1/reminders",
            data={"remind_at": "2026-03-08T12:00:00+00:00", "channel": "web"},
            follow_redirects=False,
        )
        assert create.status_code == 303

        reminder_detail = client.get("/reminders/1")
        assert reminder_detail.status_code == 200
        assert "task for reminder" in reminder_detail.text

        history = client.get("/reminders/1/history")
        assert history.status_code == 200
        assert "created" in history.text


def test_reminders_pending_ack_page() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "life.db"
        run_cli(["--db", str(db_path), "init-db"])
        run_cli(["--db", str(db_path), "task", "create", "pending ack task"])
        run_cli(["--db", str(db_path), "reminder", "create", "1", "2026-03-08T00:00:00+00:00"])
        run_cli(["--db", str(db_path), "reminder", "due", "--send", "--now", "2026-03-08T00:00:00+00:00"])
        client = _build_client(db_path)
        _login(client)

        page = client.get("/reminders/pending-ack")
        assert page.status_code == 200
        assert "pending ack task" in page.text


def test_anki_detail_page_show() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "life.db"
        run_cli(["--db", str(db_path), "init-db"])
        run_cli(["--db", str(db_path), "anki", "create", "manual", "Q detail", "A detail", "--deck-name", "default"])
        client = _build_client(db_path)
        _login(client)

        page = client.get("/anki/1")
        assert page.status_code == 200
        assert "Q detail" in page.text
        assert "A detail" in page.text


def test_inbox_review_and_triage_history_pages() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "life.db"
        run_cli(["--db", str(db_path), "init-db"])
        run_cli(["--db", str(db_path), "--user", "xiaoyu", "capture", "review me"])
        run_cli(["--db", str(db_path), "--user", "xiaoyu", "inbox", "triage", "1", "task"])
        client = _build_client(db_path)
        _login(client)

        review_page = client.get("/inbox/review")
        assert review_page.status_code == 200

        history_page = client.get("/inbox/triage-history")
        assert history_page.status_code == 200
        assert "to_task" in history_page.text
