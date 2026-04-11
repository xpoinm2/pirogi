from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

from telethon.errors import FloodWaitError, RPCError


T = TypeVar("T")


async def call_with_retry(
    *,
    description: str,
    logger: logging.Logger,
    operation: Callable[[], Awaitable[T]],
    max_attempts: int = 5,
) -> T:
    last_error: BaseException | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            return await operation()
        except FloodWaitError as exc:
            last_error = exc
            if attempt >= max_attempts:
                break

            wait_seconds = max(1, int(getattr(exc, "seconds", 1))) + 1
            logger.warning(
                "%s | FloodWait %s sec | attempt %s/%s",
                description,
                wait_seconds,
                attempt,
                max_attempts,
            )
            await asyncio.sleep(wait_seconds)
        except (asyncio.TimeoutError, TimeoutError, OSError, ConnectionError) as exc:
            last_error = exc
            if attempt >= max_attempts:
                break

            delay = min(30, 2 ** (attempt - 1))
            logger.warning(
                "%s | transient error %s | retry in %s sec | attempt %s/%s",
                description,
                exc.__class__.__name__,
                delay,
                attempt,
                max_attempts,
            )
            await asyncio.sleep(delay)
        except RPCError:
            raise

    assert last_error is not None
    raise last_error
