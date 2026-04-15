from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from app.cli import ConsoleMenu, print_dialogs, print_scheduled_messages, resolve_dialog
from app.db import Database
from app.exceptions import AppError, ConfigError, ValidationError
from app.logging_setup import setup_logging
from app.services.scheduler_service import (
    cancel_remote_scheduled,
    load_messages_from_file,
    mass_schedule_messages,
    preview_import,
)
from app.services.relay_service import (
    build_relay_tasks,
    pause_relay_run,
    process_relay_run,
    resume_relay_run,
)
from app.settings import Settings
from app.telegram.auth import ensure_authorized
from app.telegram.chats import list_dialogs
from app.telegram.client import create_client
from app.telegram.scheduled import list_scheduled_messages


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="telegram-manager",
        description="Telegram management CLI for personal account via Telethon.",
    )
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("menu", help="Run interactive console menu")
    subparsers.add_parser("login", help="Authorize and persist session")

    dialogs_parser = subparsers.add_parser("dialogs", help="List dialogs")
    dialogs_parser.add_argument("--limit", type=int, default=None, help="Override dialog fetch limit")

    schedule_parser = subparsers.add_parser("schedule", help="Import CSV/JSON and schedule messages")
    schedule_parser.add_argument("--file", required=True, help="Path to CSV or JSON file")
    schedule_parser.add_argument("--chat-id", type=int, help="Dialog id")
    schedule_parser.add_argument("--chat-search", help="Search by chat title or username")
    schedule_parser.add_argument("--dry-run", action="store_true", help="Validate and preview only")

    list_parser = subparsers.add_parser("list-scheduled", help="Show scheduled messages")
    list_parser.add_argument("--chat-id", type=int, help="Dialog id")
    list_parser.add_argument("--chat-search", help="Search by chat title or username")

    cancel_parser = subparsers.add_parser("cancel", help="Cancel scheduled messages")
    cancel_parser.add_argument("--chat-id", type=int, help="Dialog id")
    cancel_parser.add_argument("--chat-search", help="Search by chat title or username")
    cancel_parser.add_argument(
        "--message-ids",
        type=int,
        nargs="+",
        required=False,
        help="Scheduled message ids to cancel",
    )

    preview_parser = subparsers.add_parser("preview-import", help="Validate import file and preview rows")
    preview_parser.add_argument("--file", required=True, help="Path to CSV or JSON file")

    relay_parser = subparsers.add_parser("relay-start", help="Forward/copy messages to target chats with random delays")
    relay_parser.add_argument("--source-chat-id", type=int, required=True, help="Source chat id where messages live")
    relay_parser.add_argument(
        "--message-ids",
        type=int,
        nargs="+",
        help="Source message ids in order (for all_to_all or manual one_to_one)",
    )
    relay_parser.add_argument(
        "--target-chat-ids",
        type=int,
        nargs="+",
        help="Target chat ids (for all_to_all or manual one_to_one)",
    )
    relay_parser.add_argument(
        "--plan-file",
        help="CSV/JSON with message_id,target_chat_id (for one_to_one mode)",
    )
    relay_parser.add_argument("--mode", choices=["one_to_one", "all_to_all"], default="one_to_one")
    relay_parser.add_argument("--delay-min", type=int, default=180, help="Minimum delay in seconds")
    relay_parser.add_argument("--delay-max", type=int, default=360, help="Maximum delay in seconds")
    relay_parser.add_argument("--long-pause-every", type=int, default=20, help="Long pause every N sent messages")
    relay_parser.add_argument("--long-pause-min", type=int, default=300, help="Min long pause in seconds")
    relay_parser.add_argument("--long-pause-max", type=int, default=600, help="Max long pause in seconds")
    relay_parser.add_argument("--dry-run", action="store_true", help="Create run and mark tasks as skipped")

    relay_status_parser = subparsers.add_parser("relay-status", help="Show relay run summary")
    relay_status_parser.add_argument("--run-id", type=int, required=True, help="Relay run id")

    relay_pause_parser = subparsers.add_parser("relay-pause", help="Pause relay run")
    relay_pause_parser.add_argument("--run-id", type=int, required=True, help="Relay run id")

    relay_resume_parser = subparsers.add_parser("relay-resume", help="Resume relay run")
    relay_resume_parser.add_argument("--run-id", type=int, required=True, help="Relay run id")

    return parser


async def async_main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        settings = Settings.load()
    except ConfigError as exc:
        print(f"Ошибка конфигурации: {exc}")
        return 2

    logger = setup_logging(settings.log_dir, settings.log_level)
    db = Database(settings.database_path)
    db.init()

    command = args.command or "menu"

    try:
        if command == "preview-import":
            items = load_messages_from_file(Path(args.file), settings)
            print(f"Файл валиден. Сообщений: {len(items)}")
            for line in preview_import(items):
                print("  ", line)
            return 0
        if command == "relay-status":
            summary = db.relay_run_summary(args.run_id)
            if summary is None:
                raise ValidationError(f"Relay run #{args.run_id} не найден")
            print(
                f"run_id={summary['id']} status={summary['status']} total={summary['total_tasks']} "
                f"sent={summary['sent_tasks']} failed={summary['failed_tasks']} skipped={summary['skipped_tasks']}"
            )
            return 0
        if command == "relay-pause":
            pause_relay_run(db, args.run_id)
            print(f"Relay run #{args.run_id} поставлен на паузу")
            return 0
    except (AppError, ValidationError) as exc:
        print(f"Ошибка: {exc}")
        return 1

    client = create_client(settings)

    try:
        await ensure_authorized(
            client,
            logger=logger,
            phone_number=settings.default_phone,
            max_attempts=settings.max_retries,
            interactive=True,
        )

        if command == "login":
            me = await client.get_me()
            print(f"OK: session is authorized. id={getattr(me, 'id', None)} username={getattr(me, 'username', None)}")
            return 0

        if command == "dialogs":
            dialogs = await list_dialogs(
                client,
                limit=args.limit or settings.dialog_fetch_limit,
            )
            print_dialogs(dialogs)
            return 0

        if command == "schedule":
            dialog = await resolve_dialog(
                client,
                settings=settings,
                chat_id=args.chat_id,
                chat_search=args.chat_search,
                interactive=args.chat_id is None and args.chat_search is None,
            )
            chat_ref = await client.get_input_entity(dialog.id)
            items = load_messages_from_file(Path(args.file), settings)

            result = await mass_schedule_messages(
                client,
                db=db,
                settings=settings,
                logger=logger,
                chat=chat_ref,
                chat_id=dialog.id,
                chat_title=dialog.title,
                items=items,
                dry_run=args.dry_run,
            )
            print(
                f"Итог: total={result.total}, scheduled={result.scheduled}, "
                f"failed={result.failed}, dry_run={result.dry_run}"
            )
            if result.errors:
                print("Ошибки:")
                for error in result.errors:
                    print("  -", error)
            return 0 if result.failed == 0 else 1

        if command == "list-scheduled":
            dialog = await resolve_dialog(
                client,
                settings=settings,
                chat_id=args.chat_id,
                chat_search=args.chat_search,
                interactive=args.chat_id is None and args.chat_search is None,
            )
            chat_ref = await client.get_input_entity(dialog.id)
            messages = await list_scheduled_messages(
                client,
                chat=chat_ref,
                chat_id=dialog.id,
                chat_title=dialog.title,
                logger=logger,
                max_attempts=settings.max_retries,
            )
            print_scheduled_messages(messages, settings=settings)
            return 0

        if command == "cancel":
            dialog = await resolve_dialog(
                client,
                settings=settings,
                chat_id=args.chat_id,
                chat_search=args.chat_search,
                interactive=args.chat_id is None and args.chat_search is None,
            )
            chat_ref = await client.get_input_entity(dialog.id)
            message_ids = args.message_ids
            if not message_ids:
                scheduled = await list_scheduled_messages(
                    client,
                    chat=chat_ref,
                    chat_id=dialog.id,
                    chat_title=dialog.title,
                    logger=logger,
                    max_attempts=settings.max_retries,
                )
                print_scheduled_messages(scheduled, settings=settings)
                raw = input("Введите ID сообщений через пробел: ").strip()
                if not raw:
                    raise ValidationError("Не переданы message ids")
                message_ids = [int(chunk) for chunk in raw.split()]

            await cancel_remote_scheduled(
                client,
                db=db,
                settings=settings,
                logger=logger,
                chat=chat_ref,
                chat_id=dialog.id,
                message_ids=message_ids,
            )
            print("Сообщения отменены.")
            return 0

        if command == "relay-start":
            if args.delay_min <= 0 or args.delay_max <= 0 or args.delay_min > args.delay_max:
                raise ValidationError("delay-min/delay-max заданы некорректно")
            if args.long_pause_every < 0:
                raise ValidationError("long-pause-every не может быть отрицательным")
            if args.long_pause_every > 0 and (
                args.long_pause_min <= 0
                or args.long_pause_max <= 0
                or args.long_pause_min > args.long_pause_max
            ):
                raise ValidationError("long-pause-min/max заданы некорректно")

            if args.plan_file:
                from app.importers import load_relay_plan

                pairs = load_relay_plan(Path(args.plan_file))
                source_ids = [item[0] for item in pairs]
                target_ids = [item[1] for item in pairs]
                mode = "one_to_one"
            else:
                if not args.message_ids or not args.target_chat_ids:
                    raise ValidationError(
                        "Для relay-start передайте --plan-file либо оба списка --message-ids и --target-chat-ids"
                    )
                source_ids = args.message_ids
                target_ids = args.target_chat_ids
                mode = args.mode

            tasks = build_relay_tasks(
                mode=mode,
                source_chat_id=args.source_chat_id,
                source_message_ids=source_ids,
                target_chat_ids=target_ids,
            )
            run_id = db.create_relay_run(
                mode=mode,
                source_chat_id=args.source_chat_id,
                total_tasks=len(tasks),
                delay_min_seconds=args.delay_min,
                delay_max_seconds=args.delay_max,
                long_pause_every=args.long_pause_every,
                long_pause_min_seconds=args.long_pause_min,
                long_pause_max_seconds=args.long_pause_max,
                dry_run=args.dry_run,
            )
            db.add_relay_tasks(
                run_id,
                [(index, args.source_chat_id, msg_id, target_chat_id) for index, msg_id, target_chat_id in tasks],
            )
            print(f"Relay run created: #{run_id}, tasks={len(tasks)}, mode={mode}, dry_run={args.dry_run}")
            summary = await process_relay_run(
                client,
                db=db,
                logger=logger,
                run_id=run_id,
                max_attempts=settings.max_retries,
            )
            print(
                f"Relay done: run_id={summary['id']} status={summary['status']} total={summary['total_tasks']} "
                f"sent={summary['sent_tasks']} failed={summary['failed_tasks']} skipped={summary['skipped_tasks']}"
            )
            return 0 if int(summary["failed_tasks"]) == 0 else 1

        if command == "relay-resume":
            resume_relay_run(db, args.run_id)
            summary = await process_relay_run(
                client,
                db=db,
                logger=logger,
                run_id=args.run_id,
                max_attempts=settings.max_retries,
            )
            print(
                f"Relay done: run_id={summary['id']} status={summary['status']} total={summary['total_tasks']} "
                f"sent={summary['sent_tasks']} failed={summary['failed_tasks']} skipped={summary['skipped_tasks']}"
            )
            return 0 if int(summary["failed_tasks"]) == 0 else 1

        menu = ConsoleMenu(client=client, db=db, settings=settings, logger=logger)
        await menu.run()
        return 0

    except (AppError, ValidationError) as exc:
        print(f"Ошибка: {exc}")
        return 1
    except KeyboardInterrupt:
        print("\nОперация прервана пользователем.")
        return 130
    finally:
        await client.disconnect()


def main() -> None:
    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()
