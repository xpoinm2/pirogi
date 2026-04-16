from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from app.exceptions import ConfigError


PROJECT_ROOT = Path(__file__).resolve().parent.parent

FALLBACK_API_ID = 2040
FALLBACK_API_HASH = "b18441a1ff607e10a989891a5462e627"


def _load_env_files() -> None:
    candidates = (
        PROJECT_ROOT / "config" / ".env",
        PROJECT_ROOT / ".env",
    )
    for candidate in candidates:
        if candidate.exists():
            load_dotenv(candidate, override=False)


def _get_required(name: str) -> str:
    value = os.getenv(name, "").strip().strip("\"'")
    if not value:
        raise ConfigError(f"Не задано обязательное значение {name} в config/.env")
    return value

def _get_optional(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    cleaned = value.strip().strip("\"'")
    return cleaned or None

def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw.strip().strip("\"'"))
    except ValueError as exc:
        raise ConfigError(f"{name} должно быть целым числом") from exc


def _get_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw.strip().strip("\"'"))
    except ValueError as exc:
        raise ConfigError(f"{name} должно быть числом") from exc


def _resolve_path(value: str, *, default: str) -> Path:
    raw = os.getenv(value, default).strip()
    path = Path(raw)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


@dataclass(slots=True)
class Settings:
    api_id: int
    api_hash: str
    default_phone: str | None
    string_session: str | None
    session_name: str
    session_dir: Path
    database_path: Path
    log_dir: Path
    timezone_name: str
    log_level: str
    max_retries: int
    request_retries: int
    connection_retries: int
    retry_delay_seconds: float
    max_batch_size: int
    max_scheduled_per_chat: int
    dialog_fetch_limit: int
    project_root: Path = PROJECT_ROOT
    session_check_timeout_seconds: int

    @classmethod
    def load(cls) -> "Settings":
        _load_env_files()

        api_id = _get_int("API_ID", FALLBACK_API_ID)
        if api_id <= 0:
            raise ConfigError("API_ID должен быть положительным числом")

        api_hash = _get_optional("API_HASH") or FALLBACK_API_HASH
        timezone_name = os.getenv("TIMEZONE", "Europe/Vilnius").strip() or "Europe/Vilnius"

        try:
            ZoneInfo(timezone_name)
        except Exception as exc:
            raise ConfigError(f"Некорректная таймзона TIMEZONE: {timezone_name}") from exc

        settings = cls(
            api_id=api_id,
            api_hash=api_hash,
            default_phone=os.getenv("DEFAULT_PHONE", "").strip() or None,
            string_session=os.getenv("STRING_SESSION", "").strip() or None,
            session_name=os.getenv("SESSION_NAME", "telegram_manager").strip() or "telegram_manager",
            session_dir=_resolve_path("SESSION_DIR", default="data/sessions"),
            database_path=_resolve_path("DATABASE_PATH", default="data/telegram_manager.db"),
            log_dir=_resolve_path("LOG_DIR", default="logs"),
            timezone_name=timezone_name,
            log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO",
            max_retries=_get_int("MAX_RETRIES", 5),
            request_retries=_get_int("REQUEST_RETRIES", 3),
            connection_retries=_get_int("CONNECTION_RETRIES", 3),
            retry_delay_seconds=_get_float("RETRY_DELAY_SECONDS", 2.0),
            max_batch_size=_get_int("MAX_BATCH_SIZE", 100),
            max_scheduled_per_chat=_get_int("MAX_SCHEDULED_PER_CHAT", 100),
            dialog_fetch_limit=_get_int("DIALOG_FETCH_LIMIT", 200),
            session_check_timeout_seconds=_get_int("SESSION_CHECK_TIMEOUT_SECONDS", 25),
        )
        if settings.session_check_timeout_seconds <= 0:
            raise ConfigError("SESSION_CHECK_TIMEOUT_SECONDS должен быть положительным числом")
        settings.ensure_directories()
        return settings

    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)

    @property
    def session_path(self) -> Path:
        return self.session_dir / f"{self.session_name}.session"

    def ensure_directories(self) -> None:
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
