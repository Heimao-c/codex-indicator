import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from cc_indicator.metadata import MetadataResolver, clean_title, project_name


class MetadataTests(unittest.TestCase):
    def test_reads_name_and_cwd_from_current_state_database(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            database = sqlite3.connect(home / "state_5.sqlite")
            database.execute(
                "CREATE TABLE threads (id TEXT PRIMARY KEY, name TEXT, title TEXT, first_user_message TEXT, cwd TEXT)"
            )
            database.execute(
                "INSERT INTO threads VALUES (?, ?, ?, ?, ?)",
                ("session-1", "Explicit conversation", "Generated title", "First prompt", str(home)),
            )
            database.commit()
            database.close()

            result = MetadataResolver(home, cache_seconds=0).resolve("session-1", "/fallback")
            self.assertEqual(result.title, "Explicit conversation")
            self.assertEqual(result.cwd, str(home))

    def test_falls_back_to_session_index(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            (home / "session_index.jsonl").write_text(
                json.dumps({"id": "session-2", "thread_name": "Indexed title"}) + "\n",
                encoding="utf-8",
            )
            result = MetadataResolver(home, cache_seconds=0).resolve("session-2", str(home))
            self.assertEqual(result.title, "Indexed title")

    def test_falls_back_to_claude_transcript_first_user_message(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "codex"
            home.mkdir()
            claude_dir = root / "claude"
            transcript = claude_dir / "projects" / "-home-phi-cc-indicator" / "claude-9.jsonl"
            transcript.parent.mkdir(parents=True)
            transcript.write_text(
                "\n".join(
                    json.dumps(record)
                    for record in (
                        {"type": "user", "isMeta": True, "message": {"role": "user", "content": "meta record"}},
                        {"type": "user", "message": {"role": "user", "content": "Build the widget"}},
                        {"type": "assistant", "message": {"role": "assistant", "content": [], "stop_reason": "end_turn"}},
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            result = MetadataResolver(home, claude_dir=claude_dir, cache_seconds=0).resolve(
                "claude-9", "/fallback"
            )
            self.assertEqual(result.title, "Build the widget")
            self.assertEqual(result.cwd, "/fallback")

    def test_claude_title_skips_slash_command_markup(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "codex"
            home.mkdir()
            claude_dir = root / "claude"
            transcript = claude_dir / "projects" / "-home-phi-cc-indicator" / "claude-9.jsonl"
            transcript.parent.mkdir(parents=True)
            transcript.write_text(
                "\n".join(
                    json.dumps(record)
                    for record in (
                        {
                            "type": "user",
                            "message": {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "text",
                                        "text": "<command-name>/model</command-name>\n"
                                        "            <command-message>model</command-message>\n"
                                        "            <command-args></command-args>",
                                    }
                                ],
                            },
                        },
                        {
                            "type": "user",
                            "message": {
                                "role": "user",
                                "content": (
                                    "<local-command-stdout>Set model to deepseek-v4-flash "
                                    "and saved as your default for new sessions</local-command-stdout>"
                                ),
                            },
                        },
                        {"type": "user", "message": {"role": "user", "content": "Real question"}},
                        {"type": "assistant", "message": {"role": "assistant", "content": []}},
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            result = MetadataResolver(home, claude_dir=claude_dir, cache_seconds=0).resolve(
                "claude-9", "/fallback"
            )
            self.assertEqual(result.title, "Real question")

    def test_claude_title_keeps_plain_text_beside_command_markup(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "codex"
            home.mkdir()
            claude_dir = root / "claude"
            transcript = claude_dir / "projects" / "-home-phi-cc-indicator" / "claude-9.jsonl"
            transcript.parent.mkdir(parents=True)
            transcript.write_text(
                json.dumps(
                    {
                        "type": "user",
                        "message": {
                            "role": "user",
                            "content": (
                                "<command-name>/compact</command-name> "
                                "<command-message>compact</command-message> now explain the plan"
                            ),
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            result = MetadataResolver(home, claude_dir=claude_dir, cache_seconds=0).resolve(
                "claude-9", "/fallback"
            )
            self.assertEqual(result.title, "now explain the plan")

    def test_project_uses_git_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "robot-project"
            nested = root / "src" / "navigation"
            nested.mkdir(parents=True)
            (root / ".git").mkdir()
            self.assertEqual(project_name(str(nested)), "robot-project")

    def test_title_is_single_line_and_bounded(self) -> None:
        self.assertEqual(clean_title("a\n\tb"), "a b")
        self.assertEqual(len(clean_title("x" * 200, limit=20)), 20)


if __name__ == "__main__":
    unittest.main()
