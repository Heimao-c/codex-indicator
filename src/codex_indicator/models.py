from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Mapping


class SessionStatus(str, Enum):
    ATTENTION = "attention"
    WORKING = "working"
    DONE = "done"
    UNKNOWN = "unknown"
    CLOSED = "closed"


STATUS_ORDER = {
    SessionStatus.ATTENTION: 0,
    SessionStatus.WORKING: 1,
    SessionStatus.DONE: 2,
    SessionStatus.UNKNOWN: 3,
    SessionStatus.CLOSED: 4,
}


def status_for_event(event: str, payload: Mapping[str, Any]) -> SessionStatus:
    if event == "SessionEnd":
        return SessionStatus.CLOSED
    if event == "PermissionRequest":
        return SessionStatus.ATTENTION
    if event == "PreToolUse" and payload.get("tool_name") == "request_user_input":
        return SessionStatus.ATTENTION
    if event == "Stop":
        return SessionStatus.DONE
    if event == "SessionStart":
        return SessionStatus.WORKING if payload.get("source") == "compact" else SessionStatus.DONE
    if event in {
        "UserPromptSubmit",
        "PreToolUse",
        "PostToolUse",
        "PreCompact",
        "PostCompact",
        "SubagentStart",
        "SubagentStop",
    }:
        return SessionStatus.WORKING
    if event == "Notification":
        note_type = str(payload.get("notification_type") or payload.get("type") or "")
        return SessionStatus.ATTENTION if note_type == "permission_prompt" else SessionStatus.DONE
    return SessionStatus.UNKNOWN


@dataclass(frozen=True)
class SessionState:
    session_id: str
    status: SessionStatus
    cwd: str
    event: str
    updated_at: float
    turn_id: str | None = None
    pid: int | None = None
    terminal_id: str | None = None
    thread_id: str | None = None
    source_host: str | None = None
    display_title: str | None = None
    display_project: str | None = None
    manageable: bool = True
    tool: str = "codex"

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["status"] = self.status.value
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SessionState":
        pid = value.get("pid")
        raw_status = str(value.get("status", "unknown"))
        # Migrate state files written by versions that exposed "idle".
        if raw_status == "idle":
            raw_status = SessionStatus.DONE.value
        return cls(
            session_id=str(value["session_id"]),
            status=SessionStatus(raw_status),
            cwd=str(value.get("cwd", "")),
            event=str(value.get("event", "")),
            updated_at=float(value.get("updated_at", 0.0)),
            turn_id=str(value["turn_id"]) if value.get("turn_id") else None,
            pid=int(pid) if pid else None,
            terminal_id=str(value["terminal_id"]) if value.get("terminal_id") else None,
            thread_id=str(value["thread_id"]) if value.get("thread_id") else None,
            source_host=str(value["source_host"]) if value.get("source_host") else None,
            display_title=str(value["display_title"]) if value.get("display_title") else None,
            display_project=str(value["display_project"]) if value.get("display_project") else None,
            manageable=bool(value.get("manageable", True)),
            tool=str(value.get("tool") or "codex"),
        )
