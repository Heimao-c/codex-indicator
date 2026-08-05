import unittest

from codex_indicator.models import SessionStatus
from codex_indicator.presentation import session_row, shorten
from codex_indicator.service import SessionView


class PresentationTests(unittest.TestCase):
    def test_uses_three_dots_for_long_names(self) -> None:
        self.assertEqual(shorten("123456789", 8), "12345...")

    def test_remote_row_is_short_and_identifies_host(self) -> None:
        session = SessionView(
            session_id="ssh:host:id",
            thread_id="id",
            status=SessionStatus.WORKING,
            project="robot-project",
            title="很长的对话名称" * 20,
            cwd="/root/robot-project",
            updated_at=1,
            source_host="robot-server",
        )
        row = session_row(session)
        self.assertIn("robot-s...:robot-pro...", row)
        self.assertTrue(row.endswith("..."))
        self.assertLess(len(row), 90)
