from __future__ import annotations

import csv
import json
from pathlib import Path

from app.exceptions import ValidationError


def load_relay_plan(file_path: Path) -> list[tuple[int, int]]:
    resolved = file_path.resolve()
    if not resolved.exists():
        raise ValidationError(f"Файл не найден: {resolved}")

    suffix = resolved.suffix.lower()
    if suffix == ".csv":
        return _load_csv_plan(resolved)
    if suffix == ".json":
        return _load_json_plan(resolved)
    raise ValidationError("План рассылки поддерживается только в CSV или JSON")


def _load_csv_plan(path: Path) -> list[tuple[int, int]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"message_id", "target_chat_id"}
        if not reader.fieldnames or not required.issubset({name.strip() for name in reader.fieldnames}):
            raise ValidationError("CSV должен содержать колонки: message_id,target_chat_id")

        plan: list[tuple[int, int]] = []
        for row_index, row in enumerate(reader, start=2):
            try:
                message_id = int((row.get("message_id") or "").strip())
                target_chat_id = int((row.get("target_chat_id") or "").strip())
            except ValueError as exc:
                raise ValidationError(f"Строка {row_index}: message_id и target_chat_id должны быть числами") from exc

            plan.append((message_id, target_chat_id))
        return plan


def _load_json_plan(path: Path) -> list[tuple[int, int]]:
    with path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)

    if not isinstance(raw, list):
        raise ValidationError("JSON план должен быть массивом объектов")

    plan: list[tuple[int, int]] = []
    for idx, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise ValidationError(f"Элемент {idx}: ожидается объект")
        try:
            message_id = int(item["message_id"])
            target_chat_id = int(item["target_chat_id"])
        except (KeyError, ValueError, TypeError) as exc:
            raise ValidationError(
                f"Элемент {idx}: нужны числовые поля message_id и target_chat_id"
            ) from exc
        plan.append((message_id, target_chat_id))
    return plan
