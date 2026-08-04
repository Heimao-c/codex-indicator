from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Mapping


class SessionStatus(str, Enum):
    ATTENTION = "attention"
    WORKING = "working"
    IDLE = "idle"
    DONE = "done"
    UNKNOWN = "unknown"
    CLOSED = "closed"


STATUS_ORDER = {
    SessionStatus.ATTENTION: 0,
    SessionStatus.WORKING: 1,
    SessionStatus.DONE: 2,
    SessionStatus.IDLE: 3,
    SessionStatus.UNKNOWN: 4,
    SessionStatus.CLOSED: 5,
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
        return SessionStatus.WORKING if payload.get("source") == "compact" else SessionStatus.IDLE
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

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["status"] = self.status.value
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SessionState":
        pid = value.get("pid")
        return cls(
            session_id=str(value["session_id"]),
            status=SessionStatus(str(value.get("status", "unknown"))),
            cwd=str(value.get("cwd", "")),
            event=str(value.get("event", "")),
            updated_at=float(value.get("updated_at", 0.0)),
            turn_id=str(value["turn_id"]) if value.get("turn_id") else None,
            pid=int(pid) if pid else None,
            terminal_id=str(value["terminal_id"]) if value.get("terminal_id") else None,
        )
