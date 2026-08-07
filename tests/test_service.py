import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_indicator.models import SessionStatus
from codex_indicator.service import SessionService, SessionView
from codex_indicator.state_store import StateStore


def _view(
    session_id: str,
    tool: str = "codex",
    manageable: bool = True,
    thread_id: str | None = None,
) -> SessionView:
    return SessionView(
        session_id=session_id,
        thread_id=thread_id or session_id,
        status=SessionStatus.DONE,
        project="project",
        title="title",
        cwd="/workspace",
        updated_at=1,
        manageable=manageable,
        tool=tool,
    )


class SessionManageTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.store = StateStore(Path(self._temporary.name) / "state")
        self.service = SessionService(store=self.store)

    def test_rename_placeholder_keeps_local_override_and_skips_app_server(self) -> None:
        session = _view("process-1-pts-1", manageable=False)
        with patch("codex_indicator.service.CodexAppServerClient") as client:
            self.service.rename(session, "  My  title  ")
        client.assert_not_called()
        self.assertEqual(self.store.title_override(session.session_id), "My title")

    def test_rename_manageable_codex_calls_app_server(self) -> None:
        session = _view("thread-1", thread_id="thread-1")
        with patch("codex_indicator.service.CodexAppServerClient") as client:
            self.service.rename(session, "New name")
        client.return_value.rename.assert_called_once_with("thread-1", "New name")
        self.assertEqual(self.store.title_override(session.session_id), "New name")

    def test_archive_placeholder_and_claude_hide_locally(self) -> None:
        for session in (
            _view("process-1-pts-1", manageable=False),
            _view("claude-1", tool="claude"),
        ):
            with patch("codex_indicator.service.CodexAppServerClient") as client:
                self.service.archive(session)
            client.assert_not_called()
            self.assertTrue(self.store.is_hidden(session.session_id))

    def test_archive_manageable_codex_uses_app_server(self) -> None:
        session = _view("thread-1", thread_id="thread-1")
        with patch("codex_indicator.service.CodexAppServerClient") as client:
            self.service.archive(session)
        client.return_value.archive.assert_called_once_with("thread-1")
        self.assertFalse(self.store.is_hidden(session.session_id))

    def test_claude_allow_all_delegates_to_hooks_and_remote_hosts(self) -> None:
        with patch("codex_indicator.service.hooks.claude_bypass_enabled", return_value=True) as enabled:
            with patch.object(self.service.scanner, "remote_claude_bypass_state", return_value={}):
                self.assertTrue(self.service.claude_allow_all)
        enabled.assert_called_once_with()
        with patch.object(self.service.scanner, "set_remote_claude_allow_all", return_value=1) as remote:
            with patch("codex_indicator.service.hooks.set_claude_bypass", return_value=True) as toggle:
                self.assertTrue(self.service.set_claude_allow_all(True))
        toggle.assert_called_once_with(True)
        remote.assert_called_once_with(True)

    def test_claude_allow_all_requires_remote_bypass(self) -> None:
        with patch("codex_indicator.service.hooks.claude_bypass_enabled", return_value=True):
            with patch.object(
                self.service.scanner, "remote_claude_bypass_state", return_value={"host": False}
            ):
                self.assertFalse(self.service.claude_allow_all)


if __name__ == "__main__":
    unittest.main()
