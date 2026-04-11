from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass(slots=True)
class ImportMessageItem:
    text: str | None
    send_at: datetime
    attachment_path: Path | None
    disable_preview: bool
    source_name: str
    source_row: int

    @property
    def has_attachment(self) -> bool:
        return self.attachment_path is not None


@dataclass(slots=True)
class DialogInfo:
    id: int
    title: str
    entity_type: str
    username: str | None = None


@dataclass(slots=True)
class ScheduledMessageInfo:
    id: int
    chat_id: int
    chat_title: str
    text: str | None
    schedule_at: datetime
    has_media: bool = False


@dataclass(slots=True)
class ScheduledMessageRecord:
    external_message_id: int | None
    chat_id: int
    chat_title: str
    text: str | None
    attachment_path: str | None
    send_at: datetime
    disable_preview: bool
    source_file: str | None
    source_row: int | None
    status: str
    dry_run: bool
    error_message: str | None = None


@dataclass(slots=True)
class ScheduleBatchResult:
    total: int
    scheduled: int
    failed: int
    dry_run: bool
    errors: list[str] = field(default_factory=list)
    scheduled_items: list[ScheduledMessageInfo] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.failed == 0
