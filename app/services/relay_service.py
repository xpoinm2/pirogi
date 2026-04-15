from __future__ import annotations

import asyncio
import logging
import random

from telethon import TelegramClient
from telethon.errors import RPCError

from app.db import Database
from app.exceptions import ValidationError


def build_relay_tasks(
    *,
    mode: str,
    source_chat_id: int,
    source_message_ids: list[int],
    target_chat_ids: list[int],
) -> list[tuple[int, int, int]]:
    if mode not in {"one_to_one", "all_to_all"}:
        raise ValidationError("mode должен быть one_to_one или all_to_all")
    if not source_message_ids:
        raise ValidationError("Нужен хотя бы один source message id")
    if not target_chat_ids:
        raise ValidationError("Нужен хотя бы один target chat id")

    tasks: list[tuple[int, int, int]] = []
    index = 1
    if mode == "one_to_one":
        if len(source_message_ids) != len(target_chat_ids):
            raise ValidationError("Для one_to_one число message_id должно совпадать с числом target_chat_id")
        for message_id, target_chat_id in zip(source_message_ids, target_chat_ids):
            tasks.append((index, message_id, target_chat_id))
            index += 1
        return tasks

    for message_id in source_message_ids:
        for target_chat_id in target_chat_ids:
            tasks.append((index, message_id, target_chat_id))
            index += 1
    return tasks


async def process_relay_run(
    client: TelegramClient,
    *,
    db: Database,
    logger: logging.Logger,
    run_id: int,
    max_attempts: int,
) -> dict[str, object]:
    run = db.get_relay_run(run_id)
    if run is None:
        raise ValidationError(f"Relay run #{run_id} не найден")

    if run["status"] in {"completed", "failed"}:
        return _require_summary(db, run_id)

    db.update_relay_run_status(run_id, "in_progress")
    run = db.get_relay_run(run_id)
    assert run is not None

    pending = db.list_relay_tasks(run_id, statuses=("pending", "in_progress"))
    if not pending:
        summary = _require_summary(db, run_id)
        status = "completed" if summary["failed_tasks"] == 0 else "failed"
        db.update_relay_run_status(run_id, status)
        return _require_summary(db, run_id)

    source_chat = await client.get_input_entity(int(run["source_chat_id"]))
    entity_cache: dict[int, object] = {}
    sent_counter = int(_require_summary(db, run_id)["sent_tasks"])
    dry_run = bool(run["dry_run"])

    for task in pending:
        latest_run = db.get_relay_run(run_id)
        assert latest_run is not None
        if latest_run["status"] == "paused":
            logger.info("Relay run #%s paused by user", run_id)
            return _require_summary(db, run_id)

        task_id = int(task["id"])
        attempts = int(task["attempts"]) + 1
        db.mark_relay_task_started(task_id, attempts)

        target_chat_id = int(task["target_chat_id"])
        message_id = int(task["source_message_id"])

        try:
            if dry_run:
                db.mark_relay_task_skipped(task_id, "dry_run")
            else:
                message = await _forward_with_fallback(
                    client,
                    source_chat=source_chat,
                    source_message_id=message_id,
                    target_chat_id=target_chat_id,
                    entity_cache=entity_cache,
                )
                db.mark_relay_task_sent(task_id, getattr(message, "id", None))
                sent_counter += 1
                delay_seconds = random.randint(int(run["delay_min_seconds"]), int(run["delay_max_seconds"]))
                logger.info(
                    "Relay task #%s sent (msg=%s -> chat=%s), sleep=%s",
                    task["task_index"],
                    message_id,
                    target_chat_id,
                    delay_seconds,
                )
                await asyncio.sleep(delay_seconds)

                long_pause_every = int(run["long_pause_every"])
                if long_pause_every > 0 and sent_counter % long_pause_every == 0:
                    extra_pause = random.randint(
                        int(run["long_pause_min_seconds"]),
                        int(run["long_pause_max_seconds"]),
                    )
                    logger.info(
                        "Relay run #%s long pause triggered after %s sends: %s sec",
                        run_id,
                        sent_counter,
                        extra_pause,
                    )
                    await asyncio.sleep(extra_pause)
        except RPCError as exc:
            text = f"{exc.__class__.__name__}: {exc}"
            logger.error("Relay task failed (run=%s task=%s): %s", run_id, task["task_index"], text)
            db.mark_relay_task_failed(task_id, text)
            if attempts >= max_attempts:
                logger.error("Relay task task_id=%s exhausted retries=%s", task_id, max_attempts)
        except Exception as exc:
            text = f"{exc.__class__.__name__}: {exc}"
            logger.exception("Relay task crashed (run=%s task=%s)", run_id, task["task_index"])
            db.mark_relay_task_failed(task_id, text)

    summary = _require_summary(db, run_id)
    final_status = "completed" if summary["failed_tasks"] == 0 else "failed"
    db.update_relay_run_status(run_id, final_status)
    return _require_summary(db, run_id)


def pause_relay_run(db: Database, run_id: int) -> None:
    run = db.get_relay_run(run_id)
    if run is None:
        raise ValidationError(f"Relay run #{run_id} не найден")
    if run["status"] in {"completed", "failed"}:
        raise ValidationError(f"Relay run #{run_id} уже завершен со статусом {run['status']}")
    db.update_relay_run_status(run_id, "paused")


def resume_relay_run(db: Database, run_id: int) -> None:
    run = db.get_relay_run(run_id)
    if run is None:
        raise ValidationError(f"Relay run #{run_id} не найден")
    if run["status"] in {"completed", "failed"}:
        raise ValidationError(f"Relay run #{run_id} уже завершен со статусом {run['status']}")
    db.update_relay_run_status(run_id, "pending")


def _require_summary(db: Database, run_id: int) -> dict[str, object]:
    summary = db.relay_run_summary(run_id)
    if summary is None:
        raise ValidationError(f"Relay run #{run_id} не найден")
    return summary


async def _forward_with_fallback(
    client: TelegramClient,
    *,
    source_chat: object,
    source_message_id: int,
    target_chat_id: int,
    entity_cache: dict[int, object],
):
    target_entity = entity_cache.get(target_chat_id)
    if target_entity is None:
        target_entity = await client.get_input_entity(target_chat_id)
        entity_cache[target_chat_id] = target_entity

    try:
        return await client.forward_messages(
            entity=target_entity,
            messages=source_message_id,
            from_peer=source_chat,
            drop_author=True,
        )
    except Exception:
        source = await client.get_messages(source_chat, ids=source_message_id)
        if source is None:
            raise ValidationError(f"Source message id={source_message_id} не найден")
        if getattr(source, "media", None) is not None:
            return await client.send_file(
                entity=target_entity,
                file=source.media,
                caption=source.message or "",
            )
        return await client.send_message(
            entity=target_entity,
            message=source.message or "",
            link_preview=False,
        )
