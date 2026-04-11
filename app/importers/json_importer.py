from __future__ import annotations

import json
from pathlib import Path
from zoneinfo import ZoneInfo

from app.exceptions import ValidationError
from app.importers.schemas import build_import_message
from app.models import ImportMessageItem


def load_json_messages(file_path: Path, *, timezone: ZoneInfo, project_root: Path) -> list[ImportMessageItem]:
    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValidationError(f"Некорректный JSON: {exc}") from exc

    if not isinstance(payload, list):
        raise ValidationError("JSON должен содержать массив объектов")

    errors: list[str] = []
    items: list[ImportMessageItem] = []

    for index, row in enumerate(payload, start=1):
        if not isinstance(row, dict):
            errors.append(f"JSON элемент {index}: ожидается объект")
            continue

        try:
            item = build_import_message(
                row,
                source_name=file_path.name,
                row_number=index,
                timezone=timezone,
                project_root=project_root,
            )
            items.append(item)
        except ValidationError as exc:
            errors.append(f"JSON элемент {index}: {exc}")

    if errors:
        raise ValidationError("\n".join(errors))

    return items
