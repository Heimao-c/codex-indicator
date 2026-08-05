import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

from codex_indicator.models import SessionState, SessionStatus
from codex_indicator.scanner import LinuxSessionScanner
from codex_indicator.state_store import StateStore


@unittest.skipUnless(sys.platform.startswith("linux"), "requires Linux /proc semantics")
class LinuxScannerTests(unittest.TestCase):
    def test_infers_done_and_working_without_reading_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "rollout.jsonl"
            path.write_text(
                "\n".join(
                    (
                        json.dumps({"type": "event_msg", "payload": {"type": "task_started"}}),
                        json.dumps({"type": "response_item", "payload": {"type": "message", "content": "ignored"}}),
                        json.dumps({"type": "event_msg", "payload": {"type": "task_complete"}}),
                    )
                ),
                encoding="utf-8",
            )
            self.assertEqual(LinuxSessionScanner.infer_status(path), SessionStatus.DONE)
            path.write_text(
                json.dumps({"type": "event_msg", "payload": {"type": "task_started"}}) + "\n",
                encoding="utf-8",
            )
            self.assertEqual(LinuxSessionScanner.infer_status(path), SessionStatus.WORKING)

    def test_request_user_input_is_attention_until_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "rollout.jsonl"
            call = {
                "type": "response_item",
                "payload": {"type": "custom_tool_call", "name": "request_user_input", "call_id": "call-1"},
            }
            path.write_text(json.dumps(call) + "\n", encoding="utf-8")
            self.assertEqual(LinuxSessionScanner.infer_status(path), SessionStatus.ATTENTION)
            with path.open("a", encoding="utf-8") as output:
                output.write(
                    json.dumps(
                        {"type": "response_item", "payload": {"type": "custom_tool_call_output", "call_id": "call-1"}}
                    )
                    + "\n"
                )
            self.assertEqual(LinuxSessionScanner.infer_status(path), SessionStatus.WORKING)

    def test_discovers_codex_rollout_from_process_fds(self) -> None:
        session_id = "019fcc04-9328-70f2-a3e7-362473724c0d"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            proc = root / "proc"
            process = proc / "1234"
            (process / "fd").mkdir(parents=True)
            (process / "comm").write_text("codex\n", encoding="utf-8")
            uid = os.getuid() if hasattr(os, "getuid") else 0
            (process / "status").write_text(f"Name:\tcodex\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\n", encoding="utf-8")
            (process / "environ").write_bytes(b"GNOME_TERMINAL_SCREEN=/screen/test\0")
            (process / "fd" / "0").symlink_to("/dev/pts/42")
            workspace = root / "workspace"
            workspace.mkdir()
            (process / "cwd").symlink_to(workspace)
            rollout = root / "home" / ".codex" / "sessions" / f"rollout-2026-01-01-{session_id}.jsonl"
            rollout.parent.mkdir(parents=True)
            rollout.write_text(
                json.dumps({"type": "event_msg", "payload": {"type": "task_complete"}}) + "\n",
                encoding="utf-8",
            )
            (process / "fd" / "42").symlink_to(rollout)

            result = LinuxSessionScanner(proc).discover()
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0].session_id, session_id)
            self.assertEqual(result[0].status, SessionStatus.DONE)
            self.assertEqual(result[0].terminal_id, "TTY:/dev/pts/42")

    def test_discovers_live_tty_codex_without_rollout_as_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            process = root / "proc" / "1234"
            (process / "fd").mkdir(parents=True)
            (process / "comm").write_text("codex\n", encoding="utf-8")
            uid = os.getuid() if hasattr(os, "getuid") else 0
            (process / "status").write_text(f"Uid:\t{uid}\t{uid}\t{uid}\t{uid}\n", encoding="utf-8")
            (process / "fd" / "0").symlink_to("/dev/pts/43")
            workspace = root / "workspace"
            workspace.mkdir()
            (process / "cwd").symlink_to(workspace)

            result = LinuxSessionScanner(root / "proc").discover()

            self.assertEqual(len(result), 1)
            self.assertEqual(result[0].session_id, "process-1234-pts-43")
            self.assertEqual(result[0].status, SessionStatus.DONE)
            self.assertFalse(result[0].manageable)
            self.assertEqual(result[0].terminal_id, "TTY:/dev/pts/43")

    def test_binds_hook_state_to_live_tty_placeholder(self) -> None:
        session_id = "019fcc04-9328-70f2-a3e7-362473724c0d"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            process = root / "proc" / str(os.getpid())
            (process / "fd").mkdir(parents=True)
            (process / "comm").write_text("codex\n", encoding="utf-8")
            uid = os.getuid() if hasattr(os, "getuid") else 0
            (process / "status").write_text(f"Uid:\t{uid}\t{uid}\t{uid}\t{uid}\n", encoding="utf-8")
            (process / "fd" / "0").symlink_to("/dev/pts/43")
            workspace = root / "workspace"
            workspace.mkdir()
            (process / "cwd").symlink_to(workspace)
            store = StateStore(root / "state")
            store.write(
                SessionState(
                    session_id=session_id,
                    status=SessionStatus.ATTENTION,
                    cwd=str(workspace),
                    event="PermissionRequest",
                    updated_at=time.time(),
                    pid=os.getpid(),
                    terminal_id="GNOME_TERMINAL_SCREEN:app-server",
                    thread_id=session_id,
                )
            )

            LinuxSessionScanner(root / "proc").reconcile(store)

            state = store.list_states()[0]
            self.assertEqual(state.session_id, session_id)
            self.assertEqual(state.pid, os.getpid())
            self.assertEqual(state.terminal_id, "TTY:/dev/pts/43")
            self.assertEqual(state.status, SessionStatus.ATTENTION)
            self.assertEqual(state.event, "PermissionRequest")
            self.assertTrue(state.manageable)

    def test_ignores_headless_app_server_even_when_it_holds_a_rollout(self) -> None:
        session_id = "019fcc04-9328-70f2-a3e7-362473724c0d"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            process = root / "proc" / "1234"
            (process / "fd").mkdir(parents=True)
            (process / "comm").write_text("codex\n", encoding="utf-8")
            uid = os.getuid() if hasattr(os, "getuid") else 0
            (process / "status").write_text(f"Uid:\t{uid}\t{uid}\t{uid}\t{uid}\n", encoding="utf-8")
            (process / "fd" / "0").symlink_to("/dev/null")
            workspace = root / "workspace"
            workspace.mkdir()
            (process / "cwd").symlink_to(workspace)
            rollout = root / "sessions" / f"rollout-{session_id}.jsonl"
            rollout.parent.mkdir()
            rollout.write_text("{}\n", encoding="utf-8")
            (process / "fd" / "42").symlink_to(rollout)
            self.assertEqual(LinuxSessionScanner(root / "proc").discover(), [])

    def test_prefers_root_rollout_over_newer_subagent(self) -> None:
        main_id = "019fcc04-9328-70f2-a3e7-362473724c0d"
        child_id = "019fcc04-9328-70f2-a3e7-362473724c0e"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            process = root / "proc" / "1234"
            (process / "fd").mkdir(parents=True)
            (process / "comm").write_text("codex\n", encoding="utf-8")
            uid = os.getuid() if hasattr(os, "getuid") else 0
            (process / "status").write_text(f"Uid:\t{uid}\t{uid}\t{uid}\t{uid}\n", encoding="utf-8")
            (process / "fd" / "0").symlink_to("/dev/pts/42")
            workspace = root / "workspace"
            workspace.mkdir()
            (process / "cwd").symlink_to(workspace)
            sessions = root / "sessions"
            sessions.mkdir()
            main = sessions / f"rollout-{main_id}.jsonl"
            child = sessions / f"rollout-{child_id}.jsonl"
            main.write_text(json.dumps({"type": "session_meta", "payload": {"id": main_id, "source": "cli"}}) + "\n")
            child.write_text(
                json.dumps(
                    {
                        "type": "session_meta",
                        "payload": {
                            "id": child_id,
                            "parent_thread_id": main_id,
                            "source": {"subagent": {}},
                        },
                    }
                )
                + "\n"
            )
            os.utime(child, (time.time() + 10, time.time() + 10))
            (process / "fd" / "41").symlink_to(main)
            (process / "fd" / "42").symlink_to(child)
            result = LinuxSessionScanner(root / "proc").discover()
            self.assertEqual(result[0].session_id, main_id)

    def test_hook_attention_is_not_overwritten_by_passive_scan(self) -> None:
        session_id = "019fcc04-9328-70f2-a3e7-362473724c0d"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            process = root / "proc" / str(os.getpid())
            (process / "fd").mkdir(parents=True)
            (process / "comm").write_text("codex\n", encoding="utf-8")
            uid = os.getuid() if hasattr(os, "getuid") else 0
            (process / "status").write_text(f"Uid:\t{uid}\t{uid}\t{uid}\t{uid}\n", encoding="utf-8")
            (process / "fd" / "0").symlink_to("/dev/pts/42")
            workspace = root / "workspace"
            workspace.mkdir()
            (process / "cwd").symlink_to(workspace)
            rollout = root / "sessions" / f"rollout-{session_id}.jsonl"
            rollout.parent.mkdir()
            rollout.write_text(json.dumps({"type": "event_msg", "payload": {"type": "task_started"}}) + "\n")
            (process / "fd" / "42").symlink_to(rollout)
            store = StateStore(root / "state")
            store.write(
                SessionState(
                    session_id=session_id,
                    status=SessionStatus.ATTENTION,
                    cwd=str(workspace),
                    event="PermissionRequest",
                    updated_at=time.time(),
                    pid=os.getpid(),
                    thread_id=session_id,
                )
            )
            LinuxSessionScanner(root / "proc").reconcile(store)
            self.assertEqual(store.list_states()[0].status, SessionStatus.ATTENTION)
            self.assertEqual(store.list_states()[0].event, "PermissionRequest")


if __name__ == "__main__":
    unittest.main()
