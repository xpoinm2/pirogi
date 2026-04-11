from __future__ import annotations

import logging

from telethon import TelegramClient, functions

from app.models import ImportMessageItem, ScheduledMessageInfo
from app.telegram.retry import call_with_retry


async def list_scheduled_messages(
    client: TelegramClient,
    *,
    chat: object,
    chat_id: int,
    chat_title: str,
    logger: logging.Logger,
    max_attempts: int,
) -> list[ScheduledMessageInfo]:
    response = await call_with_retry(
        description="get_scheduled_history",
        logger=logger,
        operation=lambda: client(
            functions.messages.GetScheduledHistoryRequest(
                peer=chat,
                hash=0,
            )
        ),
        max_attempts=max_attempts,
    )

    items: list[ScheduledMessageInfo] = []
    for message in response.messages:
        items.append(
            ScheduledMessageInfo(
                id=message.id,
                chat_id=chat_id,
                chat_title=chat_title,
                text=getattr(message, "message", None),
                schedule_at=message.date,
                has_media=getattr(message, "media", None) is not None,
            )
        )

    items.sort(key=lambda item: item.schedule_at)
    return items


async def schedule_item(
    client: TelegramClient,
    *,
    chat: object,
    item: ImportMessageItem,
    logger: logging.Logger,
    max_attempts: int,
):
    if item.attachment_path is not None:
        return await call_with_retry(
            description=f"send_file(row={item.source_row})",
            logger=logger,
            operation=lambda: client.send_file(
                entity=chat,
                file=str(item.attachment_path),
                caption=item.text,
                schedule=item.send_at,
            ),
            max_attempts=max_attempts,
        )

    return await call_with_retry(
        description=f"send_message(row={item.source_row})",
        logger=logger,
        operation=lambda: client.send_message(
            entity=chat,
            message=item.text or "",
            link_preview=not item.disable_preview,
            schedule=item.send_at,
        ),
        max_attempts=max_attempts,
    )


async def cancel_scheduled_messages(
    client: TelegramClient,
    *,
    chat: object,
    message_ids: list[int],
    logger: logging.Logger,
    max_attempts: int,
) -> None:
    if not message_ids:
        return

    await call_with_retry(
        description="delete_scheduled_messages",
        logger=logger,
        operation=lambda: client(
            functions.messages.DeleteScheduledMessagesRequest(
                peer=chat,
                id=message_ids,
            )
        ),
        max_attempts=max_attempts,
    )
