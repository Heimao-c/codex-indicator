from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from codex_indicator.paths import codex_home


WHITESPACE = re.compile(r"\s+")
CONTROL = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True)
class SessionMetadata:
    title: str
    project: str
    cwd: str


def clean_title(value: str | None, limit: int = 96) -> str:
    if not value:
        return ""
    cleaned = WHITESPACE.sub(" ", CONTROL.sub(" ", value)).strip()
    return cleaned if len(cleaned) <= limit else f"{cleaned[: limit - 1].rstrip()}…"


def project_name(cwd: str) -> str:
    if not cwd:
        return "—"
    current = Path(cwd).expanduser()
    try:
        current = current.resolve(strict=False)
    except OSError:
        pass
    candidate = current
    while True:
        try:
            if (candidate / ".git").exists():
                return candidate.name or str(candidate)
        except OSError:
            break
        if candidate.parent == candidate:
            break
        candidate = candidate.parent
    return current.name or str(current)


class MetadataResolver:
    def __init__(self, home: Path | None = None, cache_seconds: float = 3.0) -> None:
        self.home = home or codex_home()
        self.cache_seconds = cache_seconds
        self._cache: dict[str, tuple[float, SessionMetadata]] = {}

    def invalidate(self, session_id: str) -> None:
        self._cache.pop(session_id, None)

    def resolve(self, session_id: str, fallback_cwd: str) -> SessionMetadata:
        cached = self._cache.get(session_id)
        now = time.monotonic()
        if cached and now - cached[0] < self.cache_seconds:
            return cached[1]
        title, stored_cwd = self._from_sqlite(session_id)
        if not title:
            title = self._from_index(session_id)
        cwd = stored_cwd or fallback_cwd
        metadata = SessionMetadata(
            title=clean_title(title) or f"Session {session_id[:8]}",
            project=project_name(cwd),
            cwd=cwd,
        )
        self._cache[session_id] = (now, metadata)
        return metadata

    def _state_databases(self) -> list[Path]:
        def numeric_suffix(path: Path) -> int:
            try:
                return int(path.stem.rsplit("_", 1)[1])
            except (IndexError, ValueError):
                return -1

        return sorted(self.home.glob("state_*.sqlite"), key=numeric_suffix, reverse=True)

    def _from_sqlite(self, session_id: str) -> tuple[str, str]:
        for database in self._state_databases():
            try:
                uri = f"{database.resolve().as_uri()}?mode=ro"
                connection = sqlite3.connect(uri, uri=True, timeout=0.25)
                try:
                    columns = {row[1] for row in connection.execute("PRAGMA table_info(threads)")}
                    wanted = [name for name in ("name", "title", "first_user_message", "cwd") if name in columns]
                    if not wanted:
                        continue
                    row = connection.execute(
                        f"SELECT {', '.join(wanted)} FROM threads WHERE id = ? LIMIT 1",
                        (session_id,),
                    ).fetchone()
                finally:
                    connection.close()
            except (sqlite3.Error, OSError):
                continue
            if not row:
                continue
            values = dict(zip(wanted, row))
            title = next(
                (clean_title(str(values.get(key) or "")) for key in ("name", "title", "first_user_message") if values.get(key)),
                "",
            )
            return title, str(values.get("cwd") or "")
        return "", ""

    def _from_index(self, session_id: str) -> str:
        index = self.home / "session_index.jsonl"
        try:
            lines = index.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return ""
        title = ""
        for line in lines:
            try:
                value = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if str(value.get("id")) == session_id:
                title = clean_title(str(value.get("thread_name") or ""))
        return title
