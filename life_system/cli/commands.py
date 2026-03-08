import argparse
import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Sequence

from life_system.app.services import LifeSystemService
from life_system.app.telegram_polling import TelegramPollingService
from life_system.infra.db import connection_ctx, ensure_database, now_utc_iso, resolve_db_path
from life_system.infra.repositories import UserRepository
from life_system.infra.telegram_sender import TelegramReminderSender


VALID_TASK_STATUSES = {"open", "snoozed", "done", "abandoned"}
ABANDON_REASON_PRESETS = {"overwhelm", "wrong_timing", "no_value", "impulse", "blocked"}
VALID_JOURNAL_TYPES = {"activity", "reflection", "win", "checkin"}
ISO_8601_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:[+-]\d{2}:\d{2}|Z)$")
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CST = timezone(timedelta(hours=8), name="CST")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="life", description="Life System CLI")
    parser.add_argument("--db", default=None, help="SQLite DB path (default: data/life_system.db)")
    parser.add_argument("--user", default="xiaoyu", help="Username scope (default: xiaoyu)")
    subparsers = parser.add_subparsers(dest="entity", required=True)

    subparsers.add_parser("init-db")

    user = subparsers.add_parser("user")
    user_sub = user.add_subparsers(dest="action", required=True)
    user_sub.add_parser("list")
    user_add = user_sub.add_parser("add")
    user_add.add_argument("username")
    user_add.add_argument("--display-name", default=None)
    user_set_tg = user_sub.add_parser("set-telegram")
    user_set_tg.add_argument("username")
    user_set_tg.add_argument("chat_id")
    user_clear_tg = user_sub.add_parser("clear-telegram")
    user_clear_tg.add_argument("username")

    capture = subparsers.add_parser("capture")
    capture.add_argument("content")

    inbox = subparsers.add_parser("inbox", help="Capture and triage inbox items")
    inbox_sub = inbox.add_subparsers(dest="action", required=True)
    inbox_capture = inbox_sub.add_parser("capture")
    inbox_capture.add_argument("content")
    inbox_list = inbox_sub.add_parser("list")
    status_group = inbox_list.add_mutually_exclusive_group()
    status_group.add_argument("--status", default=None)
    status_group.add_argument("--all", action="store_true")
    inbox_list.add_argument("--limit", type=int, default=50)
    inbox_triage = inbox_sub.add_parser("triage", help="Route inbox item to task/anki/archive")
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

    reminder = subparsers.add_parser("reminder", help="Reminder delivery and acknowledgement loop")
    reminder_sub = reminder.add_subparsers(dest="action", required=True)
    reminder_create = reminder_sub.add_parser("create")
    reminder_create.add_argument("task_id", type=int)
    reminder_create.add_argument("remind_at", help="ISO timestamp")
    reminder_create.add_argument("--channel", default="cli")
    reminder_due = reminder_sub.add_parser("due", help="Show due reminders; use --send to mark sent/retried")
    reminder_due.add_argument("--send", action="store_true")
    reminder_due.add_argument("--now", default=None, help="ISO timestamp, default utc now")
    reminder_due.add_argument("--limit", type=int, default=50)
    reminder_pending_ack = reminder_sub.add_parser("pending-ack", help="List reminders waiting for acknowledgement")
    reminder_pending_ack.add_argument("--limit", type=int, default=50)
    reminder_ack = reminder_sub.add_parser("ack", help="Acknowledge a reminder")
    reminder_ack.add_argument("reminder_id", type=int)
    reminder_snooze = reminder_sub.add_parser("snooze", help="Snooze reminder to a new datetime")
    reminder_snooze.add_argument("reminder_id", type=int)
    reminder_snooze.add_argument("remind_at", help="ISO timestamp")
    reminder_skip = reminder_sub.add_parser("skip", help="Skip reminder")
    reminder_skip.add_argument("reminder_id", type=int)
    reminder_skip.add_argument("--reason", default=None)
    reminder_show = reminder_sub.add_parser("show", help="Show reminder details")
    reminder_show.add_argument("reminder_id", type=int)
    reminder_history = reminder_sub.add_parser("history", help="Show reminder event history")
    reminder_history.add_argument("reminder_id", type=int)

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

    journal = subparsers.add_parser("journal", help="Record activity/reflection/wins/checkins")
    journal_sub = journal.add_subparsers(dest="action", required=True)
    journal_add = journal_sub.add_parser("add", help="Add a journal entry")
    journal_add.add_argument("content")
    journal_add.add_argument("--type", dest="entry_type", required=True, choices=sorted(VALID_JOURNAL_TYPES))
    journal_add.add_argument("--task-id", type=int, default=None)
    journal_add.add_argument("--inbox-id", type=int, default=None)
    journal_add.add_argument("--energy", type=int, default=None)
    journal_add.add_argument("--focus", type=int, default=None)
    journal_add.add_argument("--mood", type=int, default=None)
    journal_add.add_argument("--tags", default=None)
    journal_list = journal_sub.add_parser("list", help="List recent journal entries")
    journal_list.add_argument("--limit", type=int, default=50)
    journal_list.add_argument("--type", dest="entry_type", default=None, choices=sorted(VALID_JOURNAL_TYPES))
    journal_today = journal_sub.add_parser("today", help="List today's journal entries")
    journal_today.add_argument("--limit", type=int, default=50)
    journal_today.add_argument("--type", dest="entry_type", default=None, choices=sorted(VALID_JOURNAL_TYPES))

    summary = subparsers.add_parser("summary", help="Daily evidence-first summary")
    summary_sub = summary.add_subparsers(dest="action", required=True)
    summary_sub.add_parser("today", help="Show today's summary")
    summary_day = summary_sub.add_parser("day", help="Show summary for a specific day")
    summary_day.add_argument("--date", required=True, help="YYYY-MM-DD")

    telegram = subparsers.add_parser("telegram", help="Telegram polling utilities")
    telegram_sub = telegram.add_subparsers(dest="action", required=True)
    telegram_poll = telegram_sub.add_parser("poll", help="Poll Telegram updates (callbacks + private messages)")
    telegram_poll.add_argument("--limit", type=int, default=20)
    telegram_sub.add_parser("setup-menu", help="Setup Telegram command menu")

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
        user_repo = UserRepository(conn)
        if args.entity == "user":
            return _dispatch_user(user_repo, args)
        if args.entity == "telegram":
            sender = _build_telegram_sender_from_env()
            if sender is None:
                print("TELEGRAM_BOT_TOKEN 未设置，无法执行 telegram 命令")
                return 1
            if args.action == "setup-menu":
                try:
                    result = sender.setup_menu()
                except RuntimeError as exc:
                    print(f"telegram setup-menu failed: {exc}")
                    return 1
                if result.get("menu_button", True):
                    print("telegram 菜单已设置：/r /w /c /help")
                else:
                    print("telegram 命令菜单已设置；菜单按钮未设置成功")
                return 0
            poller = TelegramPollingService(conn, sender)
            try:
                result = poller.poll(limit=args.limit)
            except RuntimeError as exc:
                print(f"telegram poll failed: {exc}")
                return 1
            print(
                "telegram poll done: "
                f"fetched={result['fetched']}, processed={result['processed']}, "
                f"callbacks={result.get('processed_callbacks', 0)}, "
                f"messages={result.get('processed_messages', 0)}, "
                f"inbox_created={result.get('inbox_created', 0)}, "
                f"inbox_failed={result.get('inbox_failed', 0)}, ignored={result['ignored']}"
            )
            reasons = result.get("ignored_reasons", {})
            if isinstance(reasons, dict) and reasons:
                reason_text = ",".join(f"{k}:{reasons[k]}" for k in sorted(reasons))
                print(f"ignored reasons: {reason_text}")
            return 0

        user = user_repo.get_by_username(args.user)
        if user is None:
            print(f"user not found: {args.user}")
            return 1
        service = LifeSystemService(
            conn,
            user_id=user["id"],
            username=user["username"],
            telegram_chat_id=user.get("telegram_chat_id"),
            reminder_sender=_build_telegram_sender_from_env(),
        )
        return _dispatch(service, args)


def _dispatch_user(user_repo: UserRepository, args: argparse.Namespace) -> int:
    if args.action == "list":
        rows = user_repo.list_all()
        for row in rows:
            tg = "已配置" if row.get("telegram_chat_id") else "未配置"
            print(f"{row['id']}\t{row['username']}\t{row['display_name']}\tTelegram:{tg}")
        return 0

    if args.action == "add":
        user_id = user_repo.add(username=args.username, display_name=args.display_name, created_at=now_utc_iso())
        if user_id is None:
            print(f"username already exists: {args.username}")
            return 1
        print(f"user added: id={user_id} username={args.username}")
        return 0

    if args.action == "set-telegram":
        updated = user_repo.set_telegram_chat_id(args.username, args.chat_id)
        if not updated:
            print(f"user not found: {args.username}")
            return 1
        print(f"telegram chat id set for {args.username}")
        return 0

    if args.action == "clear-telegram":
        updated = user_repo.clear_telegram_chat_id(args.username)
        if not updated:
            print(f"user not found: {args.username}")
            return 1
        print(f"telegram chat id cleared for {args.username}")
        return 0

    return 1


def _build_telegram_sender_from_env() -> TelegramReminderSender | None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        return None
    return TelegramReminderSender(token)


def _validate_iso8601(value: str, field_name: str) -> bool:
    if not ISO_8601_PATTERN.match(value):
        print(f"invalid {field_name}: must be ISO-8601 like 2026-03-08T09:00:00+08:00")
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        print(f"invalid {field_name}: must be ISO-8601 like 2026-03-08T09:00:00+08:00")
        return False
    return True


def _validate_level(value: int | None, field_name: str) -> bool:
    if value is None:
        return True
    if 1 <= value <= 5:
        return True
    print(f"invalid {field_name}: must be 1-5")
    return False


def _validate_date_yyyy_mm_dd(value: str) -> bool:
    if not DATE_PATTERN.match(value):
        print("invalid date: must be YYYY-MM-DD")
        return False
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        print("invalid date: must be YYYY-MM-DD")
        return False
    return True


def _fmt_optional(value: object) -> str:
    return "-" if value is None else str(value)


def _fmt_journal_levels(row: dict[str, object]) -> str:
    return f"E{_fmt_optional(row.get('energy_level'))} F{_fmt_optional(row.get('focus_level'))} M{_fmt_optional(row.get('mood_level'))}"


def _print_journal_entries(rows: list[dict[str, object]]) -> None:
    for row in rows:
        print(f"[{row['id']}] {row['entry_type']} | {row['created_at']} | {_fmt_journal_levels(row)}")
        print(f"  {row['content']}")


def _print_kv_block(item: dict[str, object], keys: list[str]) -> None:
    for key in keys:
        print(f"{key}: {item.get(key)}")


def _format_history_payload(payload: str | None) -> str:
    if not payload:
        return "-"
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return payload
    if isinstance(data, dict):
        parts = [f"{k}={data[k]}" for k in sorted(data)]
        return ", ".join(parts) if parts else "-"
    return str(data)


def _print_summary(summary: dict[str, object]) -> None:
    day = summary["day"]
    overview = summary["overview"]
    grouped = summary["journal_grouped"]
    state = summary["state_snapshot"]
    loops = summary["open_loops"]
    note = summary["note"]

    print(f"== 每日总结 | {day} ==")
    print("【今日概览】")
    print(
        "收件箱: 新增={inbox_captured}, 已分拣={inbox_triaged}, 已归档={inbox_archived}".format(**overview)
    )
    print(
        "任务: 新建={tasks_created}, 完成={tasks_done}, 延后={tasks_snoozed}, 放弃={tasks_abandoned}".format(
            **overview
        )
    )
    print(
        "提醒: 首次发送={reminders_sent}, 重试={reminders_retried}, 已确认={reminders_acknowledged}, 已跳过={reminders_skipped}, 已过期={reminders_expired}".format(
            **overview
        )
    )
    print("Anki: 新建草稿={anki_created}, 已导出={anki_exported}".format(**overview))
    print("日志条目: {journal_count}".format(**overview))
    print("")

    print("【日志亮点】")
    has_journal = False
    type_labels = {
        "activity": "活动",
        "reflection": "反思",
        "win": "小胜利",
        "checkin": "状态记录",
    }
    rows_all: list[dict[str, object]] = []
    for et in ("activity", "reflection", "win", "checkin"):
        for row in grouped.get(et, []):
            copied = dict(row)
            copied["_et"] = et
            rows_all.append(copied)
    rows_all.sort(key=lambda r: str(r.get("created_at", "")), reverse=True)
    rows_all = rows_all[:3]
    if rows_all:
        has_journal = True
        for row in rows_all:
            et = str(row.get("_et"))
            label = type_labels.get(et, et)
            ts = _to_cst_display(str(row["created_at"]))
            print(f"- {label} [{row['id']}] {ts} | {row['content']}")
    if not has_journal:
        print("- 今天暂无日志记录")
    print("")

    print("【状态快照】")
    if state["avg_energy"] is None and state["avg_focus"] is None and state["avg_mood"] is None:
        print("无状态数据")
    else:
        e = "-" if state["avg_energy"] is None else f"{float(state['avg_energy']):.2f}"
        f = "-" if state["avg_focus"] is None else f"{float(state['avg_focus']):.2f}"
        m = "-" if state["avg_mood"] is None else f"{float(state['avg_mood']):.2f}"
        print(f"平均能量: {e} | 平均专注: {f} | 平均心情: {m}")
    print("")

    print("【未闭环事项】")
    print(f"开放任务: {loops['open_tasks']}")
    print(f"延后任务: {loops['snoozed_tasks']}")
    print(f"待确认提醒: {loops['pending_ack']}")
    print("")

    print("【今日短注】")
    print(note)


def _to_cst_display(iso_text: str) -> str:
    dt = datetime.fromisoformat(iso_text.replace("Z", "+00:00"))
    return dt.astimezone(CST).strftime("%Y-%m-%d %H:%M")


def _to_cst_display_with_seconds(iso_text: str) -> str:
    dt = datetime.fromisoformat(iso_text.replace("Z", "+00:00"))
    return dt.astimezone(CST).strftime("%Y-%m-%d %H:%M:%S")


def _fmt_reminder_time(value: object) -> str:
    if value is None:
        return "-"
    text = str(value)
    try:
        return _to_cst_display_with_seconds(text)
    except Exception:
        return text


def _print_reminder_show(item: dict[str, object]) -> None:
    keys = [
        "id",
        "task_id",
        "task_title",
        "status",
        "remind_at",
        "requires_ack",
        "ack_at",
        "last_attempt_at",
        "attempt_count",
        "next_retry_at",
        "max_attempts",
        "escalation_level",
        "acked_via",
        "skip_reason",
        "message_ref",
        "created_at",
    ]
    time_keys = {"remind_at", "ack_at", "last_attempt_at", "next_retry_at", "created_at"}
    for key in keys:
        value = item.get(key)
        if key in time_keys:
            print(f"{key}: {_fmt_reminder_time(value)}")
        else:
            print(f"{key}: {value}")


def _format_history_payload_cst(payload: str | None) -> str:
    if not payload:
        return "-"
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return payload
    if not isinstance(data, dict):
        return str(data)

    time_keys = {"remind_at", "next_retry_at", "ack_at", "last_attempt_at"}
    out: list[str] = []
    for k in sorted(data):
        v = data[k]
        if k in time_keys:
            v = _fmt_reminder_time(v)
        out.append(f"{k}={v}")
    return ", ".join(out) if out else "-"


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
        items = service.list_inbox(status=args.status, limit=args.limit, include_archived=args.all)
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
        status = service.archive_inbox(args.inbox_id)
        if status == "archived":
            print("inbox archived")
            return 0
        if status == "already_archived":
            print("already archived")
            return 0
        print("inbox item not found")
        return 1

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
                f"[{task['id']}] {task['status']} | p{task['priority']} | due={_fmt_optional(task['due_at'])} "
                f"| snooze={_fmt_optional(task['snooze_until'])} | {task['title']}"
            )
        return 0

    if entity == "task" and action == "done":
        ok = service.done_task(task_id=args.task_id)
        print("task done" if ok else "task not found")
        return 0 if ok else 1

    if entity == "task" and action == "snooze":
        if not _validate_iso8601(args.snooze_until, "snooze_until"):
            return 1
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
        if not _validate_iso8601(args.remind_at, "remind_at"):
            return 1
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
        if args.send:
            result = service.send_due_reminders(now=args.now, limit=args.limit)
            if result["error"] == "missing_telegram_token":
                print("TELEGRAM_BOT_TOKEN 未设置，无法发送 Telegram 提醒")
                return 1
            items = result["items"]
        else:
            items = service.due_reminders(now=args.now, limit=args.limit, send=False)
        for item in items:
            print(
                f"[{item['id']}] {item['status']} | attempt={item['attempt_count']} "
                f"| retry={_fmt_reminder_time(item['next_retry_at'])} | remind_at={_fmt_reminder_time(item['remind_at'])} "
                f"| task={item['task_id']} {item['task_title']}"
            )
        if args.send:
            print(f"reminders processed: {result['processed']}, failed: {result['failed']}")
        return 0

    if entity == "reminder" and action == "pending-ack":
        items = service.list_pending_ack_reminders(limit=args.limit)
        for item in items:
            print(
                f"[{item['id']}] {item['status']} | attempt={item['attempt_count']} "
                f"| retry={_fmt_reminder_time(item['next_retry_at'])} | task={item['task_id']} {item['task_title']}"
            )
        return 0

    if entity == "reminder" and action == "ack":
        status = service.ack_reminder(args.reminder_id)
        if status == "acknowledged":
            print("reminder acknowledged")
            return 0
        if status == "already_acknowledged":
            print("already acknowledged")
            return 0
        print("reminder not found")
        return 1

    if entity == "reminder" and action == "snooze":
        if not _validate_iso8601(args.remind_at, "remind_at"):
            return 1
        status = service.snooze_reminder(args.reminder_id, args.remind_at)
        if status == "snoozed":
            print("reminder snoozed")
            return 0
        if status == "already_snoozed_same":
            print(f"already snoozed to {args.remind_at}")
            return 0
        print("reminder not found")
        return 1

    if entity == "reminder" and action == "skip":
        status = service.skip_reminder(args.reminder_id, reason=args.reason)
        if status == "skipped":
            print("reminder skipped")
            return 0
        if status == "already_skipped":
            print("already skipped")
            return 0
        print("reminder not found")
        return 1

    if entity == "reminder" and action == "show":
        item = service.show_reminder(args.reminder_id)
        if item is None:
            print("reminder not found")
            return 1
        _print_reminder_show(item)
        return 0

    if entity == "reminder" and action == "history":
        events = service.reminder_history(args.reminder_id)
        if events is None:
            print("reminder not found")
            return 1
        for ev in events:
            payload_text = _format_history_payload_cst(ev["payload"])
            print(f"[{ev['id']}] {_fmt_reminder_time(ev['event_at'])} | {ev['event_type']} | {payload_text}")
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

    if entity == "journal" and action == "add":
        if not _validate_level(args.energy, "energy"):
            return 1
        if not _validate_level(args.focus, "focus"):
            return 1
        if not _validate_level(args.mood, "mood"):
            return 1
        entry_id = service.add_journal_entry(
            content=args.content,
            entry_type=args.entry_type,
            related_task_id=args.task_id,
            related_inbox_id=args.inbox_id,
            energy_level=args.energy,
            focus_level=args.focus,
            mood_level=args.mood,
            tags=args.tags,
        )
        print(f"journal entry added: id={entry_id}")
        return 0

    if entity == "journal" and action == "list":
        rows = service.list_journal(limit=args.limit, entry_type=args.entry_type)
        _print_journal_entries(rows)
        return 0

    if entity == "journal" and action == "today":
        rows = service.today_journal(limit=args.limit, entry_type=args.entry_type)
        _print_journal_entries(rows)
        return 0

    if entity == "summary" and action == "today":
        summary = service.build_today_summary()
        _print_summary(summary)
        return 0

    if entity == "summary" and action == "day":
        if not _validate_date_yyyy_mm_dd(args.date):
            return 1
        summary = service.build_day_summary(args.date)
        _print_summary(summary)
        return 0

    parser = build_parser()
    parser.print_help()
    return 1
