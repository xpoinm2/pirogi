from __future__ import annotations

import shutil
from pathlib import Path

from app.exceptions import AppError


class SessionImporter:
    """Handles validating and copying .session files into app storage."""

    @staticmethod
    def validate_source(source_path: str | Path) -> Path:
        source = Path(source_path).expanduser()
        if not source.exists():
            raise AppError(f"Session-файл не найден: {source}")
        if not source.is_file():
            raise AppError(f"Ожидался файл .session, но получен путь: {source}")
        if source.suffix.lower() != ".session":
            raise AppError("Можно выбрать только файл с расширением .session")
        return source.resolve()

    @staticmethod
    def copy_into_directory(source: Path, target_dir: Path, *, overwrite: bool = False) -> Path:
        target_dir.mkdir(parents=True, exist_ok=True)
        destination = target_dir / source.name

        if destination.exists() and not overwrite:
            destination = SessionImporter._next_available_path(destination)

        shutil.copy2(source, destination)
        return destination

    @staticmethod
    def _next_available_path(destination: Path) -> Path:
        base_name = destination.stem
        suffix = destination.suffix
        parent = destination.parent

        index = 1
        while True:
            candidate = parent / f"{base_name}_{index}{suffix}"
            if not candidate.exists():
                return candidate
            index += 1
