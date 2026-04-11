from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from app.exceptions import AppError
from app.services.session_importer import SessionImporter


@dataclass(slots=True)
class SessionImportResult:
    source: Path
    destination: Path
    renamed: bool


class SessionManager:
    """Persists and resolves active Telegram .session file for GUI flow."""

    def __init__(self, *, session_dir: Path) -> None:
        self.session_dir = session_dir
        self.state_path = self.session_dir / "active_session.json"
        self.session_dir.mkdir(parents=True, exist_ok=True)

    def import_session(self, source_path: str | Path, *, overwrite: bool = False) -> SessionImportResult:
        source = SessionImporter.validate_source(source_path)
        destination = SessionImporter.copy_into_directory(source, self.session_dir, overwrite=overwrite)
        self.set_active_session(destination.name)
        return SessionImportResult(
            source=source,
            destination=destination,
            renamed=source.name != destination.name,
        )

    def set_active_session(self, session_file_name: str) -> Path:
        session_path = (self.session_dir / session_file_name).resolve()
        if session_path.suffix.lower() != ".session":
            raise AppError("Активная сессия должна иметь расширение .session")
        if not session_path.exists() or not session_path.is_file():
            raise AppError(f"Session-файл не найден: {session_path}")

        payload = {"active_session": session_path.name}
        self.state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return session_path

    def get_active_session_path(self) -> Path | None:
        if not self.state_path.exists():
            return None

        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise AppError(f"Файл состояния session повреждён: {self.state_path}") from exc

        session_name = str(payload.get("active_session", "")).strip()
        if not session_name:
            return None

        session_path = (self.session_dir / session_name).resolve()
        if not session_path.exists() or not session_path.is_file():
            return None
        return session_path

    def clear_active_session(self) -> None:
        if self.state_path.exists():
            self.state_path.unlink()

    def list_sessions(self) -> list[Path]:
        return sorted(path for path in self.session_dir.glob("*.session") if path.is_file())
