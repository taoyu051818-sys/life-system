from typing import Any, Mapping, Protocol


class ReminderSender(Protocol):
    def send_reminder(self, payload: Mapping[str, Any]) -> None:
        ...


class AnkiExporter(Protocol):
    def export_draft(self, payload: Mapping[str, Any]) -> str:
        ...


class EventLogger(Protocol):
    def log(self, event_type: str, payload: Mapping[str, Any]) -> None:
        ...


class NullEventLogger:
    def log(self, event_type: str, payload: Mapping[str, Any]) -> None:
        del event_type
        del payload

