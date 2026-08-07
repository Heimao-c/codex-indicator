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

    @staticmethod
    def _make_agent_process(root: Path, pid: int, comm: str, tty: str, workspace: Path) -> None:
        process = root / "proc" / str(pid)
        (process / "fd").mkdir(parents=True)
        (process / "comm").write_text(f"{comm}\n", encoding="utf-8")
        uid = os.getuid() if hasattr(os, "getuid") else 0
        (process / "status").write_text(f"Uid:\t{uid}\t{uid}\t{uid}\t{uid}\n", encoding="utf-8")
        (process / "fd" / "0").symlink_to(f"/dev/pts/{tty}")
        workspace.mkdir(exist_ok=True)
        (process / "cwd").symlink_to(workspace)

    def test_infer_claude_status_by_freshness_and_tail_records(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "session.jsonl"
            assistant_end = {
                "type": "assistant",
                "message": {"role": "assistant", "content": [], "stop_reason": "end_turn"},
            }
            now = time.time()

            def write(records: list[dict]) -> None:
                path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
                os.utime(path, (now - 600, now - 600))

            write([assistant_end])
            self.assertEqual(LinuxSessionScanner.infer_claude_status(path, now=now), SessionStatus.DONE)
            os.utime(path, (now - 1, now - 1))
            self.assertEqual(LinuxSessionScanner.infer_claude_status(path, now=now), SessionStatus.WORKING)
            # A trailing user prompt means a long generation is still in flight.
            write([assistant_end, {"type": "user", "message": {"role": "user", "content": "why"}}])
            self.assertEqual(LinuxSessionScanner.infer_claude_status(path, now=now), SessionStatus.WORKING)
            # A trailing tool-use assistant message means the tool loop is busy.
            write(
                [
                    assistant_end,
                    {"type": "assistant", "message": {"role": "assistant", "content": [], "stop_reason": "tool_use"}},
                ]
            )
            self.assertEqual(LinuxSessionScanner.infer_claude_status(path, now=now), SessionStatus.WORKING)
            # Meta user records (attachments, permission notes) are not prompts.
            write([assistant_end, {"type": "user", "isMeta": True, "message": {"role": "user", "content": "..."}}])
            self.assertEqual(LinuxSessionScanner.infer_claude_status(path, now=now), SessionStatus.DONE)

    def test_discovers_claude_transcript_by_cwd(self) -> None:
        session_id = "019fcc04-9328-70f2-a3e7-362473724c0d"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            self._make_agent_process(root, 1234, "claude", "42", workspace)
            claude_dir = root / "claude-home"
            transcript = claude_dir / "projects" / "-home-phi-codex-indicator" / f"{session_id}.jsonl"
            transcript.parent.mkdir(parents=True)
            now = time.time()
            transcript.write_text(
                "\n".join(
                    json.dumps(record)
                    for record in (
                        {"type": "user", "cwd": str(workspace), "message": {"role": "user", "content": "hello"}},
                        {"type": "assistant", "message": {"role": "assistant", "content": [], "stop_reason": "end_turn"}},
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            os.utime(transcript, (now - 600, now - 600))

            result = LinuxSessionScanner(root / "proc", claude_dir=claude_dir).discover()

            self.assertEqual(len(result), 1)
            item = result[0]
            self.assertEqual(item.session_id, session_id)
            self.assertEqual(item.tool, "claude")
            self.assertTrue(item.manageable)
            self.assertEqual(item.status, SessionStatus.DONE)
            self.assertEqual(item.terminal_id, "TTY:/dev/pts/42")
            self.assertEqual(item.cwd, str(workspace))

    def test_claude_clear_keeps_live_transcript_not_stale_preclear_one(self) -> None:
        """After /clear in one terminal the ended transcript must not displace
        the live one, even though the process start time predates both."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            self._make_agent_process(root, 1234, "claude", "45", workspace)
            claude_dir = root / "claude-home"
            project = claude_dir / "projects" / "-home-phi-codex-indicator"
            project.mkdir(parents=True)
            now = time.time()
            stale = project / "11111111-1111-4111-8111-111111111111.jsonl"
            stale.write_text(
                json.dumps(
                    {"type": "assistant", "message": {"role": "assistant", "content": [], "stop_reason": "end_turn"}}
                )
                + "\n",
                encoding="utf-8",
            )
            os.utime(stale, (now - 3600, now - 3600))
            live = project / "22222222-2222-4222-8222-222222222222.jsonl"
            live.write_text(
                json.dumps(
                    {"type": "user", "cwd": str(workspace), "message": {"role": "user", "content": "keep going"}}
                )
                + "\n",
                encoding="utf-8",
            )
            os.utime(live, (now - 2, now - 2))

            result = LinuxSessionScanner(root / "proc", claude_dir=claude_dir).discover()

            self.assertEqual(len(result), 1)
            self.assertEqual(result[0].session_id, "22222222-2222-4222-8222-222222222222")
            self.assertEqual(result[0].status, SessionStatus.WORKING)
            self.assertEqual(result[0].terminal_id, "TTY:/dev/pts/45")

    def test_parallel_claude_sessions_in_one_directory_both_discovered(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            self._make_agent_process(root, 1234, "claude", "46", workspace)
            self._make_agent_process(root, 1235, "claude", "47", workspace)
            claude_dir = root / "claude-home"
            project = claude_dir / "projects" / "-home-phi-codex-indicator"
            project.mkdir(parents=True)
            now = time.time()
            for session_id, offset in (
                ("11111111-1111-4111-8111-111111111111", 5.0),
                ("22222222-2222-4222-8222-222222222222", 1.0),
            ):
                transcript = project / f"{session_id}.jsonl"
                transcript.write_text(
                    json.dumps(
                        {"type": "user", "cwd": str(workspace), "message": {"role": "user", "content": "hi"}}
                    )
                    + "\n",
                    encoding="utf-8",
                )
                os.utime(transcript, (now - offset, now - offset))

            result = LinuxSessionScanner(root / "proc", claude_dir=claude_dir).discover()

            self.assertEqual(len(result), 2)
            self.assertEqual(
                {item.session_id for item in result},
                {"11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"},
            )

    def test_claude_without_transcript_is_a_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            self._make_agent_process(root, 1234, "claude", "43", workspace)

            result = LinuxSessionScanner(root / "proc", claude_dir=root / "claude-home").discover()

            self.assertEqual(len(result), 1)
            self.assertEqual(result[0].session_id, "process-1234-pts-43")
            self.assertEqual(result[0].tool, "claude")
            self.assertFalse(result[0].manageable)
            self.assertEqual(result[0].status, SessionStatus.DONE)

    def test_ignores_subagent_and_tool_result_transcripts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            claude_dir = root / "claude-home"
            project = claude_dir / "projects" / "-home-phi-codex-indicator"
            for directory in ("subagents", "tool-results"):
                (project / directory).mkdir(parents=True)
                (project / directory / "019fcc04-9328-70f2-a3e7-362473724c0d.jsonl").write_text(
                    "{}\n", encoding="utf-8"
                )

            self.assertEqual(
                LinuxSessionScanner(root / "proc", claude_dir=claude_dir).discover(),
                [],
            )

    def test_claude_hook_state_binds_to_claude_placeholder(self) -> None:
        session_id = "019fcc04-9328-70f2-a3e7-362473724c0d"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            self._make_agent_process(root, os.getpid(), "claude", "43", workspace)
            store = StateStore(root / "state")
            store.write(
                SessionState(
                    session_id=session_id,
                    status=SessionStatus.ATTENTION,
                    cwd=str(workspace),
                    event="Notification",
                    updated_at=time.time(),
                    pid=os.getpid(),
                    terminal_id="GNOME_TERMINAL_SCREEN:app-server",
                    thread_id=session_id,
                    tool="claude",
                )
            )

            LinuxSessionScanner(root / "proc", claude_dir=root / "claude-home").reconcile(store)

            state = store.list_states()[0]
            self.assertEqual(state.session_id, session_id)
            self.assertEqual(state.pid, os.getpid())
            self.assertEqual(state.terminal_id, "TTY:/dev/pts/43")
            self.assertEqual(state.status, SessionStatus.ATTENTION)
            self.assertEqual(state.tool, "claude")
            self.assertTrue(state.manageable)

    def test_codex_hook_state_does_not_bind_to_claude_placeholder(self) -> None:
        session_id = "019fcc04-9328-70f2-a3e7-362473724c0d"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            self._make_agent_process(root, os.getpid(), "claude", "44", workspace)
            store = StateStore(root / "state")
            store.write(
                SessionState(
                    session_id=session_id,
                    status=SessionStatus.WORKING,
                    cwd=str(workspace),
                    event="UserPromptSubmit",
                    updated_at=time.time(),
                    pid=os.getpid(),
                    thread_id=session_id,
                    tool="codex",
                )
            )

            LinuxSessionScanner(root / "proc", claude_dir=root / "claude-home").reconcile(store)

            # The claude placeholder stays a placeholder (no binding), and the
            # codex hook session keeps its own identity: both are visible.
            states = {state.session_id: state for state in store.list_states()}
            self.assertEqual(len(states), 2)
            placeholder = states[f"process-{os.getpid()}-pts-44"]
            self.assertEqual(placeholder.tool, "claude")
            self.assertFalse(placeholder.manageable)
            codex_state = states[session_id]
            self.assertEqual(codex_state.tool, "codex")
            self.assertEqual(codex_state.status, SessionStatus.WORKING)


if __name__ == "__main__":
    unittest.main()
