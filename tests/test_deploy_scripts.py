from pathlib import Path


def test_run_reminders_includes_inbox_review_send() -> None:
    text = Path("scripts/run_reminders.sh").read_text(encoding="utf-8")
    assert "reminder due --send" in text
    assert "inbox review-send" in text


def test_fix_restart_services_script_is_safe_and_has_key_units() -> None:
    p = Path("scripts/fix_restart_services.sh")
    assert p.exists()
    text = p.read_text(encoding="utf-8")

    assert "set -euo pipefail" in text
    assert "systemctl" in text
    assert "life-reminders.timer" in text
    assert "life-telegram-poll.timer" in text
    assert "life-summary.timer" in text
    assert "life-web.service" in text

    assert "init-db" not in text
    assert "rm -f" not in text
    assert not ("sqlite3" in text.lower() and "delete" in text.lower())


def test_run_encouragement_script_exists_and_sends_daily() -> None:
    p = Path("scripts/run_encouragement.sh")
    assert p.exists()
    text = p.read_text(encoding="utf-8")
    assert "set -euo pipefail" in text
    assert "encouragement send-daily" in text
    assert "init-db" not in text
