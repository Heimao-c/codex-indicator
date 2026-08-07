from __future__ import annotations

import sys
from dataclasses import dataclass, replace

from cc_indicator import hooks
from cc_indicator.codex_control import CodexAppServerClient
from cc_indicator.metadata import MetadataResolver
from cc_indicator.models import STATUS_ORDER, SessionStatus
from cc_indicator.scanner import PassiveScanner
from cc_indicator.state_store import StateStore
from cc_indicator.terminal import launch_claude, launch_codex
from cc_indicator.terminal_window import (
    ApprovalBatchResult,
    TerminalApprovalController,
    TerminalWindowResolver,
    focus_terminal,
)


@dataclass(frozen=True)
class SessionView:
    session_id: str
    thread_id: str
    status: SessionStatus
    project: str
    title: str
    cwd: str
    updated_at: float
    source_host: str | None = None
    terminal_id: str | None = None
    pid: int | None = None
    window_id: int | None = None
    window_title: str | None = None
    manageable: bool = True
    tool: str = "codex"

    @property
    def location(self) -> str:
        return self.source_host or "本机"


class SessionService:
    def __init__(
        self,
        store: StateStore | None = None,
        metadata: MetadataResolver | None = None,
        scanner: PassiveScanner | None = None,
        windows: TerminalWindowResolver | None = None,
        approvals: TerminalApprovalController | None = None,
    ) -> None:
        self.store = store or StateStore()
        self.metadata = metadata or MetadataResolver()
        self.scanner = scanner or PassiveScanner()
        self.windows = windows or TerminalWindowResolver()
        self.approvals = approvals or TerminalApprovalController()

    def sessions(self) -> list[SessionView]:
        self.scanner.reconcile(self.store)
        views: list[SessionView] = []
        by_terminal = {}
        for state in self.store.list_states():
            key = state.terminal_id or state.session_id
            previous = by_terminal.get(key)
            if not previous or state.updated_at > previous.updated_at:
                by_terminal[key] = state
        for state in by_terminal.values():
            if self.store.is_hidden(state.session_id):
                continue
            thread_id = state.thread_id or state.session_id
            item = None if state.source_host else self.metadata.resolve(thread_id, state.cwd)
            views.append(
                SessionView(
                    session_id=state.session_id,
                    thread_id=thread_id,
                    status=state.status,
                    project=state.display_project or (item.project if item else "—"),
                    title=(
                        self.store.title_override(state.session_id)
                        or state.display_title
                        or (item.title if item else f"Session {thread_id[:8]}")
                    ),
                    cwd=item.cwd if item else state.cwd,
                    updated_at=state.updated_at,
                    source_host=state.source_host,
                    terminal_id=state.terminal_id,
                    pid=state.pid,
                    manageable=state.manageable,
                    tool=state.tool,
                )
            )
        views.sort(key=lambda item: (STATUS_ORDER[item.status], -item.updated_at, item.project.lower()))
        matched_windows = self.windows.match(views)
        views = [
            replace(
                item,
                status=(
                    SessionStatus.ATTENTION
                    if matched_windows.get(item.session_id) and matched_windows[item.session_id].needs_attention
                    else SessionStatus.WORKING
                    if matched_windows.get(item.session_id) and matched_windows[item.session_id].is_working
                    else item.status
                ),
                window_id=matched_windows[item.session_id].window_id if item.session_id in matched_windows else None,
                window_title=matched_windows[item.session_id].title if item.session_id in matched_windows else None,
            )
            for item in views
        ]
        views.sort(key=lambda item: (STATUS_ORDER[item.status], -item.updated_at, item.project.lower()))
        return views

    @staticmethod
    def counts(sessions: list[SessionView]) -> dict[SessionStatus, int]:
        counts = {status: 0 for status in SessionStatus}
        for session in sessions:
            counts[session.status] += 1
        return counts

    @staticmethod
    def fingerprint(sessions: list[SessionView]) -> tuple[tuple[object, ...], ...]:
        return tuple(
            (
                item.session_id,
                item.thread_id,
                item.status.value,
                item.project,
                item.title,
                item.source_host,
                item.window_id,
                item.manageable,
                item.tool,
                int(item.updated_at),
            )
            for item in sessions
        )

    def rename(self, session: SessionView, name: str) -> None:
        cleaned = " ".join(name.split()).strip()
        # The indicator keeps a local display title for every conversation;
        # Codex also renames the real conversation through its app-server API
        # when a real thread ID is known. Placeholder terminals (no exposed
        # rollout/conversation) and Claude sessions are local-only.
        self.store.set_title_override(session.session_id, cleaned)
        if session.tool == "codex" and session.manageable:
            CodexAppServerClient(session.source_host).rename(session.thread_id, cleaned)
            self.metadata.invalidate(session.thread_id)

    def archive(self, session: SessionView) -> None:
        if session.tool != "codex" or not session.manageable:
            # Claude Code has no archive API, and placeholder terminals expose
            # no real conversation ID: hide the conversation locally.
            self.store.set_hidden(session.session_id, True)
            return
        CodexAppServerClient(session.source_host).archive(session.thread_id)
        self.store.delete(session.session_id)
        self.store.set_title_override(session.session_id, None)

    def focus(self, session: SessionView) -> None:
        current = session
        if sys.platform.startswith("linux") and current.window_id is None:
            current = next(
                (item for item in self.sessions() if item.session_id == session.session_id),
                session,
            )
        focus_terminal(pid=current.pid, window_id=current.window_id)

    @property
    def supports_approvals(self) -> bool:
        return self.approvals.supported

    @property
    def claude_allow_all(self) -> bool:
        # On: local settings carry defaultMode=bypassPermissions and every
        # connected remote host with Claude settings is in the same state.
        remote = self.scanner.remote_claude_bypass_state()
        return hooks.claude_bypass_enabled() and all(remote.values())

    def set_claude_allow_all(self, enabled: bool) -> bool:
        changed = hooks.set_claude_bypass(enabled)
        if self.scanner.set_remote_claude_allow_all(enabled):
            changed = True
        return changed

    def approve_all(
        self,
        sessions: list[SessionView] | None = None,
        *,
        allow_high_risk: bool = False,
    ) -> ApprovalBatchResult:
        # The terminal-screen approval flow understands Codex panes; Claude
        # has no per-request approval API and is covered by the settings-based
        # claude_allow_all toggle instead.
        codex_sessions = [
            session
            for session in (sessions if sessions is not None else self.sessions())
            if session.tool == "codex"
        ]
        return self.approvals.approve_all(
            codex_sessions,
            allow_high_risk=allow_high_risk,
        )

    @staticmethod
    def new_terminal(session: SessionView | None = None, tool: str = "codex") -> None:
        target = session.tool if session else tool
        if target == "claude":
            launch_claude(cwd=session.cwd if session else None)
            return
        launch_codex(
            cwd=session.cwd if session else None,
            host=session.source_host if session else None,
        )
