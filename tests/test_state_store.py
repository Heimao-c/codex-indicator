import os
import tempfile
import unittest
from pathlib import Path

from codex_indicator.models import SessionStatus
from codex_indicator.state_store import StateStore


class StateStoreTests(unittest.TestCase):
    def test_records_and_reads_live_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = StateStore(Path(temp))
            state = store.record_hook(
                {
                    "session_id": "abc",
                    "hook_event_name": "UserPromptSubmit",
                    "cwd": "/workspace",
                    "turn_id": "turn-1",
                },
                pid=os.getpid(),
                environment={"GNOME_TERMINAL_SCREEN": "/screen/1"},
                now=100.0,
            )
            self.assertIsNotNone(state)
            self.assertEqual(state.status, SessionStatus.WORKING)
            self.assertEqual(state.terminal_id, "GNOME_TERMINAL_SCREEN:/screen/1")
            self.assertEqual(store.list_states(now=101.0)[0].session_id, "abc")

    def test_closed_session_is_hidden(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = StateStore(Path(temp))
            store.record_hook(
                {"session_id": "abc", "hook_event_name": "SessionEnd", "cwd": "/workspace"},
                pid=os.getpid(),
            )
            self.assertEqual(store.list_states(), [])
            self.assertEqual(len(store.list_states(include_closed=True)), 1)

    def test_missing_required_hook_fields_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = StateStore(Path(temp))
            self.assertIsNone(store.record_hook({"hook_event_name": "Stop"}))
            self.assertEqual(store.list_states(), [])


if __name__ == "__main__":
    unittest.main()
