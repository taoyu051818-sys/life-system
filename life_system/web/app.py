from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from life_system.app.services import LifeSystemService
from life_system.infra.db import connection_ctx, ensure_database, resolve_db_path
from life_system.infra.repositories import UserRepository

CST = timezone(timedelta(hours=8), name="Asia/Shanghai")
SESSION_KEY = "web_authed"
SESSION_UNTIL_KEY = "web_auth_until"


def create_app(db_path: str | None = None) -> FastAPI:
    app = FastAPI(title="Life System Web", version="0.4.0")

    current_db_path = resolve_db_path(db_path or os.getenv("LIFE_SYSTEM_DB"))
    ensure_database(current_db_path)

    password = os.getenv("LIFE_WEB_PASSWORD")
    if not password:
        raise RuntimeError("LIFE_WEB_PASSWORD is required for web login")

    session_secret = os.getenv("LIFE_WEB_SESSION_SECRET", "life-web-dev-secret")
    active_username = os.getenv("LIFE_WEB_DEFAULT_USER", "xiaoyu")

    app.add_middleware(SessionMiddleware, secret_key=session_secret, same_site="lax", https_only=False)

    web_dir = Path(__file__).resolve().parent
    templates = Jinja2Templates(directory=str(web_dir / "templates"))
    app.state.templates = templates
    templates.env.filters["bj_time"] = _fmt_bj_time

    app.mount("/static", StaticFiles(directory=str(web_dir / "static")), name="static")

    csp = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'sha256-pgn1TCGZX6O77zDvy0oTODMOxemn0oj0LeCnQTRj7Kg='; "
        "img-src 'self' data:;"
    )

    @app.middleware("http")
    async def add_security_and_cache_headers(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = csp
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "public, max-age=3600"
        else:
            response.headers["Cache-Control"] = "no-store"
        return response

    def _base_ctx(request: Request) -> dict[str, Any]:
        return {"request": request, "active_user": active_username, "logged_in": _is_authenticated(request)}

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request) -> HTMLResponse:
        if _is_authenticated(request):
            return RedirectResponse(url="/", status_code=302)
        ctx = _base_ctx(request)
        ctx.update({"error": None})
        return templates.TemplateResponse(request, "login.html", ctx)

    @app.post("/login", response_class=HTMLResponse)
    async def login_submit(request: Request) -> HTMLResponse:
        form = await _parse_urlencoded_body(request)
        submitted = (form.get("password") or "").strip()
        remember = form.get("remember") == "on"
        if submitted != password:
            ctx = _base_ctx(request)
            ctx.update({"error": "invalid password"})
            return templates.TemplateResponse(request, "login.html", ctx, status_code=401)
        request.session[SESSION_KEY] = True
        lifetime = timedelta(days=30) if remember else timedelta(hours=12)
        request.session[SESSION_UNTIL_KEY] = _to_iso(datetime.now(timezone.utc) + lifetime)
        return RedirectResponse(url="/", status_code=303)

    @app.post("/logout")
    def logout(request: Request) -> RedirectResponse:
        request.session.clear()
        return RedirectResponse(url="/login", status_code=303)

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request) -> HTMLResponse:
        if not _is_authenticated(request):
            return RedirectResponse(url="/login", status_code=302)
        ctx = _base_ctx(request)
        ctx.update({"flash": None})
        return templates.TemplateResponse(request, "index.html", ctx)

    @app.post("/quick-journal/activity", response_class=HTMLResponse)
    async def quick_journal_activity(request: Request) -> HTMLResponse:
        return await _create_quick_journal(request, current_db_path, active_username, "activity", "activity saved")

    @app.post("/quick-journal/reflection", response_class=HTMLResponse)
    async def quick_journal_reflection(request: Request) -> HTMLResponse:
        return await _create_quick_journal(request, current_db_path, active_username, "reflection", "reflection saved")

    @app.post("/quick-journal/win", response_class=HTMLResponse)
    async def quick_journal_win(request: Request) -> HTMLResponse:
        return await _create_quick_journal(request, current_db_path, active_username, "win", "win saved")

    @app.post("/quick-journal/checkin", response_class=HTMLResponse)
    async def quick_journal_checkin(request: Request) -> HTMLResponse:
        if not _is_authenticated(request):
            return RedirectResponse(url="/login", status_code=302)
        form = await _parse_urlencoded_body(request)
        focus_raw = (form.get("focus") or "").strip()
        if not focus_raw.isdigit() or int(focus_raw) < 1 or int(focus_raw) > 5:
            return templates.TemplateResponse(
                request,
                "partials/_quick_journal_panel.html",
                {"request": request, "active_user": active_username, "flash": "focus must be 1-5"},
                status_code=400,
            )
        with connection_ctx(current_db_path) as conn:
            service = _build_user_service(conn, active_username)
            service.add_journal_entry(content="\u72b6\u6001\u7b7e\u5230", entry_type="checkin", focus_level=int(focus_raw))
        return templates.TemplateResponse(
            request,
            "partials/_quick_journal_panel.html",
            {"request": request, "active_user": active_username, "flash": "checkin saved"},
        )

    @app.get("/journal", response_class=HTMLResponse)
    def journal_page(
        request: Request,
        limit: int = Query(50, ge=1, le=500),
        view: str = Query("cards"),
    ) -> HTMLResponse:
        if not _is_authenticated(request):
            return RedirectResponse(url="/login", status_code=302)
        view_mode = "timeline" if view == "timeline" else "cards"
        with connection_ctx(current_db_path) as conn:
            service = _build_user_service(conn, active_username)
            rows = service.list_journal(limit=limit)
        ctx = _base_ctx(request)
        ctx.update({"rows": rows, "limit": limit, "view_mode": view_mode})
        return templates.TemplateResponse(request, "journal.html", ctx)

    @app.get("/inbox", response_class=HTMLResponse)
    def inbox_page(request: Request) -> HTMLResponse:
        if not _is_authenticated(request):
            return RedirectResponse(url="/login", status_code=302)
        with connection_ctx(current_db_path) as conn:
            service = _build_user_service(conn, active_username)
            items = _list_inbox_new_desc(service)
        ctx = _base_ctx(request)
        ctx.update({"items": items, "flash": None})
        return templates.TemplateResponse(request, "inbox.html", ctx)

    @app.post("/inbox/{inbox_id}/to-task", response_class=HTMLResponse)
    def inbox_to_task(request: Request, inbox_id: int) -> HTMLResponse:
        if not _is_authenticated(request):
            return RedirectResponse(url="/login", status_code=302)
        with connection_ctx(current_db_path) as conn:
            service = _build_user_service(conn, active_username)
            triage_status = service.inbox_triage_status(inbox_id)
            if triage_status == "not_found":
                flash = f"inbox not found: {inbox_id}"
            elif triage_status == "already_archived":
                flash = "inbox already archived"
            elif triage_status == "already_triaged":
                flash = "inbox already triaged"
            else:
                task_id = service.triage_inbox_to_task(inbox_id)
                flash = f"task created from inbox={inbox_id}, task={task_id}" if task_id is not None else "task create failed"
            items = _list_inbox_new_desc(service)
        return templates.TemplateResponse(request, "partials/_inbox_panel.html", {"request": request, "active_user": active_username, "items": items, "flash": flash})

    @app.post("/inbox/{inbox_id}/archive", response_class=HTMLResponse)
    def inbox_archive(request: Request, inbox_id: int) -> HTMLResponse:
        if not _is_authenticated(request):
            return RedirectResponse(url="/login", status_code=302)
        with connection_ctx(current_db_path) as conn:
            service = _build_user_service(conn, active_username)
            status = service.archive_inbox(inbox_id)
            if status == "archived":
                flash = f"inbox archived: {inbox_id}"
            elif status == "already_archived":
                flash = "inbox already archived"
            elif status == "already_triaged":
                flash = "inbox already triaged"
            else:
                flash = f"inbox not found: {inbox_id}"
            items = _list_inbox_new_desc(service)
        return templates.TemplateResponse(request, "partials/_inbox_panel.html", {"request": request, "active_user": active_username, "items": items, "flash": flash})

    @app.post("/inbox/{inbox_id}/keep", response_class=HTMLResponse)
    def inbox_keep(request: Request, inbox_id: int) -> HTMLResponse:
        if not _is_authenticated(request):
            return RedirectResponse(url="/login", status_code=302)
        with connection_ctx(current_db_path) as conn:
            service = _build_user_service(conn, active_username)
            item = next((x for x in service.list_inbox(status="new", limit=200) if int(x["id"]) == inbox_id), None)
            flash = "keep in inbox" if item is not None else f"inbox not found: {inbox_id}"
            items = _list_inbox_new_desc(service)
        return templates.TemplateResponse(request, "partials/_inbox_panel.html", {"request": request, "active_user": active_username, "items": items, "flash": flash})

    @app.get("/inbox/{inbox_id}/history", response_class=HTMLResponse)
    def inbox_history(request: Request, inbox_id: int) -> HTMLResponse:
        if not _is_authenticated(request):
            return RedirectResponse(url="/login", status_code=302)
        with connection_ctx(current_db_path) as conn:
            service = _build_user_service(conn, active_username)
            rows = service.inbox_history(inbox_id) or []
        ctx = _base_ctx(request)
        ctx.update({"rows": rows, "inbox_id": inbox_id})
        return templates.TemplateResponse(request, "partials/_inbox_history.html", ctx)

    @app.get("/tasks", response_class=HTMLResponse)
    def tasks_page(request: Request) -> HTMLResponse:
        if not _is_authenticated(request):
            return RedirectResponse(url="/login", status_code=302)
        with connection_ctx(current_db_path) as conn:
            service = _build_user_service(conn, active_username)
            tasks = service.list_tasks(limit=200)
        ctx = _base_ctx(request)
        ctx.update({"tasks": tasks, "flash": None})
        return templates.TemplateResponse(request, "tasks.html", ctx)

    @app.get("/tasks/{task_id}", response_class=HTMLResponse)
    def task_detail(request: Request, task_id: int) -> HTMLResponse:
        if not _is_authenticated(request):
            return RedirectResponse(url="/login", status_code=302)
        with connection_ctx(current_db_path) as conn:
            service = _build_user_service(conn, active_username)
            task = service.get_task_detail(task_id)
        return templates.TemplateResponse(request, "partials/_task_detail.html", {"request": request, "task": task})

    @app.post("/tasks/{task_id}/done", response_class=HTMLResponse)
    def task_done(request: Request, task_id: int) -> HTMLResponse:
        if not _is_authenticated(request):
            return RedirectResponse(url="/login", status_code=302)
        with connection_ctx(current_db_path) as conn:
            service = _build_user_service(conn, active_username)
            ok = service.done_task(task_id)
            flash = f"task done: {task_id}" if ok else f"task not found: {task_id}"
            tasks = service.list_tasks(limit=200)
        return templates.TemplateResponse(request, "partials/_tasks_panel.html", {"request": request, "tasks": tasks, "flash": flash})

    @app.post("/tasks/{task_id}/snooze", response_class=HTMLResponse)
    async def task_snooze(request: Request, task_id: int) -> HTMLResponse:
        if not _is_authenticated(request):
            return RedirectResponse(url="/login", status_code=302)
        form = await _parse_urlencoded_body(request)
        snooze_until = (form.get("snooze_until") or "").strip()
        with connection_ctx(current_db_path) as conn:
            service = _build_user_service(conn, active_username)
            if not _is_iso_aware(snooze_until):
                tasks = service.list_tasks(limit=200)
                return templates.TemplateResponse(request, "partials/_tasks_panel.html", {"request": request, "tasks": tasks, "flash": "invalid ISO datetime"}, status_code=400)
            ok = service.snooze_task(task_id, snooze_until)
            flash = f"task snoozed: {task_id}" if ok else f"task not found: {task_id}"
            tasks = service.list_tasks(limit=200)
        return templates.TemplateResponse(request, "partials/_tasks_panel.html", {"request": request, "tasks": tasks, "flash": flash})

    @app.get("/reminders", response_class=HTMLResponse)
    def reminders_page(request: Request) -> HTMLResponse:
        if not _is_authenticated(request):
            return RedirectResponse(url="/login", status_code=302)
        with connection_ctx(current_db_path) as conn:
            service = _build_user_service(conn, active_username)
            reminders = service.list_reminders(limit=200)
        ctx = _base_ctx(request)
        ctx.update({"reminders": reminders, "flash": None})
        return templates.TemplateResponse(request, "reminders.html", ctx)

    @app.post("/reminders/{reminder_id}/ack", response_class=HTMLResponse)
    def reminder_ack(request: Request, reminder_id: int) -> HTMLResponse:
        if not _is_authenticated(request):
            return RedirectResponse(url="/login", status_code=302)
        with connection_ctx(current_db_path) as conn:
            service = _build_user_service(conn, active_username)
            status = service.ack_reminder(reminder_id, acked_via="web")
            reminders = service.list_reminders(limit=200)
        return templates.TemplateResponse(request, "partials/_reminders_panel.html", {"request": request, "reminders": reminders, "flash": f"ack: {status}"})

    @app.post("/reminders/{reminder_id}/skip", response_class=HTMLResponse)
    def reminder_skip(request: Request, reminder_id: int) -> HTMLResponse:
        if not _is_authenticated(request):
            return RedirectResponse(url="/login", status_code=302)
        with connection_ctx(current_db_path) as conn:
            service = _build_user_service(conn, active_username)
            status = service.skip_reminder(reminder_id, reason="web_skip")
            reminders = service.list_reminders(limit=200)
        return templates.TemplateResponse(request, "partials/_reminders_panel.html", {"request": request, "reminders": reminders, "flash": f"skip: {status}"})

    @app.post("/reminders/{reminder_id}/snooze", response_class=HTMLResponse)
    async def reminder_snooze(request: Request, reminder_id: int) -> HTMLResponse:
        if not _is_authenticated(request):
            return RedirectResponse(url="/login", status_code=302)
        form = await _parse_urlencoded_body(request)
        remind_at = (form.get("remind_at") or "").strip()
        with connection_ctx(current_db_path) as conn:
            service = _build_user_service(conn, active_username)
            if not _is_iso_aware(remind_at):
                reminders = service.list_reminders(limit=200)
                return templates.TemplateResponse(request, "partials/_reminders_panel.html", {"request": request, "reminders": reminders, "flash": "invalid ISO datetime"}, status_code=400)
            status = service.snooze_reminder(reminder_id, remind_at)
            reminders = service.list_reminders(limit=200)
        return templates.TemplateResponse(request, "partials/_reminders_panel.html", {"request": request, "reminders": reminders, "flash": f"snooze: {status}"})

    @app.get("/anki", response_class=HTMLResponse)
    def anki_page(
        request: Request,
        limit: int = Query(100, ge=1, le=500),
        due_limit: int = Query(50, ge=1, le=500),
        deck: str | None = Query(None),
        draft_select: str | None = Query(None),
        due_select: str | None = Query(None),
    ) -> HTMLResponse:
        if not _is_authenticated(request):
            return RedirectResponse(url="/login", status_code=302)
        deck_filter = _none_if_blank(deck)
        draft_select_all = (draft_select == "all")
        due_select_all = (due_select == "all")
        with connection_ctx(current_db_path) as conn:
            service = _build_user_service(conn, active_username)
            drafts = service.list_anki_drafts(limit=limit, deck_name=deck_filter)
            due_cards = service.list_due_anki_cards(limit=due_limit)
            deck_options = service.list_anki_decks()
        ctx = _base_ctx(request)
        ctx.update(
            {
                "drafts": drafts,
                "due_cards": due_cards,
                "flash": None,
                "import_errors": [],
                "import_json": "",
                "limit": limit,
                "due_limit": due_limit,
                "deck_filter": deck_filter,
                "deck_options": deck_options,
                "draft_select_all": draft_select_all,
                "due_select_all": due_select_all,
            }
        )
        return templates.TemplateResponse(request, "anki.html", ctx)

    def _anki_panel_response(
        request: Request,
        service: LifeSystemService,
        *,
        flash: str,
        import_errors: list[dict[str, Any]] | None = None,
        import_json: str = "",
        deck_filter: str | None = None,
        limit: int = 100,
        due_limit: int = 50,
        draft_select_all: bool = False,
        due_select_all: bool = False,
    ) -> HTMLResponse:
        drafts = service.list_anki_drafts(limit=limit, deck_name=deck_filter)
        due_cards = service.list_due_anki_cards(limit=due_limit)
        deck_options = service.list_anki_decks()
        return templates.TemplateResponse(
            request,
            "partials/_anki_panel.html",
            {
                "request": request,
                "active_user": active_username,
                "drafts": drafts,
                "due_cards": due_cards,
                "flash": flash,
                "import_errors": import_errors or [],
                "import_json": import_json,
                "deck_filter": deck_filter,
                "deck_options": deck_options,
                "limit": limit,
                "due_limit": due_limit,
                "draft_select_all": draft_select_all,
                "due_select_all": due_select_all,
            },
        )

    @app.post("/anki/{draft_id}/update", response_class=HTMLResponse)
    async def anki_update(request: Request, draft_id: int) -> HTMLResponse:
        if not _is_authenticated(request):
            return RedirectResponse(url="/login", status_code=302)
        form = await _parse_urlencoded_body(request)
        front = _none_if_blank(form.get("front"))
        back = _none_if_blank(form.get("back"))
        tags = _none_if_blank(form.get("tags"))
        deck = _none_if_blank(form.get("deck_name"))
        with connection_ctx(current_db_path) as conn:
            service = _build_user_service(conn, active_username)
            status = service.update_anki_draft(draft_id, front=front, back=back, tags=tags, deck_name=deck)
            return _anki_panel_response(request, service, flash=f"update: {status}")

    @app.post("/anki/{draft_id}/archive", response_class=HTMLResponse)
    def anki_archive(request: Request, draft_id: int) -> HTMLResponse:
        if not _is_authenticated(request):
            return RedirectResponse(url="/login", status_code=302)
        with connection_ctx(current_db_path) as conn:
            service = _build_user_service(conn, active_username)
            status = service.archive_anki_draft(draft_id)
            return _anki_panel_response(request, service, flash=f"archive: {status}")

    @app.post("/anki/import-json", response_class=HTMLResponse)
    async def anki_import_json(request: Request) -> HTMLResponse:
        if not _is_authenticated(request):
            return RedirectResponse(url="/login", status_code=302)
        form = await _parse_urlencoded_body(request)
        raw_json = (form.get("raw_json") or "").strip()
        with connection_ctx(current_db_path) as conn:
            service = _build_user_service(conn, active_username)
            result = service.import_anki_json(raw_json)
            if result["ok"]:
                return _anki_panel_response(request, service, flash=f"import success: {result['created']}")
            return _anki_panel_response(
                request,
                service,
                flash=f"import failed: {len(result['errors'])}",
                import_errors=result["errors"],
                import_json=raw_json,
            )

    @app.post("/anki/batch-activate", response_class=HTMLResponse)
    @app.post("/anki/activate", response_class=HTMLResponse)
    async def anki_batch_activate(request: Request) -> HTMLResponse:
        if not _is_authenticated(request):
            return RedirectResponse(url="/login", status_code=302)
        body = await request.body()
        parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True)
        deck_filter = _none_if_blank((parsed.get("deck_filter") or [""])[0])
        try:
            limit = int((parsed.get("limit") or ["100"])[0] or "100")
        except ValueError:
            limit = 100
        try:
            due_limit = int((parsed.get("due_limit") or ["50"])[0] or "50")
        except ValueError:
            due_limit = 50
        draft_select_all = (parsed.get("draft_select_mode") or [""])[0] == "all"
        due_select_all = (parsed.get("due_select_mode") or [""])[0] == "all"
        draft_ids: list[int] = []
        for raw in parsed.get("draft_id", []):
            token = raw.strip()
            if not token:
                continue
            try:
                draft_ids.append(int(token))
            except ValueError:
                continue

        with connection_ctx(current_db_path) as conn:
            service = _build_user_service(conn, active_username)
            if not draft_ids:
                return _anki_panel_response(
                    request,
                    service,
                    flash="batch activate: no draft selected",
                    deck_filter=deck_filter,
                    limit=limit,
                    due_limit=due_limit,
                    draft_select_all=draft_select_all,
                    due_select_all=due_select_all,
                )
            result = service.activate_anki_drafts(draft_ids=draft_ids)
            return _anki_panel_response(
                request,
                service,
                flash=(
                    f"batch activate: activated={result['activated_count']} deduped={result['deduped_count']} "
                    f"skipped={result['skipped_count']} failed={result['failed_count']}"
                ),
                deck_filter=deck_filter,
                limit=limit,
                due_limit=due_limit,
                draft_select_all=draft_select_all,
                due_select_all=due_select_all,
            )

    @app.post("/anki/batch-review", response_class=HTMLResponse)
    async def anki_batch_review(request: Request) -> HTMLResponse:
        if not _is_authenticated(request):
            return RedirectResponse(url="/login", status_code=302)
        body = await request.body()
        parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True)
        deck_filter = _none_if_blank((parsed.get("deck_filter") or [""])[0])
        try:
            limit = int((parsed.get("limit") or ["100"])[0] or "100")
        except ValueError:
            limit = 100
        try:
            due_limit = int((parsed.get("due_limit") or ["50"])[0] or "50")
        except ValueError:
            due_limit = 50
        draft_select_all = (parsed.get("draft_select_mode") or [""])[0] == "all"
        due_select_all = (parsed.get("due_select_mode") or [""])[0] == "all"
        rating = ((parsed.get("rating") or ["good"])[0] or "good").strip().lower()
        card_ids: list[int] = []
        for raw in parsed.get("card_id", []):
            token = raw.strip()
            if not token:
                continue
            try:
                card_ids.append(int(token))
            except ValueError:
                continue

        with connection_ctx(current_db_path) as conn:
            service = _build_user_service(conn, active_username)
            if not card_ids:
                return _anki_panel_response(
                    request,
                    service,
                    flash="batch review: no due card selected",
                    deck_filter=deck_filter,
                    limit=limit,
                    due_limit=due_limit,
                    draft_select_all=draft_select_all,
                    due_select_all=due_select_all,
                )
            result = service.review_anki_cards(card_ids=card_ids, rating=rating)
            return _anki_panel_response(
                request,
                service,
                flash=(
                    f"batch review: reviewed={result['reviewed_count']} "
                    f"skipped={result['skipped_count']} failed={result['failed_count']}"
                ),
                deck_filter=deck_filter,
                limit=limit,
                due_limit=due_limit,
                draft_select_all=draft_select_all,
                due_select_all=due_select_all,
            )


    @app.get("/anki/review", response_class=HTMLResponse)
    def anki_review_page(
        request: Request,
        deck_name: str | None = Query(None),
        limit: int = Query(50, ge=1, le=200),
    ) -> HTMLResponse:
        if not _is_authenticated(request):
            return RedirectResponse(url="/login", status_code=302)
        deck_filter = _none_if_blank(deck_name)
        with connection_ctx(current_db_path) as conn:
            service = _build_user_service(conn, active_username)
            due_cards = service.list_due_anki_cards(limit=limit, deck_name=deck_filter)
            deck_options = service.list_anki_decks()
        card = due_cards[0] if due_cards else None
        ctx = _base_ctx(request)
        ctx.update(
            {
                "card": card,
                "due_count": len(due_cards),
                "total_due": len(due_cards),
                "revealed": False,
                "flash": None,
                "deck_filter": deck_filter,
                "deck_options": deck_options,
                "limit": limit,
                "session_done": False,
            }
        )
        return templates.TemplateResponse(request, "anki_review.html", ctx)

    @app.post("/anki/review/reveal", response_class=HTMLResponse)
    async def anki_review_reveal(request: Request) -> HTMLResponse:
        if not _is_authenticated(request):
            return RedirectResponse(url="/login", status_code=302)
        form = await _parse_urlencoded_body(request)
        deck_filter = _none_if_blank(form.get("deck_name"))
        try:
            limit = int(form.get("limit") or "50")
        except ValueError:
            limit = 50
        with connection_ctx(current_db_path) as conn:
            service = _build_user_service(conn, active_username)
            due_cards = service.list_due_anki_cards(limit=limit, deck_name=deck_filter)
        card = due_cards[0] if due_cards else None
        return templates.TemplateResponse(
            request,
            "partials/_anki_review_session_panel.html",
            {
                "request": request,
                "active_user": active_username,
                "card": card,
                "due_count": len(due_cards),
                "total_due": len(due_cards),
                "revealed": True,
                "flash": None,
                "deck_filter": deck_filter,
                "limit": limit,
                "session_done": card is None,
            },
        )

    @app.post("/anki/review/rate", response_class=HTMLResponse)
    async def anki_review_rate(request: Request) -> HTMLResponse:
        if not _is_authenticated(request):
            return RedirectResponse(url="/login", status_code=302)
        form = await _parse_urlencoded_body(request)
        rating = (form.get("rate") or "").strip().lower()
        deck_filter = _none_if_blank(form.get("deck_name"))
        try:
            limit = int(form.get("limit") or "50")
        except ValueError:
            limit = 50
        try:
            card_id = int(form.get("card_id") or "0")
        except ValueError:
            card_id = 0

        flash = None
        with connection_ctx(current_db_path) as conn:
            service = _build_user_service(conn, active_username)
            if card_id <= 0:
                flash = "invalid card id"
            else:
                try:
                    updated = service.review_anki_card(card_id=card_id, rating=rating)
                except ValueError:
                    updated = None
                    flash = "invalid rating"
                if updated is None and flash is None:
                    flash = "anki card not found"
            due_cards = service.list_due_anki_cards(limit=limit, deck_name=deck_filter)
        card = due_cards[0] if due_cards else None
        return templates.TemplateResponse(
            request,
            "partials/_anki_review_session_panel.html",
            {
                "request": request,
                "active_user": active_username,
                "card": card,
                "due_count": len(due_cards),
                "total_due": len(due_cards),
                "revealed": False,
                "flash": flash or f"rated: {rating}",
                "deck_filter": deck_filter,
                "limit": limit,
                "session_done": card is None,
            },
        )

    @app.get("/anki/stats", response_class=HTMLResponse)
    def anki_stats_page(request: Request) -> HTMLResponse:
        if not _is_authenticated(request):
            return RedirectResponse(url="/login", status_code=302)
        with connection_ctx(current_db_path) as conn:
            service = _build_user_service(conn, active_username)
            stats = service.build_anki_stats()
        ctx = _base_ctx(request)
        ctx.update({"stats": stats})
        return templates.TemplateResponse(request, "anki_stats.html", ctx)

    return app


async def _create_quick_journal(request: Request, db_path: Path, username: str, entry_type: str, ok_text: str) -> HTMLResponse:
    if not _is_authenticated(request):
        return RedirectResponse(url="/login", status_code=302)
    form = await _parse_urlencoded_body(request)
    content = (form.get("content") or "").strip()
    if not content:
        return templates_for(request).TemplateResponse(request, "partials/_quick_journal_panel.html", {"request": request, "active_user": username, "flash": "empty content"}, status_code=400)
    with connection_ctx(db_path) as conn:
        service = _build_user_service(conn, username)
        service.add_journal_entry(content=content, entry_type=entry_type)
    return templates_for(request).TemplateResponse(request, "partials/_quick_journal_panel.html", {"request": request, "active_user": username, "flash": ok_text})


def templates_for(request: Request) -> Jinja2Templates:
    return request.app.state.templates  # type: ignore[return-value]


async def _parse_urlencoded_body(request: Request) -> dict[str, str]:
    body = await request.body()
    parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True)
    return {k: (v[0] if v else "") for k, v in parsed.items()}


def _list_inbox_new_desc(service: LifeSystemService) -> list[dict[str, Any]]:
    rows = service.list_inbox(status="new", limit=200)
    return sorted(rows, key=lambda x: str(x.get("created_at") or ""), reverse=True)


def _build_user_service(conn: Any, username: str) -> LifeSystemService:
    user_repo = UserRepository(conn)
    user = user_repo.get_by_username(username)
    if user is None:
        raise HTTPException(status_code=404, detail=f"user not found: {username}")
    return LifeSystemService(conn=conn, user_id=int(user["id"]), username=str(user["username"]), telegram_chat_id=user.get("telegram_chat_id"), reminder_sender=None)


def _fmt_bj_time(value: str | None) -> str:
    if not value:
        return "-"
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return str(value)
    return dt.astimezone(CST).strftime("%Y-%m-%d %H:%M")


def _is_authenticated(request: Request) -> bool:
    session = request.session
    if not session.get(SESSION_KEY):
        return False
    until = session.get(SESSION_UNTIL_KEY)
    if not until:
        return False
    try:
        return datetime.fromisoformat(str(until).replace("Z", "+00:00")) > datetime.now(timezone.utc)
    except ValueError:
        return False


def _to_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _is_iso_aware(value: str) -> bool:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return dt.tzinfo is not None


def _none_if_blank(value: str | None) -> str | None:
    if value is None:
        return None
    out = value.strip()
    return out if out else None






