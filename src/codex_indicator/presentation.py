from __future__ import annotations

from codex_indicator.i18n import COLOR_SYMBOLS, status_text
from codex_indicator.service import SessionView


def shorten(value: str, limit: int) -> str:
    cleaned = " ".join((value or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(1, limit - 3)].rstrip() + "..."


def session_location(session: SessionView) -> str:
    if session.source_host:
        return f"{shorten(session.source_host, 10)}:{shorten(session.project, 12)}"
    return shorten(session.project, 16)


def session_row(session: SessionView) -> str:
    return (
        f"{COLOR_SYMBOLS[session.status]} {status_text(session.status)} · "
        f"{session_location(session)} — {shorten(session.title, 12)}"
    )
