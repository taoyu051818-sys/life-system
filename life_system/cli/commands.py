import argparse
from typing import Sequence

from life_system.app.services import LifeSystemService
from life_system.infra.db import connection_ctx, ensure_database, resolve_db_path
from life_system.infra.repositories import UserRepository


VALID_TASK_STATUSES = {"open", "snoozed", "done", "abandoned"}
ABANDON_REASON_PRESETS = {"overwhelm", "wrong_timing", "no_value", "impulse", "blocked"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="life")
    parser.add_argument("--db", default=None, help="SQLite DB path (default: data/life_system.db)")
    parser.add_argument("--user", default="xiaoyu", help="Username scope (default: xiaoyu)")
    subparsers = parser.add_subparsers(dest="entity", required=True)

    subparsers.add_parser("init-db")

    capture = subparsers.add_parser("capture")
    capture.add_argument("content")

    inbox = subparsers.add_parser("inbox")
    inbox_sub = inbox.add_subparsers(dest="action", required=True)
    inbox_capture = inbox_sub.add_parser("capture")
    inbox_capture.add_argument("content")
    inbox_list = inbox_sub.add_parser("list")
    inbox_list.add_argument("--status", default=None)
    inbox_list.add_argument("--limit", type=int, default=50)
    inbox_triage = inbox_sub.add_parser("triage")
    inbox_triage.add_argument("inbox_id", type=int)
    inbox_triage.add_argument("target", choices=["task", "anki", "archive"])

    task = subparsers.add_parser("task")
    task_sub = task.add_subparsers(dest="action", required=True)
    task_create = task_sub.add_parser("create")
    task_create.add_argument("title")
    task_create.add_argument("--notes", default=None)
    task_create.add_argument("--priority", type=int, default=3)
    task_create.add_argument("--due-at", default=None, help="ISO timestamp")
    task_create.add_argument("--inbox-id", type=int, default=None)
    task_list = task_sub.add_parser("list")
    task_list.add_argument("--status", default=None, choices=sorted(VALID_TASK_STATUSES))
    task_list.add_argument("--limit", type=int, default=50)
    task_done = task_sub.add_parser("done")
    task_done.add_argument("task_id", type=int)
    task_snooze = task_sub.add_parser("snooze")
    task_snooze.add_argument("task_id", type=int)
    task_snooze.add_argument("snooze_until", help="ISO timestamp")
    task_abandon = task_sub.add_parser("abandon")
    task_abandon.add_argument("task_id", type=int)
    task_abandon.add_argument("--reason-code", default=None, choices=sorted(ABANDON_REASON_PRESETS))
    task_abandon.add_argument("--reason-text", default=None)
    task_abandon.add_argument("--energy-level", type=int, default=None)

    reminder = subparsers.add_parser("reminder")
    reminder_sub = reminder.add_subparsers(dest="action", required=True)
    reminder_create = reminder_sub.add_parser("create")
    reminder_create.add_argument("task_id", type=int)
    reminder_create.add_argument("remind_at", help="ISO timestamp")
    reminder_create.add_argument("--channel", default="cli")
    reminder_due = reminder_sub.add_parser("due")
    reminder_due.add_argument("--now", default=None, help="ISO timestamp, default utc now")
    reminder_due.add_argument("--limit", type=int, default=50)

    anki = subparsers.add_parser("anki")
    anki_sub = anki.add_subparsers(dest="action", required=True)
    anki_create = anki_sub.add_parser("create")
    anki_create.add_argument("source_type", choices=["task", "inbox", "manual"])
    anki_create.add_argument("front")
    anki_create.add_argument("back")
    anki_create.add_argument("--source-id", type=int, default=None)
    anki_create.add_argument("--deck-name", default="inbox")
    anki_create.add_argument("--tags", default=None)
    anki_list = anki_sub.add_parser("list")
    anki_list.add_argument("--status", default=None)
    anki_list.add_argument("--limit", type=int, default=50)
    anki_export = anki_sub.add_parser("export-csv")
    anki_export.add_argument("path")

    return parser


def run_cli(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    db_path = resolve_db_path(args.db)

    if args.entity == "init-db":
        ensure_database(db_path)
        print(f"database initialized: {db_path}")
        return 0

    ensure_database(db_path)

    with connection_ctx(db_path) as conn:
        user = UserRepository(conn).get_by_username(args.user)
        if user is None:
            print(f"user not found: {args.user}")
            return 1
        service = LifeSystemService(conn, user_id=user["id"], username=user["username"])
        return _dispatch(service, args)


def _dispatch(service: LifeSystemService, args: argparse.Namespace) -> int:
    entity = args.entity
    action = getattr(args, "action", None)

    if entity == "capture":
        item_id = service.capture_inbox(content=args.content, source="cli")
        print(f"inbox captured: id={item_id}")
        return 0

    if entity == "inbox" and action == "capture":
        item_id = service.capture_inbox(content=args.content, source="cli")
        print(f"inbox captured: id={item_id}")
        return 0

    if entity == "inbox" and action == "list":
        items = service.list_inbox(status=args.status, limit=args.limit)
        for item in items:
            print(f"{item['id']}\t{item['status']}\t{item['created_at']}\t{item['content']}")
        return 0

    if entity == "inbox" and action == "triage":
        if args.target == "task":
            task_id = service.triage_inbox_to_task(args.inbox_id)
            if task_id is None:
                print("inbox item not found")
                return 1
            print(f"inbox triaged to task: inbox_id={args.inbox_id} task_id={task_id}")
            return 0
        if args.target == "anki":
            draft_id = service.triage_inbox_to_anki(args.inbox_id)
            if draft_id is None:
                print("inbox item not found")
                return 1
            print(f"inbox triaged to anki: inbox_id={args.inbox_id} draft_id={draft_id}")
            return 0
        ok = service.archive_inbox(args.inbox_id)
        print("inbox archived" if ok else "inbox item not found")
        return 0 if ok else 1

    if entity == "task" and action == "create":
        task_id = service.create_task(
            title=args.title,
            notes=args.notes,
            priority=args.priority,
            due_at=args.due_at,
            inbox_item_id=args.inbox_id,
        )
        if task_id is None:
            print("inbox item not found")
            return 1
        print(f"task created: id={task_id}")
        return 0

    if entity == "task" and action == "list":
        items = service.list_tasks(status=args.status, limit=args.limit)
        for task in items:
            print(
                f"{task['id']}\t{task['status']}\tp{task['priority']}\t{task['due_at']}\t{task['snooze_until']}\t{task['title']}"
            )
        return 0

    if entity == "task" and action == "done":
        ok = service.done_task(task_id=args.task_id)
        print("task done" if ok else "task not found")
        return 0 if ok else 1

    if entity == "task" and action == "snooze":
        ok = service.snooze_task(task_id=args.task_id, snooze_until=args.snooze_until)
        print("task snoozed" if ok else "task not found")
        return 0 if ok else 1

    if entity == "task" and action == "abandon":
        ok = service.abandon_task(
            task_id=args.task_id,
            reason_code=args.reason_code,
            reason_text=args.reason_text,
            energy_level=args.energy_level,
        )
        print("task abandoned" if ok else "task not found")
        return 0 if ok else 1

    if entity == "reminder" and action == "create":
        reminder_id = service.create_reminder(
            task_id=args.task_id,
            remind_at=args.remind_at,
            channel=args.channel,
        )
        if reminder_id is None:
            print("task not found")
            return 1
        print(f"reminder created: id={reminder_id}")
        return 0

    if entity == "reminder" and action == "due":
        items = service.due_reminders(now=args.now, limit=args.limit)
        for item in items:
            print(f"{item['id']}\ttask={item['task_id']}\t{item['remind_at']}\t{item['task_title']}")
        return 0

    if entity == "anki" and action == "create":
        draft_id = service.create_anki_draft(
            source_type=args.source_type,
            source_id=args.source_id,
            deck_name=args.deck_name,
            front=args.front,
            back=args.back,
            tags=args.tags,
        )
        print(f"anki draft created: id={draft_id}")
        return 0

    if entity == "anki" and action == "list":
        items = service.list_anki_drafts(status=args.status, limit=args.limit)
        for item in items:
            print(
                f"{item['id']}\t{item['status']}\t{item['deck_name']}\t{item['source_type']}:{item['source_id']}"
            )
        return 0

    if entity == "anki" and action == "export-csv":
        count = service.export_anki_drafts_csv(args.path)
        print(f"anki drafts exported: count={count} path={args.path}")
        return 0

    parser = build_parser()
    parser.print_help()
    return 1
