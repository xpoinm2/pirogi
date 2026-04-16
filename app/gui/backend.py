from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from telethon.errors import SessionPasswordNeededError

from app.db import Database
from app.exceptions import AppError
from app.models import DialogInfo, ImportMessageItem, ScheduleBatchResult, ScheduledMessageInfo
from app.services.relay_service import (
    build_relay_tasks,
    pause_relay_run,
    process_relay_run,
    resume_relay_run,
)
from app.services.scheduler_service import (
    cancel_remote_scheduled,
    load_messages_from_file,
    mass_schedule_messages,
)
from app.settings import Settings
from app.telegram.chats import list_dialogs
from app.telegram.client import create_client
from app.telegram.retry import call_with_retry
from app.telegram.scheduled import list_scheduled_messages


@dataclass(slots=True)
class AuthResult:
    status: str
    message: str
    user_id: int | None = None
    username: str | None = None
    display_name: str | None = None


class TelegramManagerBackend:
    def __init__(
        self,
        *,
        settings: Settings,
        db: Database,
        logger: logging.Logger,
        session_path: Path | None = None,
    ) -> None:
        self.settings = settings
        self.db = db
        self.logger = logger
        self.client = create_client(settings, session_path=session_path)
        self._pending_phone: str | None = None
        self._phone_code_hash: str | None = None

    async def _run_with_timeout(self, *, label: str, operation):
        timeout_seconds = self.settings.session_check_timeout_seconds
        try:
            return await asyncio.wait_for(operation(), timeout=timeout_seconds)
        except asyncio.TimeoutError as exc:
            self.logger.error("%s превысило лимит ожидания: %s сек", label, timeout_seconds)
            raise AppError(
                "Telegram долго не отвечает. Проверьте VPN/прокси и интернет, затем повторите попытку."
            ) from exc

    async def connect(self) -> None:
        if self.client.is_connected():
            return
        await call_with_retry(
            description="connect_client",
            logger=self.logger,
            operation=self.client.connect,
            max_attempts=self.settings.max_retries,
        )

    async def disconnect(self) -> None:
        if self.client.is_connected():
            await self.client.disconnect()

    async def check_session(self) -> AuthResult:
        self.logger.info("Проверка session начата")
        await self.connect()
        timeout_seconds = self.settings.session_check_timeout_seconds
        try:
            is_authorized = await asyncio.wait_for(
                call_with_retry(
                    description="check_session_is_user_authorized",
                    logger=self.logger,
                    operation=self.client.is_user_authorized,
                    max_attempts=self.settings.max_retries,
                ),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            self.logger.error("Проверка session превысила лимит ожидания: %s сек", timeout_seconds)
            raise AppError(
                "Проверка session заняла слишком много времени. "
                "Проверьте интернет/прокси и повторите попытку."
            ) from exc

        if not is_authorized:
            self.logger.info("Session не авторизована")
            return AuthResult(
                status="not_authorized",
                message="Session не авторизована. Запроси код и заверши вход.",
            )

        try:
            me = await asyncio.wait_for(
                call_with_retry(
                    description="get_me",
                    logger=self.logger,
                    operation=self.client.get_me,
                    max_attempts=self.settings.max_retries,
                ),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            self.logger.error("Получение профиля превысило лимит ожидания: %s сек", timeout_seconds)
            raise AppError(
                "Session авторизована, но Telegram долго отвечает при чтении профиля. "
                "Попробуйте ещё раз."
            ) from exc
        self.logger.info("Session авторизована: user_id=%s", getattr(me, "id", None))
        return self._build_authorized_result(me)

    async def request_code(self, phone_number: str) -> AuthResult:
        phone = phone_number.strip()
        if not phone:
            raise AppError("Введите номер телефона в международном формате, например +79990000000")

        await self.connect()
        current = await self.check_session()
        if current.status == "authorized":
            return current

        sent_code = await self._run_with_timeout(
            label="Запрос кода",
            operation=lambda: call_with_retry(
                description="send_code_request",
                logger=self.logger,
                operation=lambda: self.client.send_code_request(phone),
                max_attempts=self.settings.max_retries,
            ),
        )

        self._pending_phone = phone
        self._phone_code_hash = sent_code.phone_code_hash
        self.logger.info("Код подтверждения отправлен на %s", phone)
        return AuthResult(
            status="code_sent",
            message=f"Код отправлен на {phone}. Введите code и нажмите 'Добавить аккаунт'.",
        )

    async def sign_in(self, code: str, password: str | None = None) -> AuthResult:
        await self.connect()

        current = await self.check_session()
        if current.status == "authorized":
            return current

        if not self._pending_phone or not self._phone_code_hash:
            raise AppError("Сначала нажми 'Запросить код'.")

        clean_code = code.strip()
        if not clean_code:
            raise AppError("Введите код из Telegram / SMS.")

        try:
            await self._run_with_timeout(
                label="Вход по коду",
                operation=lambda: call_with_retry(
                    description="sign_in",
                    logger=self.logger,
                    operation=lambda: self.client.sign_in(
                        phone=self._pending_phone,
                        code=clean_code,
                        phone_code_hash=self._phone_code_hash,
                    ),
                    max_attempts=self.settings.max_retries,
                ),
            )
        except SessionPasswordNeededError:
            clean_password = (password or "").strip()
            if not clean_password:
                return AuthResult(
                    status="password_required",
                    message="Для этого аккаунта включён 2FA пароль. Введите password и нажмите 'Войти'.",
                )

            await self._run_with_timeout(
                label="Вход с 2FA",
                operation=lambda: call_with_retry(
                    description="sign_in_2fa",
                    logger=self.logger,
                    operation=lambda: self.client.sign_in(password=clean_password),
                    max_attempts=self.settings.max_retries,
                ),
            )

        me = await self._run_with_timeout(
            label="Получение профиля после входа",
            operation=lambda: call_with_retry(
                description="get_me_after_sign_in",
                logger=self.logger,
                operation=self.client.get_me,
                max_attempts=self.settings.max_retries,
            ),
        )
        self._pending_phone = None
        self._phone_code_hash = None
        self.logger.info("Вход выполнен: id=%s username=%s", getattr(me, 'id', None), getattr(me, 'username', None))
        return self._build_authorized_result(me)

    async def get_dialogs(self) -> list[DialogInfo]:
        await self._ensure_authorized()
        dialogs = await list_dialogs(self.client, limit=self.settings.dialog_fetch_limit)
        dialogs.sort(key=lambda item: item.title.lower())
        return dialogs

    async def preview_import_file(self, file_path: str) -> list[ImportMessageItem]:
        resolved = self._resolve_existing_file(file_path)
        return load_messages_from_file(resolved, self.settings)

    async def schedule_import_file(
        self,
        *,
        chat_id: int,
        file_path: str,
        dry_run: bool,
    ) -> ScheduleBatchResult:
        await self._ensure_authorized()
        dialog = await self._get_dialog_by_id(chat_id)
        chat_ref = await self.client.get_input_entity(dialog.id)
        items = load_messages_from_file(self._resolve_existing_file(file_path), self.settings)
        return await mass_schedule_messages(
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

    async def get_scheduled_messages(self, *, chat_id: int) -> list[ScheduledMessageInfo]:
        await self._ensure_authorized()
        dialog = await self._get_dialog_by_id(chat_id)
        chat_ref = await self.client.get_input_entity(dialog.id)
        return await list_scheduled_messages(
            self.client,
            chat=chat_ref,
            chat_id=dialog.id,
            chat_title=dialog.title,
            logger=self.logger,
            max_attempts=self.settings.max_retries,
        )

    async def cancel_scheduled_messages(self, *, chat_id: int, message_ids: list[int]) -> None:
        if not message_ids:
            raise AppError("Не выбраны message id для отмены")

        await self._ensure_authorized()
        dialog = await self._get_dialog_by_id(chat_id)
        chat_ref = await self.client.get_input_entity(dialog.id)
        await cancel_remote_scheduled(
            self.client,
            db=self.db,
            settings=self.settings,
            logger=self.logger,
            chat=chat_ref,
            chat_id=dialog.id,
            message_ids=message_ids,
        )

    async def get_local_records(self, *, chat_id: int | None = None) -> list[dict[str, Any]]:
        rows = self.db.list_records(chat_id=chat_id, limit=500)
        return [dict(row) for row in rows]

    async def start_relay_run(
        self,
        *,
        source_chat_id: int,
        message_ids: list[int],
        target_chat_ids: list[int],
        delay_min: int,
        delay_max: int,
        dry_run: bool,
    ) -> dict[str, object]:
        await self._ensure_authorized()


        tasks = build_relay_tasks(
            source_message_ids=message_ids,
            target_chat_ids=target_chat_ids,
        )
        run_id = self.db.create_relay_run(
            mode="all_to_all",
            source_chat_id=source_chat_id,
            total_tasks=len(tasks),
            delay_min_seconds=delay_min,
            delay_max_seconds=delay_max,
            long_pause_every=0,
            long_pause_min_seconds=0,
            long_pause_max_seconds=0,
            dry_run=dry_run,
        )
        self.db.add_relay_tasks(
            run_id,
            [(index, source_chat_id, msg_id, target_chat_id) for index, msg_id, target_chat_id in tasks],
        )
        return await process_relay_run(
            self.client,
            db=self.db,
            logger=self.logger,
            run_id=run_id,
            max_attempts=self.settings.max_retries,
        )

    async def relay_status(self, *, run_id: int) -> dict[str, object]:
        summary = self.db.relay_run_summary(run_id)
        if summary is None:
            raise AppError(f"Relay run #{run_id} не найден")
        return summary

    async def relay_pause(self, *, run_id: int) -> dict[str, object]:
        pause_relay_run(self.db, run_id)
        return await self.relay_status(run_id=run_id)

    async def relay_resume(self, *, run_id: int) -> dict[str, object]:
        await self._ensure_authorized()
        resume_relay_run(self.db, run_id)
        return await process_relay_run(
            self.client,
            db=self.db,
            logger=self.logger,
            run_id=run_id,
            max_attempts=self.settings.max_retries,
        )

    async def get_relay_runs(self, *, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.db.list_relay_runs(limit=limit)
        return [dict(row) for row in rows]


    async def _ensure_authorized(self) -> None:
        await self.connect()
        if not await self.client.is_user_authorized():
            raise AppError("Сначала авторизуйтесь на вкладке 'Авторизация'.")

    async def _get_dialog_by_id(self, chat_id: int) -> DialogInfo:
        dialogs = await self.get_dialogs()
        for dialog in dialogs:
            if dialog.id == chat_id:
                return dialog
        raise AppError(
            "Чат не найден среди загруженных диалогов. Обновите список диалогов или увеличьте DIALOG_FETCH_LIMIT."
        )

    def _resolve_existing_file(self, file_path: str) -> Path:
        path = Path(file_path).expanduser()
        if not path.is_absolute():
            path = (self.settings.project_root / path).resolve()
        if not path.exists():
            raise AppError(f"Файл не найден: {path}")
        return path

    def _build_authorized_result(self, me: object) -> AuthResult:
        first_name = getattr(me, "first_name", None) or ""
        last_name = getattr(me, "last_name", None) or ""
        display_name = (f"{first_name} {last_name}".strip() or getattr(me, "username", None) or "Без имени")
        return AuthResult(
            status="authorized",
            message=f"Авторизация успешна: {display_name}",
            user_id=getattr(me, "id", None),
            username=getattr(me, "username", None),
            display_name=display_name,
        )
