from __future__ import annotations

import logging
from pathlib import Path

from telethon import TelegramClient
from telethon.errors import RPCError

from app.db import Database
from app.exceptions import ValidationError
from app.importers import load_csv_messages, load_json_messages
from app.models import (
    ImportMessageItem,
    ScheduledMessageInfo,
    ScheduledMessageRecord,
    ScheduleBatchResult,
)
from app.settings import Settings
from app.telegram.scheduled import cancel_scheduled_messages, list_scheduled_messages, schedule_item


def load_messages_from_file(file_path: Path, settings: Settings) -> list[ImportMessageItem]:
    resolved = file_path.resolve()
    if not resolved.exists():
        raise ValidationError(f"Файл не найден: {resolved}")

    suffix = resolved.suffix.lower()
    if suffix == ".csv":
        return load_csv_messages(
            resolved,
            timezone=settings.timezone,
            project_root=settings.project_root,
        )
    if suffix == ".json":
        return load_json_messages(
            resolved,
            timezone=settings.timezone,
            project_root=settings.project_root,
        )

    raise ValidationError("Поддерживаются только .csv и .json")


def preview_import(items: list[ImportMessageItem], *, limit: int = 10) -> list[str]:
    preview: list[str] = []
    for item in items[:limit]:
        preview.append(
            f"row={item.source_row} | send_at={item.send_at.isoformat()} | "
            f"attachment={'yes' if item.has_attachment else 'no'} | text={(item.text or '')[:50]}"
        )
    return preview


async def mass_schedule_messages(
    client: TelegramClient,
    *,
    db: Database,
    settings: Settings,
    logger: logging.Logger,
    chat: object,
    chat_id: int,
    chat_title: str,
    items: list[ImportMessageItem],
    dry_run: bool,
) -> ScheduleBatchResult:
    if not items:
        raise ValidationError("Список сообщений пуст")

    if len(items) > settings.max_batch_size:
        raise ValidationError(
            f"За один запуск можно обработать не более {settings.max_batch_size} сообщений"
        )

    existing_remote = await list_scheduled_messages(
        client,
        chat=chat,
        chat_id=chat_id,
        chat_title=chat_title,
        logger=logger,
        max_attempts=settings.max_retries,
    )

    if len(existing_remote) + len(items) > settings.max_scheduled_per_chat:
        raise ValidationError(
            "Лимит scheduled messages для этого чата будет превышен. "
            f"Уже есть: {len(existing_remote)}, новых: {len(items)}, "
            f"лимит: {settings.max_scheduled_per_chat}"
        )

    result = ScheduleBatchResult(
        total=len(items),
        scheduled=0,
        failed=0,
        dry_run=dry_run,
    )

    if dry_run:
        for offset, item in enumerate(items, start=1):
            result.scheduled_items.append(
                ScheduledMessageInfo(
                    id=-offset,
                    chat_id=chat_id,
                    chat_title=chat_title,
                    text=item.text,
                    schedule_at=item.send_at,
                    has_media=item.has_attachment,
                )
            )
        result.scheduled = len(items)
        return result

    for item in items:
        try:
            message = await schedule_item(
                client,
                chat=chat,
                item=item,
                logger=logger,
                max_attempts=settings.max_retries,
            )

            info = ScheduledMessageInfo(
                id=message.id,
                chat_id=chat_id,
                chat_title=chat_title,
                text=getattr(message, "message", None),
                schedule_at=message.date,
                has_media=getattr(message, "media", None) is not None,
            )
            result.scheduled_items.append(info)
            result.scheduled += 1

            db.save_scheduled_message(
                ScheduledMessageRecord(
                    external_message_id=message.id,
                    chat_id=chat_id,
                    chat_title=chat_title,
                    text=item.text,
                    attachment_path=str(item.attachment_path) if item.attachment_path else None,
                    send_at=item.send_at,
                    disable_preview=item.disable_preview,
                    source_file=item.source_name,
                    source_row=item.source_row,
                    status="scheduled",
                    dry_run=False,
                    error_message=None,
                )
            )

            logger.info(
                "Scheduled message id=%s row=%s chat=%s",
                message.id,
                item.source_row,
                chat_title,
            )
        except RPCError as exc:
            result.failed += 1
            error_text = f"row={item.source_row}: {exc.__class__.__name__}: {exc}"
            result.errors.append(error_text)
            logger.error(error_text)

            db.save_scheduled_message(
                ScheduledMessageRecord(
                    external_message_id=None,
                    chat_id=chat_id,
                    chat_title=chat_title,
                    text=item.text,
                    attachment_path=str(item.attachment_path) if item.attachment_path else None,
                    send_at=item.send_at,
                    disable_preview=item.disable_preview,
                    source_file=item.source_name,
                    source_row=item.source_row,
                    status="failed",
                    dry_run=False,
                    error_message=error_text,
                )
            )

            if exc.__class__.__name__ == "ScheduleTooMuchError":
                logger.error("Достигнут лимит scheduled messages в чате, дальнейшая обработка остановлена")
                break
        except Exception as exc:
            result.failed += 1
            error_text = f"row={item.source_row}: {exc.__class__.__name__}: {exc}"
            result.errors.append(error_text)
            logger.exception(error_text)

            db.save_scheduled_message(
                ScheduledMessageRecord(
                    external_message_id=None,
                    chat_id=chat_id,
                    chat_title=chat_title,
                    text=item.text,
                    attachment_path=str(item.attachment_path) if item.attachment_path else None,
                    send_at=item.send_at,
                    disable_preview=item.disable_preview,
                    source_file=item.source_name,
                    source_row=item.source_row,
                    status="failed",
                    dry_run=False,
                    error_message=error_text,
                )
            )

    return result


async def cancel_remote_scheduled(
    client: TelegramClient,
    *,
    db: Database,
    settings: Settings,
    logger: logging.Logger,
    chat: object,
    chat_id: int,
    message_ids: list[int],
) -> None:
    await cancel_scheduled_messages(
        client,
        chat=chat,
        message_ids=message_ids,
        logger=logger,
        max_attempts=settings.max_retries,
    )
    db.mark_cancelled(chat_id, message_ids)
    logger.info("Cancelled scheduled messages: %s", message_ids)
