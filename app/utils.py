from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.exceptions import ValidationError


SUPPORTED_DT_FORMATS: tuple[str, ...] = (
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%dT%H:%M:%S",
)


def utc_now() -> datetime:
    return datetime.now(UTC)


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value

    raw = str(value or "").strip().lower()
    if raw in {"1", "true", "yes", "y", "on"}:
        return True
    if raw in {"0", "false", "no", "n", "off", ""}:
        return False

    raise ValidationError(f"Не удалось распознать булево значение: {value!r}")


def parse_datetime_input(value: str, timezone: ZoneInfo) -> datetime:
    raw = (value or "").strip()
    if not raw:
        raise ValidationError("Поле send_at пустое")

    normalized = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        parsed = None

    if parsed is None:
        for fmt in SUPPORTED_DT_FORMATS:
            try:
                parsed = datetime.strptime(raw, fmt)
                break
            except ValueError:
                continue

    if parsed is None:
        raise ValidationError(
            "Поле send_at должно быть в ISO-формате, например "
            "'2026-05-01 10:30:00' или '2026-05-01T10:30:00+03:00'"
        )

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone)

    return parsed.astimezone(timezone)


def validate_schedule_window(send_at: datetime, now: datetime, max_days: int = 365) -> None:
    if send_at <= now:
        raise ValidationError("Поле send_at должно быть в будущем")

    if send_at > now + timedelta(days=max_days):
        raise ValidationError(
            f"Поле send_at слишком далеко в будущем. Максимум: около {max_days} дней"
        )


def resolve_input_path(path_value: str | None, project_root: Path) -> Path | None:
    raw = (path_value or "").strip()
    if not raw:
        return None

    path = Path(raw)
    if not path.is_absolute():
        path = project_root / path

    return path.resolve()


def truncate_text(value: str | None, max_length: int = 60) -> str:
    if not value:
        return ""
    if len(value) <= max_length:
        return value
    return value[: max_length - 1] + "…"


def format_dt(value: datetime, timezone: ZoneInfo) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(timezone).strftime("%Y-%m-%d %H:%M:%S %Z")


def prompt_yes_no(prompt: str, default: bool = False) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    answer = input(f"{prompt} {suffix}: ").strip().lower()
    if not answer:
        return default
    return answer in {"y", "yes", "1", "true"}
