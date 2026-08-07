from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path

from cc_indicator.models import SessionState, SessionStatus
from cc_indicator.paths import claude_home
from cc_indicator.remote import LinuxRemoteScanner
from cc_indicator.state_store import StateStore


SESSION_ID = re.compile(
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\.jsonl$"
)

CODEX_COMMS = {"codex", "codex.exe", "codex-cli", "codex-cli.exe"}
CLAUDE_COMMS = {"claude", "claude.exe"}
AGENT_COMMS = CODEX_COMMS | CLAUDE_COMMS

# A transcript is "active" when it was written within this window; Claude Code
# appends records at every message/tool boundary while a turn is in flight.
CLAUDE_ACTIVE_WINDOW_SECONDS = 20.0


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
    manageable: bool = True
    event: str | None = None
    thread_id: str | None = None
    tool: str = "codex"

    @property
    def state_id(self) -> str:
        return f"ssh:{self.source_host}:{self.session_id}" if self.source_host else self.session_id


class LinuxSessionScanner:
    def __init__(self, proc_root: Path = Path("/proc"), claude_dir: Path | None = None) -> None:
        self.proc_root = proc_root
        self.claude_home = claude_dir or claude_home()
        # Transcript metadata keyed by path; reused while the file mtime is
        # unchanged so idle transcripts are not re-read on every scan.
        self._transcript_cache: dict[Path, tuple[float, str, str]] = {}

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

    @classmethod
    def infer_claude_status(cls, transcript_path: Path, now: float | None = None) -> SessionStatus:
        """Infer Claude Code status from its transcript tail.

        Claude Code appends a record at every message/tool boundary while a turn
        is in flight, so a freshly written transcript means the session is busy.
        When it falls quiet, the final record of an idle session is the completed
        assistant message (stop_reason end_turn); a trailing user prompt or tool
        result record means a long generation is still in progress.
        """
        current = time.time() if now is None else now
        try:
            modified = transcript_path.stat().st_mtime
        except OSError:
            return SessionStatus.DONE
        if current - modified <= CLAUDE_ACTIVE_WINDOW_SECONDS:
            return SessionStatus.WORKING
        meaningful = SessionStatus.DONE
        for line in cls._tail_lines(transcript_path):
            try:
                record = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(record, dict):
                continue
            record_type = str(record.get("type") or "")
            if record_type == "user" and not record.get("isMeta"):
                meaningful = SessionStatus.WORKING
            elif record_type == "assistant":
                message = record.get("message")
                stop_reason = message.get("stop_reason") if isinstance(message, dict) else None
                meaningful = (
                    SessionStatus.WORKING if stop_reason == "tool_use" else SessionStatus.DONE
                )
        return meaningful

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
    def _process_started(process_root: Path) -> float:
        """Return a process start time, using a safe fallback for synthetic proc trees."""
        try:
            fields = (process_root / "stat").read_text(encoding="utf-8", errors="replace").split()
            started_ticks = int(fields[21])
            uptime = float((process_root.parent / "uptime").read_text(encoding="utf-8").split()[0])
            ticks_per_second = os.sysconf("SC_CLK_TCK")
            return time.time() - uptime + started_ticks / ticks_per_second
        except (OSError, IndexError, ValueError, TypeError, AttributeError):
            return time.time()

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

    @staticmethod
    def _claude_record_cwd(transcript_path: Path) -> str | None:
        """Return the session cwd carried by the transcript's latest records."""
        for line in LinuxSessionScanner._tail_lines(transcript_path):
            try:
                record = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(record, dict):
                continue
            cwd = record.get("cwd")
            if isinstance(cwd, str) and cwd:
                return cwd
        return None

    def _claude_transcripts(self) -> dict[Path, tuple[str, str]]:
        """Map main-session transcripts to (session_id, cwd), cached by mtime.

        Only top-level ~/.claude/projects/*/<session_id>.jsonl files are used;
        subagent and tool-result files live in per-session subdirectories.
        """
        result: dict[Path, tuple[str, str]] = {}
        try:
            project_dirs = list((self.claude_home / "projects").glob("*"))
        except OSError:
            return result
        for project_dir in project_dirs:
            try:
                paths = list(project_dir.glob("*.jsonl"))
            except OSError:
                continue
            for path in paths:
                match = SESSION_ID.search(path.name)
                if not match:
                    continue
                try:
                    modified = path.stat().st_mtime
                except OSError:
                    continue
                cached = self._transcript_cache.get(path)
                if cached is not None and cached[0] == modified:
                    session_id, cwd = cached[1], cached[2]
                else:
                    session_id = match.group(1)
                    cwd = self._claude_record_cwd(path) or ""
                    self._transcript_cache[path] = (modified, session_id, cwd)
                result[path] = (session_id, cwd)
        stale = [path for path in self._transcript_cache if path not in result]
        for path in stale:
            self._transcript_cache.pop(path, None)
        return result

    def _claude_transcript_for(
        self,
        cwd: str,
        process_started: float,
        claimed: set[str],
        now: float | None = None,
    ) -> Path | None:
        """Pick the transcript belonging to one live Claude Code terminal.

        A transcript created long before the process started is a leftover of an
        ended session. Among the rest, the terminal's current session is the
        newest one: /clear or /new in the same terminal starts a fresh
        transcript while the old one freezes. Freshly written transcripts win,
        then the newest mtime (creation time breaks ties), so a live session is
        never displaced by the ended transcript it replaced.
        """
        current = time.time() if now is None else now
        best: Path | None = None
        best_key = (False, 0.0, 0.0)
        for path, (session_id, transcript_cwd) in self._claude_transcripts().items():
            if transcript_cwd != cwd or session_id in claimed:
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            if stat.st_ctime + 60.0 < process_started:
                continue
            key = (
                current - stat.st_mtime <= CLAUDE_ACTIVE_WINDOW_SECONDS,
                stat.st_mtime,
                stat.st_ctime,
            )
            if key > best_key:
                best, best_key = path, key
        return best

    def discover(self) -> list[DiscoveredSession]:
        if sys.platform != "linux" and self.proc_root == Path("/proc"):
            return []
        by_terminal: dict[str, DiscoveredSession] = {}
        claude_terminals: list[tuple[Path, str, str, float]] = []
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
            if name not in AGENT_COMMS:
                continue
            terminal_id = self._terminal_from_tty(process_root)
            if not terminal_id:
                # app-server/IDE helper processes can hold several rollout files but
                # do not represent a terminal the user asked us to display.
                continue
            try:
                cwd = os.readlink(process_root / "cwd")
            except OSError:
                continue
            if name in CLAUDE_COMMS:
                # Claude Code does not keep its transcript open, so it cannot be
                # found through fds; bind by working directory instead.
                claude_terminals.append((process_root, terminal_id, cwd, self._process_started(process_root)))
                continue
            rollout = self._rollout_from_fds(process_root / "fd")
            match = SESSION_ID.search(rollout.name) if rollout else None
            if match:
                updated_at = rollout.stat().st_mtime
                session_id = match.group(1)
                status = self.infer_status(rollout)
                manageable = True
                rollout_path = rollout
            else:
                # Newer Codex versions may keep rollout files in the app-server
                # process instead of the TUI. Keep the live TTY visible as a
                # placeholder; reconcile() will bind it to the precise hook
                # session when one exists for the same working directory.
                updated_at = self._process_started(process_root)
                tty_name = terminal_id.removeprefix("TTY:/dev/").replace("/", "-")
                session_id = f"process-{process_root.name}-{tty_name}"
                status = SessionStatus.DONE
                manageable = False
                rollout_path = process_root / "no-rollout"
            item = DiscoveredSession(
                session_id=session_id,
                pid=int(process_root.name),
                cwd=cwd,
                terminal_id=terminal_id,
                rollout_path=rollout_path,
                status=status,
                updated_at=updated_at,
                manageable=manageable,
            )
            previous = by_terminal.get(terminal_id)
            if not previous or item.updated_at > previous.updated_at:
                by_terminal[terminal_id] = item
        # Oldest terminal claims the newest matching transcript so parallel
        # Claude sessions in one directory stay paired with the right terminal.
        claimed_sessions: set[str] = set()
        for process_root, terminal_id, cwd, started in sorted(
            claude_terminals, key=lambda item: item[3]
        ):
            transcript = self._claude_transcript_for(cwd, started, claimed_sessions)
            if transcript:
                match = SESSION_ID.search(transcript.name)
                assert match is not None
                claimed_sessions.add(match.group(1))
                updated_at = transcript.stat().st_mtime
                session_id = match.group(1)
                status = self.infer_claude_status(transcript)
                # A real session id is manageable (rename/archive apply locally
                # for Claude); placeholders below stay non-manageable.
                manageable = True
                rollout_path = transcript
            else:
                updated_at = started
                tty_name = terminal_id.removeprefix("TTY:/dev/").replace("/", "-")
                session_id = f"process-{process_root.name}-{tty_name}"
                status = SessionStatus.DONE
                manageable = False
                rollout_path = process_root / "no-rollout"
            item = DiscoveredSession(
                session_id=session_id,
                pid=int(process_root.name),
                cwd=cwd,
                terminal_id=terminal_id,
                rollout_path=rollout_path,
                status=status,
                updated_at=updated_at,
                manageable=manageable,
                tool="claude",
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
                        manageable=item.manageable,
                        event=None,
                        thread_id=item.session_id,
                        tool=item.tool,
                    )
                )

        # Hooks can be emitted by a long-lived app-server and therefore carry
        # its PID/terminal identity rather than the foreground TUI's. Bind a
        # no-rollout placeholder to the matching live hook state by cwd so the
        # current conversation remains visible and clickable.
        concrete_ids = {
            item.session_id
            for item in discovered
            if item.manageable and not item.source_host
        }
        hook_states = [
            state
            for state in existing.values()
            if not state.source_host
            and state.session_id not in concrete_ids
            and state.event not in {"PassiveDiscovery", "RemoteDiscovery"}
            and state.status != SessionStatus.CLOSED
            and state.cwd
        ]
        claimed: set[str] = set()
        resolved: list[DiscoveredSession] = []
        for item in discovered:
            if item.source_host or item.manageable:
                resolved.append(item)
                continue
            candidates = [
                state
                for state in hook_states
                if state.session_id not in claimed
                and state.cwd == item.cwd
                and state.tool == item.tool
            ]
            candidate = max(candidates, key=lambda state: state.updated_at, default=None)
            if candidate is None:
                resolved.append(item)
                continue
            claimed.add(candidate.session_id)
            resolved.append(
                replace(
                    item,
                    session_id=candidate.session_id,
                    status=candidate.status,
                    updated_at=candidate.updated_at,
                    title=candidate.display_title,
                    project=candidate.display_project,
                    # Binding attaches a real session identity, so the result is
                    # manageable for Claude too (rename/archive act locally).
                    manageable=True,
                    event=candidate.event,
                    thread_id=candidate.thread_id or candidate.session_id,
                )
            )
        discovered = resolved
        active_ids = {item.state_id for item in discovered}
        store.prune_discovered(active_ids)
        for item in discovered:
            previous = existing.get(item.state_id)
            if (
                not item.source_host
                and previous
                and previous.event not in {"PassiveDiscovery", "RemoteDiscovery"}
                and previous.pid == item.pid
                and (
                    previous.terminal_id is None
                    or previous.terminal_id == item.terminal_id
                    or item.event is None
                )
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
                and previous.manageable == item.manageable
                and previous.tool == item.tool
                and previous.event == (item.event or ("RemoteDiscovery" if item.source_host else "PassiveDiscovery"))
                and previous.thread_id == (item.thread_id or item.session_id)
            ):
                continue
            store.write(
                SessionState(
                    session_id=item.state_id,
                    status=item.status,
                    cwd=item.cwd,
                    event=item.event or ("RemoteDiscovery" if item.source_host else "PassiveDiscovery"),
                    updated_at=item.updated_at,
                    pid=item.pid,
                    terminal_id=item.terminal_id,
                    thread_id=item.thread_id or item.session_id,
                    source_host=item.source_host,
                    display_title=item.title,
                    display_project=item.project,
                    manageable=item.manageable,
                    tool=item.tool,
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

    def remote_claude_bypass_state(self) -> dict[str, bool]:
        return self._remote.claude_bypass_state() if self._remote else {}

    def set_remote_claude_allow_all(self, enabled: bool) -> int:
        return self._remote.set_claude_allow_all(enabled) if self._remote else 0
