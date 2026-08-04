from __future__ import annotations

from dataclasses import dataclass

from codex_indicator.metadata import MetadataResolver
from codex_indicator.models import STATUS_ORDER, SessionStatus
from codex_indicator.scanner import PassiveScanner
from codex_indicator.state_store import StateStore


@dataclass(frozen=True)
class SessionView:
    session_id: str
    status: SessionStatus
    project: str
    title: str
    cwd: str
    updated_at: float


class SessionService:
    def __init__(
        self,
        store: StateStore | None = None,
        metadata: MetadataResolver | None = None,
        scanner: PassiveScanner | None = None,
    ) -> None:
        self.store = store or StateStore()
        self.metadata = metadata or MetadataResolver()
        self.scanner = scanner or PassiveScanner()

    def sessions(self) -> list[SessionView]:
        self.scanner.reconcile(self.store)
        views: list[SessionView] = []
        for state in self.store.list_states():
            item = self.metadata.resolve(state.session_id, state.cwd)
            views.append(
                SessionView(
                    session_id=state.session_id,
                    status=state.status,
                    project=item.project,
                    title=item.title,
                    cwd=item.cwd,
                    updated_at=state.updated_at,
                )
            )
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
            (item.session_id, item.status.value, item.project, item.title, int(item.updated_at))
            for item in sessions
        )
