from __future__ import annotations

from telethon import TelegramClient

from app.models import DialogInfo


async def list_dialogs(client: TelegramClient, *, limit: int | None) -> list[DialogInfo]:
    dialogs: list[DialogInfo] = []

    async for dialog in client.iter_dialogs(limit=limit, ignore_migrated=True):
        username = getattr(dialog.entity, "username", None)
        dialogs.append(
            DialogInfo(
                id=dialog.id,
                title=dialog.title,
                entity_type=dialog.entity.__class__.__name__,
                username=username,
            )
        )

    return dialogs
