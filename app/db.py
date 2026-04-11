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
