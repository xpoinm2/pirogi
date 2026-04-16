from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4


@dataclass(slots=True)
class ProxyEntry:
    id: str
    title: str
    mode: str
    scheme: str
    host: str
    port: int
    username: str | None = None
    password: str | None = None

    @property
    def endpoint(self) -> str:
        return f"{self.scheme}://{self.host}:{self.port}"


class ProxyStore:
    def __init__(self, storage_path: Path) -> None:
        self.storage_path = storage_path
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> tuple[list[ProxyEntry], str | None]:
        if not self.storage_path.exists():
            return [], None

        payload = json.loads(self.storage_path.read_text(encoding="utf-8"))
        rows = payload.get("proxies", [])
        active_id = payload.get("active_id")
        entries: list[ProxyEntry] = []
        for row in rows:
            try:
                entries.append(
                    ProxyEntry(
                        id=str(row["id"]),
                        title=str(row["title"]),
                        mode=str(row["mode"]),
                        scheme=str(row["scheme"]),
                        host=str(row["host"]),
                        port=int(row["port"]),
                        username=(str(row.get("username")).strip() or None) if row.get("username") is not None else None,
                        password=(str(row.get("password")).strip() or None) if row.get("password") is not None else None,
                    )
                )
            except Exception:
                continue
        return entries, (active_id if any(item.id == active_id for item in entries) else None)

    def save(self, entries: list[ProxyEntry], active_id: str | None) -> None:
        payload = {
            "active_id": active_id if any(item.id == active_id for item in entries) else None,
            "proxies": [
                {
                    "id": item.id,
                    "title": item.title,
                    "mode": item.mode,
                    "scheme": item.scheme,
                    "host": item.host,
                    "port": item.port,
                    "username": item.username,
                    "password": item.password,
                }
                for item in entries
            ],
        }
        self.storage_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def create_entry(
        *,
        title: str,
        mode: str,
        scheme: str,
        host: str,
        port: int,
        username: str | None,
        password: str | None,
    ) -> ProxyEntry:
        return ProxyEntry(
            id=uuid4().hex,
            title=title,
            mode=mode,
            scheme=scheme,
            host=host,
            port=port,
            username=username,
            password=password,
        )
