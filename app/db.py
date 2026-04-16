from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator

from app.models import ScheduledMessageRecord


def _to_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat()


class Database:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def init(self) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS scheduled_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    external_message_id INTEGER,
                    chat_id INTEGER NOT NULL,
                    chat_title TEXT NOT NULL,
                    text TEXT,
                    attachment_path TEXT,
                    send_at TEXT NOT NULL,
                    disable_preview INTEGER NOT NULL DEFAULT 0,
                    source_file TEXT,
                    source_row INTEGER,
                    status TEXT NOT NULL,
                    dry_run INTEGER NOT NULL DEFAULT 0,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_scheduled_unique
                ON scheduled_messages(chat_id, external_message_id)
                WHERE external_message_id IS NOT NULL
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_scheduled_status
                ON scheduled_messages(status, send_at)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_file TEXT NOT NULL UNIQUE,
                    account_name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'live',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    CHECK(status IN ('live', 'frozen', 'deleted'))
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS relay_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mode TEXT NOT NULL,
                    source_chat_id INTEGER NOT NULL,
                    total_tasks INTEGER NOT NULL,
                    delay_min_seconds INTEGER NOT NULL,
                    delay_max_seconds INTEGER NOT NULL,
                    long_pause_every INTEGER NOT NULL DEFAULT 0,
                    long_pause_min_seconds INTEGER NOT NULL DEFAULT 0,
                    long_pause_max_seconds INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'pending',
                    dry_run INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    CHECK(status IN ('pending', 'in_progress', 'paused', 'completed', 'failed'))
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS relay_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    task_index INTEGER NOT NULL,
                    source_chat_id INTEGER NOT NULL,
                    source_message_id INTEGER NOT NULL,
                    target_chat_id INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    external_message_id INTEGER,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES relay_runs(id) ON DELETE CASCADE,
                    CHECK(status IN ('pending', 'in_progress', 'sent', 'failed', 'skipped'))
                )
                """
            )
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_relay_tasks_unique
                ON relay_tasks(run_id, task_index)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_relay_tasks_run_status
                ON relay_tasks(run_id, status, task_index)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_accounts_status
                ON accounts(status, updated_at)
                """
            )

    def save_scheduled_message(self, record: ScheduledMessageRecord) -> None:
        now = _to_iso(datetime.now(UTC))
        send_at = _to_iso(record.send_at)

        with self.connect() as conn:
            if record.external_message_id is not None:
                existing = conn.execute(
                    """
                    SELECT id
                    FROM scheduled_messages
                    WHERE chat_id = ? AND external_message_id = ?
                    """,
                    (record.chat_id, record.external_message_id),
                ).fetchone()
            else:
                existing = None

            if existing:
                conn.execute(
                    """
                    UPDATE scheduled_messages
                    SET chat_title = ?,
                        text = ?,
                        attachment_path = ?,
                        send_at = ?,
                        disable_preview = ?,
                        source_file = ?,
                        source_row = ?,
                        status = ?,
                        dry_run = ?,
                        error_message = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        record.chat_title,
                        record.text,
                        record.attachment_path,
                        send_at,
                        int(record.disable_preview),
                        record.source_file,
                        record.source_row,
                        record.status,
                        int(record.dry_run),
                        record.error_message,
                        now,
                        existing["id"],
                    ),
                )
                return

            conn.execute(
                """
                INSERT INTO scheduled_messages (
                    external_message_id,
                    chat_id,
                    chat_title,
                    text,
                    attachment_path,
                    send_at,
                    disable_preview,
                    source_file,
                    source_row,
                    status,
                    dry_run,
                    error_message,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.external_message_id,
                    record.chat_id,
                    record.chat_title,
                    record.text,
                    record.attachment_path,
                    send_at,
                    int(record.disable_preview),
                    record.source_file,
                    record.source_row,
                    record.status,
                    int(record.dry_run),
                    record.error_message,
                    now,
                    now,
                ),
            )

    def mark_cancelled(self, chat_id: int, message_ids: list[int]) -> None:
        if not message_ids:
            return

        now = _to_iso(datetime.now(UTC))
        placeholders = ",".join("?" for _ in message_ids)

        with self.connect() as conn:
            conn.execute(
                f"""
                UPDATE scheduled_messages
                SET status = 'cancelled',
                    updated_at = ?
                WHERE chat_id = ?
                  AND external_message_id IN ({placeholders})
                """,
                [now, chat_id, *message_ids],
            )

    def list_records(
        self,
        *,
        chat_id: int | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[sqlite3.Row]:
        query = "SELECT * FROM scheduled_messages"
        params: list[object] = []
        where: list[str] = []

        if chat_id is not None:
            where.append("chat_id = ?")
            params.append(chat_id)

        if status is not None:
            where.append("status = ?")
            params.append(status)

        if where:
            query += " WHERE " + " AND ".join(where)

        query += " ORDER BY send_at ASC LIMIT ?"
        params.append(limit)

        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()

        return rows

    def upsert_account(
        self,
        *,
        session_file: str,
        account_name: str,
        status: str = "live",
    ) -> None:
        now = _to_iso(datetime.now(UTC))
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO accounts (session_file, account_name, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(session_file) DO UPDATE SET
                    account_name = excluded.account_name,
                    status = excluded.status,
                    updated_at = excluded.updated_at
                """,
                (session_file, account_name, status, now, now),
            )

    def list_accounts(self, *, include_deleted: bool = True) -> list[sqlite3.Row]:
        query = "SELECT * FROM accounts"
        params: list[object] = []
        if not include_deleted:
            query += " WHERE status != ?"
            params.append("deleted")
        query += " ORDER BY updated_at DESC, id DESC"
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return rows

    def get_account(self, account_id: int) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()

    def update_account_status(self, account_id: int, status: str) -> None:
        now = _to_iso(datetime.now(UTC))
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE accounts
                SET status = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, now, account_id),
            )

    def create_relay_run(
        self,
        *,
        mode: str,
        source_chat_id: int,
        total_tasks: int,
        delay_min_seconds: int,
        delay_max_seconds: int,
        long_pause_every: int,
        long_pause_min_seconds: int,
        long_pause_max_seconds: int,
        dry_run: bool,
    ) -> int:
        now = _to_iso(datetime.now(UTC))
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO relay_runs (
                    mode,
                    source_chat_id,
                    total_tasks,
                    delay_min_seconds,
                    delay_max_seconds,
                    long_pause_every,
                    long_pause_min_seconds,
                    long_pause_max_seconds,
                    status,
                    dry_run,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
                """,
                (
                    mode,
                    source_chat_id,
                    total_tasks,
                    delay_min_seconds,
                    delay_max_seconds,
                    long_pause_every,
                    long_pause_min_seconds,
                    long_pause_max_seconds,
                    int(dry_run),
                    now,
                    now,
                ),
            )
            return int(cursor.lastrowid)

    def add_relay_tasks(self, run_id: int, tasks: list[tuple[int, int, int, int]]) -> None:
        now = _to_iso(datetime.now(UTC))
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT INTO relay_tasks (
                    run_id,
                    task_index,
                    source_chat_id,
                    source_message_id,
                    target_chat_id,
                    status,
                    attempts,
                    external_message_id,
                    error_message,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, 'pending', 0, NULL, NULL, ?, ?)
                """,
                [(run_id, idx, src_chat, msg_id, target_id, now, now) for idx, src_chat, msg_id, target_id in tasks],
            )

    def get_relay_run(self, run_id: int) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM relay_runs WHERE id = ?", (run_id,)).fetchone()

    def update_relay_run_status(self, run_id: int, status: str) -> None:
        now = _to_iso(datetime.now(UTC))
        with self.connect() as conn:
            conn.execute(
                "UPDATE relay_runs SET status = ?, updated_at = ? WHERE id = ?",
                (status, now, run_id),
            )

    def list_relay_tasks(self, run_id: int, *, statuses: tuple[str, ...] | None = None) -> list[sqlite3.Row]:
        query = "SELECT * FROM relay_tasks WHERE run_id = ?"
        params: list[object] = [run_id]
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            query += f" AND status IN ({placeholders})"
            params.extend(statuses)
        query += " ORDER BY task_index ASC"
        with self.connect() as conn:
            return conn.execute(query, params).fetchall()

    def mark_relay_task_started(self, task_id: int, attempts: int) -> None:
        now = _to_iso(datetime.now(UTC))
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE relay_tasks
                SET status = 'in_progress',
                    attempts = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (attempts, now, task_id),
            )

    def mark_relay_task_sent(self, task_id: int, external_message_id: int | None) -> None:
        now = _to_iso(datetime.now(UTC))
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE relay_tasks
                SET status = 'sent',
                    external_message_id = ?,
                    error_message = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (external_message_id, now, task_id),
            )

    def mark_relay_task_failed(self, task_id: int, error_message: str) -> None:
        now = _to_iso(datetime.now(UTC))
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE relay_tasks
                SET status = 'failed',
                    error_message = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (error_message, now, task_id),
            )

    def mark_relay_task_skipped(self, task_id: int, reason: str) -> None:
        now = _to_iso(datetime.now(UTC))
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE relay_tasks
                SET status = 'skipped',
                    error_message = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (reason, now, task_id),
            )

    def relay_run_summary(self, run_id: int) -> dict[str, object] | None:
        with self.connect() as conn:
            run_row = conn.execute("SELECT * FROM relay_runs WHERE id = ?", (run_id,)).fetchone()
            if run_row is None:
                return None
            counters = conn.execute(
                """
                SELECT
                    SUM(CASE WHEN status = 'sent' THEN 1 ELSE 0 END) AS sent_tasks,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_tasks,
                    SUM(CASE WHEN status = 'skipped' THEN 1 ELSE 0 END) AS skipped_tasks
                FROM relay_tasks
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
            result = dict(run_row)
            result["sent_tasks"] = int(counters["sent_tasks"] or 0)
            result["failed_tasks"] = int(counters["failed_tasks"] or 0)
            result["skipped_tasks"] = int(counters["skipped_tasks"] or 0)
            return result

    def list_relay_runs(self, *, limit: int = 100) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT *
                FROM relay_runs
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()