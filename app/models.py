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


@dataclass(slots=True)
class RelayTask:
    run_id: int
    task_index: int
    source_chat_id: int
    source_message_id: int
    target_chat_id: int
    status: str = "pending"
    attempts: int = 0
    external_message_id: int | None = None
    error_message: str | None = None


@dataclass(slots=True)
class RelayRun:
    id: int
    mode: str
    source_chat_id: int
    total_tasks: int
    delay_min_seconds: int
    delay_max_seconds: int
    long_pause_every: int
    long_pause_min_seconds: int
    long_pause_max_seconds: int
    status: str
    dry_run: bool


@dataclass(slots=True)
class RelayRunSummary:
    run_id: int
    total_tasks: int
    sent_tasks: int
    failed_tasks: int
    skipped_tasks: int
    status: str