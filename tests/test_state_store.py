import os
import json
import tempfile
import unittest
from pathlib import Path

from codex_indicator.models import SessionState, SessionStatus
from codex_indicator.state_store import StateStore


class StateStoreTests(unittest.TestCase):
    def test_migrates_legacy_idle_state_to_done(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = StateStore(Path(temp))
            (Path(temp) / "legacy.json").write_text(
                json.dumps(
                    {
                        "session_id": "legacy",
                        "status": "idle",
                        "cwd": "/workspace",
                        "event": "SessionStart",
                        "updated_at": 1,
                        "pid": os.getpid(),
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(store.list_states()[0].status, SessionStatus.DONE)

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
            self.assertEqual(state.tool, "codex")
            self.assertTrue(state.manageable)
            self.assertEqual(store.list_states(now=101.0)[0].session_id, "abc")

    def test_claude_hook_records_are_manageable_and_tool_claude(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = StateStore(Path(temp))
            state = store.record_hook(
                {
                    "session_id": "claude-1",
                    "hook_event_name": "UserPromptSubmit",
                    "cwd": "/workspace",
                    "transcript_path": "/home/user/.claude/projects/-home-phi-codex-indicator/claude-1.jsonl",
                },
                pid=os.getpid(),
                now=100.0,
            )
            self.assertIsNotNone(state)
            self.assertEqual(state.tool, "claude")
            self.assertTrue(state.manageable)
            self.assertEqual(state.status, SessionStatus.WORKING)
            self.assertEqual(store.list_states()[0].tool, "claude")

    def test_title_override_and_hidden_survive_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = StateStore(root)
            store.set_title_override("session-1", "My title")
            store.set_hidden("session-2", True)
            reloaded = StateStore(root)
            self.assertEqual(reloaded.title_override("session-1"), "My title")
            self.assertTrue(reloaded.is_hidden("session-2"))
            self.assertIsNone(reloaded.title_override("session-2"))
            reloaded.set_title_override("session-1", None)
            self.assertIsNone(StateStore(root).title_override("session-1"))

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

    def test_prunes_stale_discovery_and_closed_hook_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = StateStore(Path(temp))
            for session_id, event, terminal_id in (
                ("active", "PassiveDiscovery", "TTY:/dev/pts/1"),
                ("stale", "PassiveDiscovery", "TTY:/dev/pts/2"),
                ("closed-hook", "Stop", "GNOME_TERMINAL_SCREEN:/screen/closed"),
                ("headless-hook", "Stop", None),
            ):
                store.write(
                    SessionState(
                        session_id=session_id,
                        status=SessionStatus.DONE,
                        cwd="/workspace",
                        event=event,
                        updated_at=1,
                        pid=os.getpid(),
                        terminal_id=terminal_id,
                    )
                )
            store.prune_discovered({"active"})
            ids = {state.session_id for state in store.list_states()}
            self.assertEqual(ids, {"active", "headless-hook"})


if __name__ == "__main__":
    unittest.main()
