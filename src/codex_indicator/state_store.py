from __future__ import annotations

import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Mapping

from codex_indicator.models import SessionState, SessionStatus, status_for_event
from codex_indicator.paths import session_state_dir
from codex_indicator.processes import find_codex_ancestor, pid_is_alive, terminal_identity


SAFE_ID = re.compile(r"[^A-Za-z0-9._-]")


class StateStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or session_state_dir()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        return self.root / f"{SAFE_ID.sub('_', session_id)}.json"

    def record_hook(
        self,
        payload: Mapping[str, Any],
        *,
        pid: int | None = None,
        environment: dict[str, str] | None = None,
        now: float | None = None,
    ) -> SessionState | None:
        session_id = str(payload.get("session_id") or "").strip()
        event = str(payload.get("hook_event_name") or "").strip()
        if not session_id or not event:
            return None
        state = SessionState(
            session_id=session_id,
            status=status_for_event(event, payload),
            cwd=str(payload.get("cwd") or ""),
            event=event,
            updated_at=now if now is not None else time.time(),
            turn_id=str(payload["turn_id"]) if payload.get("turn_id") else None,
            pid=pid if pid is not None else find_codex_ancestor(),
            terminal_id=terminal_identity(environment),
            thread_id=session_id,
        )
        self.write(state)
        return state

    def write(self, state: SessionState) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        target = self._path(state.session_id)
        temp = target.with_name(f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        temp.write_text(json.dumps(state.to_dict(), ensure_ascii=False, sort_keys=True), encoding="utf-8")
        os.replace(temp, target)

    def list_states(
        self,
        *,
        include_closed: bool = False,
        stale_without_pid_seconds: float = 24 * 60 * 60,
        now: float | None = None,
    ) -> list[SessionState]:
        current_time = now if now is not None else time.time()
        states: list[SessionState] = []
        for path in self.root.glob("*.json"):
            try:
                state = SessionState.from_dict(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError, KeyError, TypeError):
                continue
            if state.status == SessionStatus.CLOSED and not include_closed:
                continue
            if state.pid and not state.source_host and not pid_is_alive(state.pid):
                continue
            if not state.pid and current_time - state.updated_at > stale_without_pid_seconds:
                continue
            states.append(state)
        return states

    def clear(self) -> None:
        for path in self.root.glob("*.json"):
            try:
                path.unlink()
            except FileNotFoundError:
                pass

    def delete(self, session_id: str) -> None:
        try:
            self._path(session_id).unlink()
        except FileNotFoundError:
            pass

    def prune_discovered(self, active_session_ids: set[str]) -> None:
        """Drop snapshots tied to terminals that are no longer live.

        Hook subprocesses can occasionally report the long-lived app-server PID instead of
        the TUI PID. A terminal identity plus absence from the passive process scan is the
        reliable closed-window signal in that case.
        """
        for path in self.root.glob("*.json"):
            try:
                state = SessionState.from_dict(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError, KeyError, TypeError):
                continue
            if state.session_id in active_session_ids:
                continue
            passive_snapshot = state.event in {"PassiveDiscovery", "RemoteDiscovery"}
            inactive_local_hook = (
                not state.source_host
                and state.terminal_id is not None
                and state.event not in {"PassiveDiscovery", "RemoteDiscovery"}
            )
            if not passive_snapshot and not inactive_local_hook:
                continue
            try:
                path.unlink()
            except FileNotFoundError:
                pass
