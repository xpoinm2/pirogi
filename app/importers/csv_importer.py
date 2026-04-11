from __future__ import annotations

import csv
from pathlib import Path
from zoneinfo import ZoneInfo

from app.exceptions import ValidationError
from app.importers.schemas import build_import_message
from app.models import ImportMessageItem


REQUIRED_COLUMNS = {"text", "send_at", "attachment_path", "disable_preview"}


def load_csv_messages(file_path: Path, *, timezone: ZoneInfo, project_root: Path) -> list[ImportMessageItem]:
    errors: list[str] = []
    items: list[ImportMessageItem] = []

    with file_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValidationError("CSV пустой или не содержит заголовок")

        missing = REQUIRED_COLUMNS.difference(set(reader.fieldnames))
        if missing:
            joined = ", ".join(sorted(missing))
            raise ValidationError(f"В CSV отсутствуют обязательные столбцы: {joined}")

        for row_number, row in enumerate(reader, start=2):
            try:
                item = build_import_message(
                    row,
                    source_name=file_path.name,
                    row_number=row_number,
                    timezone=timezone,
                    project_root=project_root,
                )
                items.append(item)
            except ValidationError as exc:
                errors.append(f"CSV строка {row_number}: {exc}")

    if errors:
        raise ValidationError("\n".join(errors))

    return items
