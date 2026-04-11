from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from zoneinfo import ZoneInfo

from app.exceptions import ValidationError
from app.models import ImportMessageItem
from app.utils import parse_bool, parse_datetime_input, resolve_input_path, utc_now, validate_schedule_window


def build_import_message(
    raw: Mapping[str, object],
    *,
    source_name: str,
    row_number: int,
    timezone: ZoneInfo,
    project_root: Path,
) -> ImportMessageItem:
    text = str(raw.get("text", "") or "").strip() or None
    send_at_raw = str(raw.get("send_at", "") or "").strip()
    attachment_value = str(raw.get("attachment_path", "") or "").strip() or None

    disable_preview_raw = raw.get("disable_preview", False)
    disable_preview = parse_bool(disable_preview_raw)

    if not text and not attachment_value:
        raise ValidationError("Нужно заполнить хотя бы text или attachment_path")

    send_at = parse_datetime_input(send_at_raw, timezone)
    validate_schedule_window(send_at, utc_now().astimezone(timezone))

    attachment_path = resolve_input_path(attachment_value, project_root)
    if attachment_path is not None and not attachment_path.exists():
        raise ValidationError(f"Файл вложения не найден: {attachment_path}")

    return ImportMessageItem(
        text=text,
        send_at=send_at,
        attachment_path=attachment_path,
        disable_preview=disable_preview,
        source_name=source_name,
        source_row=row_number,
    )
