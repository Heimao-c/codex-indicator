import json
import os
import tempfile
import unittest
from pathlib import Path

from codex_indicator.models import SessionStatus
from codex_indicator.scanner import LinuxSessionScanner


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
            self.assertEqual(result[0].terminal_id, "GNOME_TERMINAL_SCREEN:/screen/test")


if __name__ == "__main__":
    unittest.main()
