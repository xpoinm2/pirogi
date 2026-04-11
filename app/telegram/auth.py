from __future__ import annotations

import getpass
import logging

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

from app.exceptions import AppError
from app.telegram.retry import call_with_retry


async def ensure_authorized(
    client: TelegramClient,
    *,
    logger: logging.Logger,
    phone_number: str | None,
    max_attempts: int,
    interactive: bool = True,
) -> None:
    await client.connect()

    if await client.is_user_authorized():
        return

    if not interactive:
        raise AppError(
            "Сессия не авторизована. Сначала выполните login в интерактивном режиме."
        )

    phone = phone_number or input("Введите номер телефона в международном формате (+7999...): ").strip()
    if not phone:
        raise AppError("Телефон не указан")

    sent_code = await call_with_retry(
        description="send_code_request",
        logger=logger,
        operation=lambda: client.send_code_request(phone),
        max_attempts=max_attempts,
    )

    code = input("Введите код из Telegram: ").strip()
    if not code:
        raise AppError("Код подтверждения пустой")

    try:
        await call_with_retry(
            description="sign_in",
            logger=logger,
            operation=lambda: client.sign_in(
                phone=phone,
                code=code,
                phone_code_hash=sent_code.phone_code_hash,
            ),
            max_attempts=max_attempts,
        )
    except SessionPasswordNeededError:
        password = getpass.getpass("Введите пароль двухфакторной аутентификации: ").strip()
        if not password:
            raise AppError("Пароль 2FA пустой")

        await call_with_retry(
            description="sign_in_2fa",
            logger=logger,
            operation=lambda: client.sign_in(password=password),
            max_attempts=max_attempts,
        )

    me = await client.get_me()
    logger.info("Успешный вход: id=%s username=%s", getattr(me, "id", None), getattr(me, "username", None))
