from __future__ import annotations

import logging
from pathlib import Path

from telethon import TelegramClient

from app.db import Database
from app.exceptions import AppError, ValidationError
from app.models import DialogInfo, ScheduledMessageInfo
from app.services.scheduler_service import (
    cancel_remote_scheduled,
    load_messages_from_file,
    mass_schedule_messages,
    preview_import,
)
from app.settings import Settings
from app.telegram.chats import list_dialogs
from app.telegram.scheduled import list_scheduled_messages
from app.utils import format_dt, prompt_yes_no, truncate_text


def print_dialogs(dialogs: list[DialogInfo]) -> None:
    if not dialogs:
        print("Диалоги не найдены.")
        return

    print()
    print(f"{'#':>3} | {'dialog_id':>14} | {'type':<22} | {'username':<20} | title")
    print("-" * 100)
    for index, dialog in enumerate(dialogs, start=1):
        username = dialog.username or ""
        print(
            f"{index:>3} | {dialog.id:>14} | {dialog.entity_type:<22} | "
            f"{username:<20} | {dialog.title}"
        )
    print()


def print_scheduled_messages(
    messages: list[ScheduledMessageInfo],
    *,
    settings: Settings,
) -> None:
    if not messages:
        print("Отложенных сообщений нет.")
        return

    print()
    print(f"{'msg_id':>8} | {'when':<24} | {'media':<5} | text")
    print("-" * 100)
    for item in messages:
        print(
            f"{item.id:>8} | {format_dt(item.schedule_at, settings.timezone):<24} | "
            f"{'yes' if item.has_media else 'no':<5} | {truncate_text(item.text, 80)}"
        )
    print()


async def resolve_dialog(
    client: TelegramClient,
    *,
    settings: Settings,
    chat_id: int | None = None,
    chat_search: str | None = None,
    interactive: bool = True,
) -> DialogInfo:
    dialogs = await list_dialogs(client, limit=settings.dialog_fetch_limit)
    if not dialogs:
        raise AppError("Список диалогов пуст")

    if chat_id is not None:
        for dialog in dialogs:
            if dialog.id == chat_id:
                return dialog
        raise AppError(
            "Чат с таким dialog_id не найден среди загруженных диалогов. "
            "Увеличьте DIALOG_FETCH_LIMIT или выберите чат через меню."
        )

    if chat_search:
        matched = [
            item
            for item in dialogs
            if chat_search.lower() in item.title.lower()
            or (item.username and chat_search.lower() in item.username.lower())
        ]
        if not matched:
            raise AppError(f"Чат по запросу '{chat_search}' не найден")
        if len(matched) == 1:
            return matched[0]

        print("Найдено несколько чатов:")
        print_dialogs(matched)
        return _pick_dialog(matched)

    if not interactive:
        raise AppError("Чат не выбран")

    print_dialogs(dialogs)
    return _pick_dialog(dialogs)


def _pick_dialog(dialogs: list[DialogInfo]) -> DialogInfo:
    while True:
        raw = input("Выберите номер чата: ").strip()
        if not raw.isdigit():
            print("Введите номер из первой колонки.")
            continue

        index = int(raw)
        if 1 <= index <= len(dialogs):
            return dialogs[index - 1]

        print("Номер вне диапазона.")


class ConsoleMenu:
    def __init__(
        self,
        *,
        client: TelegramClient,
        db: Database,
        settings: Settings,
        logger: logging.Logger,
    ) -> None:
        self.client = client
        self.db = db
        self.settings = settings
        self.logger = logger

    async def run(self) -> None:
        while True:
            print()
            print("=== Telegram Manager ===")
            print("1. Login / проверить авторизацию")
            print("2. Показать диалоги")
            print("3. Импортировать файл и поставить сообщения в scheduled queue")
            print("4. Показать scheduled messages")
            print("5. Отменить scheduled messages")
            print("6. Показать локальные записи SQLite")
            print("0. Выход")

            choice = input("Выберите действие: ").strip()

            try:
                if choice == "1":
                    await self.login()
                elif choice == "2":
                    await self.show_dialogs()
                elif choice == "3":
                    await self.schedule_from_file()
                elif choice == "4":
                    await self.show_scheduled()
                elif choice == "5":
                    await self.cancel_scheduled()
                elif choice == "6":
                    self.show_local_records()
                elif choice == "0":
                    return
                else:
                    print("Неизвестный пункт меню.")
            except AppError as exc:
                print(f"Ошибка: {exc}")
            except Exception as exc:
                self.logger.exception("Unhandled menu error")
                print(f"Непредвиденная ошибка: {exc}")

    async def login(self) -> None:
        me = await self.client.get_me()
        print(f"Уже авторизованы как id={getattr(me, 'id', None)} username={getattr(me, 'username', None)}")

    async def show_dialogs(self) -> None:
        dialogs = await list_dialogs(self.client, limit=self.settings.dialog_fetch_limit)
        print_dialogs(dialogs)

    async def schedule_from_file(self) -> None:
        file_path = Path(input("Введите путь к CSV/JSON: ").strip())
        dialog = await resolve_dialog(self.client, settings=self.settings, interactive=True)
        chat_ref = await self.client.get_input_entity(dialog.id)

        items = load_messages_from_file(file_path, self.settings)
        print(f"Файл валиден. Найдено сообщений: {len(items)}")
        for line in preview_import(items):
            print("  ", line)

        dry_run = prompt_yes_no("Выполнить dry-run?", default=False)
        result = await mass_schedule_messages(
            self.client,
            db=self.db,
            settings=self.settings,
            logger=self.logger,
            chat=chat_ref,
            chat_id=dialog.id,
            chat_title=dialog.title,
            items=items,
            dry_run=dry_run,
        )

        print(
            f"Итог: total={result.total}, scheduled={result.scheduled}, "
            f"failed={result.failed}, dry_run={result.dry_run}"
        )
        if result.errors:
            print("Ошибки:")
            for error in result.errors:
                print("  -", error)

    async def show_scheduled(self) -> None:
        dialog = await resolve_dialog(self.client, settings=self.settings, interactive=True)
        chat_ref = await self.client.get_input_entity(dialog.id)
        messages = await list_scheduled_messages(
            self.client,
            chat=chat_ref,
            chat_id=dialog.id,
            chat_title=dialog.title,
            logger=self.logger,
            max_attempts=self.settings.max_retries,
        )
        print_scheduled_messages(messages, settings=self.settings)

    async def cancel_scheduled(self) -> None:
        dialog = await resolve_dialog(self.client, settings=self.settings, interactive=True)
        chat_ref = await self.client.get_input_entity(dialog.id)
        messages = await list_scheduled_messages(
            self.client,
            chat=chat_ref,
            chat_id=dialog.id,
            chat_title=dialog.title,
            logger=self.logger,
            max_attempts=self.settings.max_retries,
        )

        print_scheduled_messages(messages, settings=self.settings)
        if not messages:
            return

        raw = input("Введите ID сообщений через запятую для отмены: ").strip()
        if not raw:
            raise ValidationError("Список ID пуст")

        message_ids: list[int] = []
        for chunk in raw.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            if not chunk.isdigit():
                raise ValidationError(f"Некорректный ID: {chunk}")
            message_ids.append(int(chunk))

        await cancel_remote_scheduled(
            self.client,
            db=self.db,
            settings=self.settings,
            logger=self.logger,
            chat=chat_ref,
            chat_id=dialog.id,
            message_ids=message_ids,
        )
        print("Сообщения отменены.")

    def show_local_records(self) -> None:
        rows = self.db.list_records(limit=50)
        if not rows:
            print("В SQLite пока нет записей.")
            return

        print()
        print(f"{'chat_id':>14} | {'external_id':>10} | {'status':<10} | {'send_at':<26} | text")
        print("-" * 110)
        for row in rows:
            print(
                f"{row['chat_id']:>14} | {str(row['external_message_id'] or ''):>10} | "
                f"{row['status']:<10} | {row['send_at']:<26} | {truncate_text(row['text'], 60)}"
            )
        print()
