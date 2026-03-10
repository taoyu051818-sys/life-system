import argparse
import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Sequence

from life_system.app.services import InboxReviewService, LifeSystemService
from life_system.app.telegram_polling import TelegramPollingService
from life_system.infra.db import connection_ctx, ensure_database, now_utc_iso, resolve_db_path
from life_system.infra.deepseek_client import DeepSeekClient
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
    inbox_history = inbox_sub.add_parser("history", help="Show triage events for one inbox item")
    inbox_history.add_argument("inbox_id", type=int)
    inbox_triage_history = inbox_sub.add_parser("triage-history", help="Show recent triage events")
    inbox_triage_history.add_argument("--limit", type=int, default=50)
    inbox_review_due = inbox_sub.add_parser("review-due", help="Check inbox review reminder candidates")
    inbox_review_due.add_argument("--now", default=None, help="ISO timestamp, default utc now")
    inbox_review_send = inbox_sub.add_parser("review-send", help="Send inbox review reminders")
    inbox_review_send.add_argument("--now", default=None, help="ISO timestamp, default utc now")
    inbox_feedback_scan = inbox_sub.add_parser("feedback-scan", help="Scan feedback signals for inbox outcomes")
    inbox_feedback_scan.add_argument("--now", default=None, help="ISO timestamp, default utc now")
    inbox_feedback_report = inbox_sub.add_parser("feedback-report", help="Show recent inbox feedback signals")
    inbox_feedback_report.add_argument("--limit", type=int, default=50)

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
    anki_review_due = anki_sub.add_parser("review-due", help="List due Anki cards")
    anki_review_due.add_argument("--limit", type=int, default=20)
    anki_review_due.add_argument("--now", default=None, help="ISO timestamp, default utc now")
    anki_review = anki_sub.add_parser("review", help="Review one Anki card")
    anki_review.add_argument("card_id", type=int)
    anki_review.add_argument("--rate", required=True, choices=["again", "hard", "good", "easy"])
    anki_review.add_argument("--now", default=None, help="ISO timestamp, default utc now")
    anki_activate = anki_sub.add_parser("activate", help="Activate draft ids into review cards")
    anki_activate.add_argument("draft_ids", nargs="+", type=int)
    anki_activate.add_argument("--now", default=None, help="ISO timestamp, default utc now")
    anki_update = anki_sub.add_parser("update")
    anki_update.add_argument("draft_id", type=int)
    anki_update.add_argument("--front", default=None)
    anki_update.add_argument("--back", default=None)
    anki_update.add_argument("--tags", default=None)
    anki_update.add_argument("--deck", default=None)
    anki_show = anki_sub.add_parser("show")
    anki_show.add_argument("draft_id", type=int)
    anki_archive = anki_sub.add_parser("archive")
    anki_archive.add_argument("draft_id", type=int)
    anki_export = anki_sub.add_parser("export-csv")
    anki_export.add_argument("path")
    anki_export.add_argument("--only-new", action="store_true")
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

    encouragement = subparsers.add_parser("encouragement", help="Generate/send daily encouragement")
    encouragement_sub = encouragement.add_subparsers(dest="action", required=True)
    encouragement_today = encouragement_sub.add_parser("today", help="Preview today's encouragement")
    encouragement_today.add_argument("--now", default=None, help="ISO timestamp, default utc now")
    encouragement_send = encouragement_sub.add_parser("send", help="Send encouragement for current --user")
    encouragement_send.add_argument("--now", default=None, help="ISO timestamp, default utc now")
    encouragement_daily = encouragement_sub.add_parser("send-daily", help="Send encouragement for all users")
    encouragement_daily.add_argument("--now", default=None, help="ISO timestamp, default utc now")

    telegram = subparsers.add_parser("telegram", help="Telegram polling utilities")
    telegram_sub = telegram.add_subparsers(dest="action", required=True)
    telegram_poll = telegram_sub.add_parser("poll", help="Poll Telegram updates (callbacks + private messages)")
    telegram_poll.add_argument("--limit", type=int, default=20)
    telegram_sub.add_parser("setup-menu", help="Setup Telegram command menu")
    telegram_sub.add_parser("setup-keyboard", help="Push focus keyboard to configured private chats")
    telegram_inbox_review = telegram_sub.add_parser("inbox-review", help="Send pending inbox items to Telegram")
    telegram_inbox_review.add_argument("--limit", type=int, default=5)

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
        if args.entity == "inbox" and args.action in {"review-due", "review-send"}:
            sender = _build_telegram_sender_from_env()
            review_service = InboxReviewService(conn, telegram_sender=sender)
            if args.action == "review-due":
                stats = review_service.review_due(now=args.now)
            else:
                stats = review_service.review_send(now=args.now)
            print(
                "inbox review: "
                f"checked_users={stats['checked_users']}, "
                f"sent={stats['sent']}, "
                f"skipped_empty={stats['skipped_empty']}, "
                f"skipped_already_sent={stats['skipped_already_sent']}, "
                f"escalated={stats['escalated']}, "
                f"fallback_cli={stats['fallback_cli']}, "
                f"failed={stats['failed']}"
            )
            return 0
        if args.entity == "encouragement":
            deepseek_client = _build_deepseek_client_from_env()
            sender = _build_telegram_sender_from_env()
            if args.action == "send-daily":
                sent = 0
                fallback_cli = 0
                failed = 0
                for row in user_repo.list_all():
                    service = LifeSystemService(
                        conn,
                        user_id=row["id"],
                        username=row["username"],
                        telegram_chat_id=row.get("telegram_chat_id"),
                        reminder_sender=sender,
                    )
                    try:
                        result = service.send_today_encouragement(now=args.now, deepseek_client=deepseek_client)
                    except Exception:
                        failed += 1
                        continue
                    if result.get("status") == "sent":
                        sent += 1
                    else:
                        fallback_cli += 1
                print(
                    "encouragement daily: "
                    f"sent={sent}, fallback_cli={fallback_cli}, failed={failed}"
                )
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
                reminder_sender=sender,
            )
            if args.action == "today":
                result = service.build_today_encouragement(now=args.now, deepseek_client=deepseek_client)
                print(result["text"])
                return 0
            if args.action == "send":
                try:
                    result = service.send_today_encouragement(now=args.now, deepseek_client=deepseek_client)
                except Exception as exc:
                    print(f"encouragement send failed: {exc}")
                    return 1
                if result.get("status") == "sent":
                    print("encouragement sent: channel=telegram")
                else:
                    print("encouragement generated: channel=cli")
                    print(result["text"])
                return 0
        if args.entity == "telegram":
            sender = _build_telegram_sender_from_env()
            if sender is None:
                print("TELEGRAM_BOT_TOKEN 鏈缃紝鏃犳硶鎵ц telegram 鍛戒护")
                return 1
            if args.action == "setup-menu":
                try:
                    result = sender.setup_menu()
                except RuntimeError as exc:
                    print(f"telegram setup-menu failed: {exc}")
                    return 1
                if result.get("menu_button", True):
                    print("telegram 鑿滃崟宸茶缃細/r /w /c /ir /encouragement /help")
                else:
                    print("telegram command menu set; menu button setup failed")
                return 0
            if args.action == "setup-keyboard":
                pushed = 0
                failed = 0
                for row in user_repo.list_all():
                    chat_id = row.get("telegram_chat_id")
                    if not chat_id:
                        continue
                    try:
                        sender.setup_focus_keyboard(str(chat_id))
                        pushed += 1
                    except RuntimeError:
                        failed += 1
                print(f"telegram keyboard setup: pushed={pushed}, failed={failed}")
                return 0
            if args.action == "inbox-review":
                user = user_repo.get_by_username(args.user)
                if user is None:
                    print(f"user not found: {args.user}")
                    return 1
                chat_id = user.get("telegram_chat_id")
                if not chat_id:
                    print(f"user has no telegram_chat_id: {args.user}")
                    return 1
                service = LifeSystemService(
                    conn,
                    user_id=user["id"],
                    username=user["username"],
                    telegram_chat_id=user.get("telegram_chat_id"),
                    reminder_sender=sender,
                )
                items = service.list_new_inbox_oldest(limit=args.limit)
                sent = 0
                failed = 0
                for item in items:
                    try:
                        sender.send_inbox_review_item(str(chat_id), int(item["id"]), str(item["content"]))
                        sent += 1
                    except RuntimeError:
                        failed += 1
                print(f"telegram inbox-review: sent={sent}, failed={failed}, total={len(items)}")
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
            tg = "configured" if row.get("telegram_chat_id") else "not_configured"
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


def _build_deepseek_client_from_env() -> DeepSeekClient | None:
    api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("APIKEY")
    if not api_key:
        return None
    return DeepSeekClient(
        api_key=api_key,
        base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
    )

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
        "收件箱: 新增={inbox_captured}, 已分流={inbox_triaged}, 已归档={inbox_archived}".format(**overview)
    )
    print(
        "任务: 新建={tasks_created}, 完成={tasks_done}, 延后={tasks_snoozed}, 放弃={tasks_abandoned}".format(**overview)
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
    label_map = {
        "activity": "活动",
        "reflection": "反思",
        "win": "小胜利",
        "checkin": "状态记录",
    }
    for key in ["activity", "reflection", "win", "checkin"]:
        rows = grouped.get(key, []) if isinstance(grouped, dict) else []
        for row in rows[:3]:
            has_journal = True
            created_at = _fmt_reminder_time(str(row.get("created_at") or ""))
            print(f"- {label_map.get(key, key)} | {created_at} | {row.get('content', '')}")
    if not has_journal:
        print("- 今日暂无日志记录")
    print("")

    print("【状态快照】")
    avg_energy = state.get("avg_energy") if isinstance(state, dict) else None
    avg_focus = state.get("avg_focus") if isinstance(state, dict) else None
    avg_mood = state.get("avg_mood") if isinstance(state, dict) else None
    if avg_energy is None and avg_focus is None and avg_mood is None:
        print("- no state data")
    else:
        def _fmt_avg(v: object) -> str:
            return "-" if v is None else f"{float(v):.1f}"
        print(f"- 平均能量={_fmt_avg(avg_energy)}, 平均专注={_fmt_avg(avg_focus)}, 平均心情={_fmt_avg(avg_mood)}")
    print("")

    print("【未闭环事项】")
    print(
        f"- 开放任务={loops.get('open_tasks', 0)}, 延后任务={loops.get('snoozed_tasks', 0)}, 待确认提醒={loops.get('pending_ack', 0)}"
    )
    print("")

    print("【今日短注】")
    print(note)

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
        triage_status = service.inbox_triage_status(args.inbox_id)
        if triage_status == "not_found":
            print("inbox item not found")
            return 1
        if triage_status == "already_archived":
            print("inbox already archived")
            return 0
        if triage_status == "already_triaged":
            print("inbox already triaged")
            return 0
        if args.target == "task":
            task_id = service.triage_inbox_to_task(args.inbox_id)
            if task_id is None:
                print("inbox item not found")
                return 1
            print(f"inbox triaged to task: inbox_id={args.inbox_id} task_id={task_id}")
            for warning in service.pop_nonfatal_warnings():
                print(f"warning: {warning}")
            return 0
        if args.target == "anki":
            draft_id = service.triage_inbox_to_anki(args.inbox_id)
            if draft_id is None:
                print("inbox item not found")
                return 1
            print(f"inbox triaged to anki: inbox_id={args.inbox_id} draft_id={draft_id}")
            for warning in service.pop_nonfatal_warnings():
                print(f"warning: {warning}")
            return 0
        status = service.archive_inbox(args.inbox_id)
        if status == "archived":
            print("inbox archived")
            for warning in service.pop_nonfatal_warnings():
                print(f"warning: {warning}")
            return 0
        if status == "already_archived":
            print("inbox already archived")
            return 0
        if status == "already_triaged":
            print("inbox already triaged")
            return 0
        print("inbox item not found")
        return 1

    if entity == "inbox" and action == "history":
        rows = service.inbox_history(args.inbox_id)
        if rows is None:
            print("inbox item not found")
            return 1
        for row in rows:
            print(
                f"[{row['id']}] {row['created_at']} | action={row['action']} | "
                f"target={row['target_type']}:{row['target_id']} | by={row['created_by']} | "
                f"rule={_fmt_optional(row['source_rule_name'])}/{_fmt_optional(row['source_rule_version'])}"
            )
        return 0

    if entity == "inbox" and action == "triage-history":
        rows = service.triage_history(limit=args.limit)
        for row in rows:
            print(
                f"[{row['id']}] inbox={row['inbox_item_id']} | {row['created_at']} | action={row['action']} | "
                f"target={row['target_type']}:{row['target_id']} | by={row['created_by']} | "
                f"rule={_fmt_optional(row['source_rule_name'])}/{_fmt_optional(row['source_rule_version'])}"
            )
        return 0

    if entity == "inbox" and action == "feedback-scan":
        stats = service.feedback_scan(now=args.now)
        print(
            "inbox feedback scan: "
            f"scanned_auto_inbox={stats['scanned_auto_inbox']}, "
            f"scanned_review_sends={stats['scanned_review_sends']}, "
            f"created_signals={stats['created_signals']}, "
            f"skipped_existing={stats['skipped_existing']}, "
            f"failed={stats['failed']}"
        )
        return 0

    if entity == "inbox" and action == "feedback-report":
        rows = service.feedback_report(limit=args.limit)
        for row in rows:
            print(
                f"[{row['id']}] {row['created_at']} | "
                f"subject={row['subject_type']}:{row['subject_key']} | signal={row['signal_type']} | "
                f"rule={_fmt_optional(row['source_rule_name'])}/{_fmt_optional(row['source_rule_version'])}"
            )
        return 0

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
                print("TELEGRAM_BOT_TOKEN 鏈缃紝鏃犳硶鍙戦€?Telegram 鎻愰啋")
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

    if entity == "anki" and action == "activate":
        result = service.activate_anki_drafts(draft_ids=[int(x) for x in args.draft_ids], now=args.now)
        print(
            "anki activate: "
            f"created_count={result['created_count']} "
            f"skipped_duplicate_count={result['skipped_duplicate_count']}"
        )
        if result["created_card_ids"]:
            print("created_card_ids=" + ",".join(str(i) for i in result["created_card_ids"]))
        for item in result["skipped"]:
            if item.get("reason") == "duplicate":
                print(
                    f"skipped draft_id={item['draft_id']} reason=duplicate existing_card_id={item.get('existing_card_id')}"
                )
            else:
                print(f"skipped draft_id={item['draft_id']} reason={item.get('reason')}")
        return 0

    if entity == "anki" and action == "review-due":
        items = service.list_due_anki_cards(limit=args.limit, now=args.now)
        for item in items:
            print(
                f"[{item['id']}] {item['state']} | due={item['due_at']} | interval={item.get('interval_days')} "
                f"| ease={item.get('ease_factor')} | {item['front']}"
            )
        return 0

    if entity == "anki" and action == "review":
        updated = service.review_anki_card(card_id=args.card_id, rating=args.rate, now=args.now)
        if updated is None:
            print("anki card not found")
            return 1
        print(
            f"anki card reviewed: id={updated['id']} rating={args.rate} "
            f"state={updated['state']} due_at={updated['due_at']} interval={updated.get('interval_days')}"
        )
        return 0

    if entity == "anki" and action == "list":
        items = service.list_anki_drafts(status=args.status, limit=args.limit)
        for item in items:
            print(
                f"[{item['id']}] 鐘舵€?status)={item['status']} | 鐗岀粍(deck)={item['deck_name']} | 鏉ユ簮(source)={item['source_type']}:{item['source_id']} | 瀵煎嚭鏃堕棿(exported_at)={_fmt_optional(item.get('exported_at'))}"
            )
        return 0

    if entity == "anki" and action == "update":
        if args.front is None and args.back is None and args.tags is None and args.deck is None:
            print("no fields to update: use --front/--back/--tags/--deck")
            return 1
        status = service.update_anki_draft(
            args.draft_id,
            front=args.front,
            back=args.back,
            tags=args.tags,
            deck_name=args.deck,
        )
        if status == "updated":
            print("anki draft updated")
            return 0
        if status == "not_found":
            print("anki draft not found")
            return 1
        print("no fields to update: use --front/--back/--tags/--deck")
        return 1

    if entity == "anki" and action == "show":
        item = service.show_anki_draft(args.draft_id)
        if item is None:
            print("anki draft not found")
            return 1
        print("== Anki 鑽夌璇︽儏 (draft detail) ==")
        fields = [
            "id",
            "status",
            "deck_name",
            "source_type",
            "source_id",
            "created_at",
            "exported_at",
            "source_inbox_item_id",
            "source_journal_entry_id",
            "source_inbox_source",
            "source_inbox_created_by",
            "source_inbox_rule_name",
            "source_inbox_rule_version",
            "source_inbox_created_at",
            "source_journal_id",
            "source_journal_entry_type",
            "source_journal_created_at",
            "source_triage_event_id",
            "source_triage_created_by",
            "source_triage_created_at",
            "front",
            "back",
            "tags",
        ]
        _print_kv_block(item, fields)
        return 0

    if entity == "anki" and action == "archive":
        status = service.archive_anki_draft(args.draft_id)
        if status == "archived":
            print("anki draft archived")
            return 0
        if status == "already_archived":
            print("anki draft already archived")
            return 0
        print("anki draft not found")
        return 1

    if entity == "anki" and action == "export-csv":
        count = service.export_anki_drafts_csv(args.path, only_new=args.only_new)
        print(f"anki drafts exported: count={count} path={args.path} only_new={args.only_new}")
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









