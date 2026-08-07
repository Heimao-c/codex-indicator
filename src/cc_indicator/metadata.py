from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from cc_indicator.paths import claude_home, codex_home


WHITESPACE = re.compile(r"\s+")
CONTROL = re.compile(r"[\x00-\x1f\x7f]")
# Claude Code records slash-command invocations and their echoed output as user
# text blocks carrying XML-like markup (<command-name>/model</command-name>…,
# <local-command-stdout>…</local-command-stdout>). It is CLI machinery, not a
# conversation title, so it is stripped before extraction.
CLAUDE_UI_MARKUP = re.compile(
    r"<(command-name|command-message|command-args|local-command-stdout)[^>]*>.*?</\1>",
    re.DOTALL,
)


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
    def __init__(
        self,
        home: Path | None = None,
        claude_dir: Path | None = None,
        cache_seconds: float = 3.0,
    ) -> None:
        self.home = home or codex_home()
        self.claude_dir = claude_dir or claude_home()
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
        if not title:
            title = self._from_claude_transcript(session_id)
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

    def _from_claude_transcript(self, session_id: str) -> str:
        # Claude Code transcripts live at <claude_home>/projects/<encoded-cwd>/<session_id>.jsonl.
        # The first real user message serves as the conversation title, mirroring codex's
        # first_user_message. Claude Code has no rename/archive API, so this is read-only.
        try:
            candidates = sorted(self.claude_dir.glob(f"projects/*/{session_id}.jsonl"))
        except OSError:
            return ""
        if not candidates:
            return ""
        try:
            lines = candidates[0].read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return ""
        for line in lines:
            try:
                record = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(record, dict) or record.get("type") != "user" or record.get("isMeta"):
                continue
            message = record.get("message")
            if not isinstance(message, dict):
                continue
            cleaned = clean_title(_message_text(message))
            if cleaned:
                return cleaned
        return ""


def _message_text(message: dict) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return CLAUDE_UI_MARKUP.sub(" ", content)
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
        return CLAUDE_UI_MARKUP.sub(" ", "\n".join(parts))
    return ""
