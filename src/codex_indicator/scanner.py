from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from codex_indicator.models import SessionState, SessionStatus
from codex_indicator.remote import LinuxRemoteScanner
from codex_indicator.state_store import StateStore


SESSION_ID = re.compile(
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\.jsonl$"
)


@dataclass(frozen=True)
class DiscoveredSession:
    session_id: str
    pid: int
    cwd: str
    terminal_id: str | None
    rollout_path: Path
    status: SessionStatus
    updated_at: float
    source_host: str | None = None
    title: str | None = None
    project: str | None = None

    @property
    def state_id(self) -> str:
        return f"ssh:{self.source_host}:{self.session_id}" if self.source_host else self.session_id


class LinuxSessionScanner:
    def __init__(self, proc_root: Path = Path("/proc")) -> None:
        self.proc_root = proc_root

    @staticmethod
    def _tail_lines(path: Path, maximum_bytes: int = 512 * 1024) -> list[str]:
        try:
            with path.open("rb") as source:
                source.seek(0, os.SEEK_END)
                size = source.tell()
                source.seek(max(0, size - maximum_bytes))
                data = source.read()
        except OSError:
            return []
        lines = data.decode("utf-8", "replace").splitlines()
        return lines[1:] if size > maximum_bytes and lines else lines

    @classmethod
    def infer_status(cls, rollout_path: Path) -> SessionStatus:
        meaningful = SessionStatus.WORKING
        pending_user_input: str | None = None
        completed_calls: set[str] = set()
        for line in cls._tail_lines(rollout_path):
            try:
                record = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            payload = record.get("payload") if isinstance(record, dict) else None
            if not isinstance(payload, dict):
                continue
            payload_type = str(payload.get("type") or "")
            if record.get("type") == "event_msg":
                if payload_type == "task_complete" or payload_type in {"turn_aborted", "task_cancelled"}:
                    meaningful = SessionStatus.DONE
                    pending_user_input = None
                elif payload_type in {"task_started", "user_message"}:
                    meaningful = SessionStatus.WORKING
                    pending_user_input = None
            if record.get("type") == "response_item":
                if payload_type in {"custom_tool_call", "function_call"}:
                    meaningful = SessionStatus.WORKING
                    if str(payload.get("name") or "") == "request_user_input":
                        pending_user_input = str(payload.get("call_id") or payload.get("id") or "pending")
                elif payload_type in {"custom_tool_call_output", "function_call_output"}:
                    call_id = str(payload.get("call_id") or "")
                    if call_id:
                        completed_calls.add(call_id)
                    if pending_user_input and (pending_user_input == "pending" or pending_user_input in completed_calls):
                        pending_user_input = None
                        meaningful = SessionStatus.WORKING
        return SessionStatus.ATTENTION if pending_user_input else meaningful

    def _same_user(self, process_root: Path) -> bool:
        if not hasattr(os, "getuid"):
            return True
        try:
            status = (process_root / "status").read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        for line in status.splitlines():
            if line.startswith("Uid:"):
                try:
                    return int(line.split()[1]) == os.getuid()
                except (IndexError, ValueError):
                    return False
        return True

    @staticmethod
    def _terminal_from_tty(process_root: Path) -> str | None:
        try:
            target = os.readlink(process_root / "fd" / "0")
        except OSError:
            return None
        return f"TTY:{target}" if target.startswith("/dev/pts/") else None

    @staticmethod
    def _terminal_from_environ(path: Path) -> str | None:
        try:
            values = path.read_bytes().split(b"\0")
        except OSError:
            return None
        wanted = {
            "GNOME_TERMINAL_SCREEN",
            "WT_SESSION",
            "TERM_SESSION_ID",
            "KONSOLE_DBUS_SESSION",
            "TMUX_PANE",
            "WINDOWID",
        }
        for value in values:
            key, separator, raw = value.partition(b"=")
            name = key.decode("utf-8", "replace")
            if separator and name in wanted:
                return f"{name}:{raw.decode('utf-8', 'replace')}"
        return None

    @staticmethod
    def _is_root_rollout(path: Path) -> bool:
        try:
            with path.open("r", encoding="utf-8", errors="replace") as source:
                record = json.loads(source.readline())
        except (OSError, json.JSONDecodeError, TypeError):
            return True
        payload = record.get("payload") if isinstance(record, dict) else None
        if not isinstance(payload, dict):
            return True
        source = payload.get("source")
        return not payload.get("parent_thread_id") and not (
            isinstance(source, dict) and source.get("subagent")
        )

    @staticmethod
    def _rollout_from_fds(fd_root: Path) -> Path | None:
        try:
            entries = list(fd_root.iterdir())
        except OSError:
            return None
        candidates: list[tuple[float, Path, bool]] = []
        for entry in entries:
            try:
                target = Path(os.readlink(entry))
                modified = target.stat().st_mtime
            except OSError:
                continue
            if SESSION_ID.search(target.name) and "sessions" in target.parts:
                candidates.append((modified, target, LinuxSessionScanner._is_root_rollout(target)))
        roots = [item for item in candidates if item[2]]
        selected = max(roots or candidates, default=(0.0, None, True), key=lambda item: item[0])
        return selected[1]

    def discover(self) -> list[DiscoveredSession]:
        if sys.platform != "linux" and self.proc_root == Path("/proc"):
            return []
        by_terminal: dict[str, DiscoveredSession] = {}
        try:
            process_dirs = list(self.proc_root.iterdir())
        except OSError:
            return []
        for process_root in process_dirs:
            if not process_root.name.isdigit() or not self._same_user(process_root):
                continue
            try:
                name = (process_root / "comm").read_text(encoding="utf-8", errors="replace").strip().lower()
            except OSError:
                continue
            if name not in {"codex", "codex.exe", "codex-cli", "codex-cli.exe"}:
                continue
            terminal_id = self._terminal_from_tty(process_root)
            if not terminal_id:
                # app-server/IDE helper processes can hold several rollout files but
                # do not represent a terminal the user asked us to display.
                continue
            rollout = self._rollout_from_fds(process_root / "fd")
            if not rollout:
                continue
            match = SESSION_ID.search(rollout.name)
            if not match:
                continue
            try:
                cwd = os.readlink(process_root / "cwd")
                updated_at = rollout.stat().st_mtime
            except OSError:
                continue
            item = DiscoveredSession(
                session_id=match.group(1),
                pid=int(process_root.name),
                cwd=cwd,
                terminal_id=terminal_id,
                rollout_path=rollout,
                status=self.infer_status(rollout),
                updated_at=updated_at,
            )
            previous = by_terminal.get(terminal_id)
            if not previous or item.updated_at > previous.updated_at:
                by_terminal[terminal_id] = item
        return list(by_terminal.values())

    def reconcile(self, store: StateStore, remote: LinuxRemoteScanner | None = None) -> None:
        existing = {state.session_id: state for state in store.list_states(include_closed=True)}
        discovered = self.discover()
        if remote:
            for item in remote.discover():
                discovered.append(
                    DiscoveredSession(
                        session_id=item.session_id,
                        pid=item.pid,
                        cwd=item.cwd,
                        terminal_id=item.terminal_id,
                        rollout_path=Path("/remote") / item.session_id,
                        status=item.status,
                        updated_at=item.updated_at,
                        source_host=item.host,
                        title=item.title,
                        project=item.project,
                    )
                )
        active_ids = {item.state_id for item in discovered}
        store.prune_discovered(active_ids)
        for item in discovered:
            previous = existing.get(item.state_id)
            if (
                not item.source_host
                and previous
                and previous.event not in {"PassiveDiscovery", "RemoteDiscovery"}
                and previous.pid == item.pid
            ):
                # Official lifecycle hooks are more precise than rollout inference,
                # especially while Codex is paused at PermissionRequest.
                continue
            if (
                previous
                and previous.pid == item.pid
                and previous.status == item.status
                and previous.updated_at == item.updated_at
                and previous.terminal_id == item.terminal_id
                and previous.display_title == item.title
                and previous.display_project == item.project
            ):
                continue
            store.write(
                SessionState(
                    session_id=item.state_id,
                    status=item.status,
                    cwd=item.cwd,
                    event="RemoteDiscovery" if item.source_host else "PassiveDiscovery",
                    updated_at=item.updated_at,
                    pid=item.pid,
                    terminal_id=item.terminal_id,
                    thread_id=item.session_id,
                    source_host=item.source_host,
                    display_title=item.title,
                    display_project=item.project,
                )
            )


class PassiveScanner:
    def __init__(self, interval_seconds: float = 3.0) -> None:
        self.interval_seconds = interval_seconds
        self._last_scan = 0.0
        self._scanner = LinuxSessionScanner() if sys.platform == "linux" else None
        self._remote = LinuxRemoteScanner() if sys.platform == "linux" else None

    def reconcile(self, store: StateStore) -> None:
        if not self._scanner:
            return
        now = time.monotonic()
        if now - self._last_scan < self.interval_seconds:
            return
        self._last_scan = now
        self._scanner.reconcile(store, self._remote)
