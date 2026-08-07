import json
import tempfile
import unittest
from pathlib import Path

from cc_indicator import hooks


class HookConfigTests(unittest.TestCase):
    @staticmethod
    def _homes(temp: str) -> tuple[Path, Path]:
        root = Path(temp)
        codex_dir = root / "codex"
        claude_dir = root / "claude"
        codex_dir.mkdir(parents=True)
        claude_dir.mkdir(parents=True)
        return codex_dir, claude_dir

    def test_install_writes_both_configs_preserves_existing_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            codex_dir, claude_dir = self._homes(temp)
            original = {
                "description": "user hooks",
                "hooks": {
                    "Stop": [
                        {
                            "hooks": [
                                {"type": "command", "command": "python3 existing.py", "timeout": 2}
                            ]
                        }
                    ]
                },
            }
            (codex_dir / "hooks.json").write_text(json.dumps(original), encoding="utf-8")
            (claude_dir / "settings.json").write_text(
                json.dumps({"env": {"ANTHROPIC_API_KEY": "sk-test"}, "model": "opus"}),
                encoding="utf-8",
            )
            hooks.install(codex_dir, claude_dir, command="/tmp/cc-indicator --cc-indicator-hook")
            hooks.install(codex_dir, claude_dir, command="/tmp/cc-indicator --cc-indicator-hook")

            result = json.loads((codex_dir / "hooks.json").read_text(encoding="utf-8"))
            stop_commands = [
                handler["command"]
                for group in result["hooks"]["Stop"]
                for handler in group["hooks"]
            ]
            self.assertEqual(stop_commands.count("python3 existing.py"), 1)
            self.assertEqual(stop_commands.count("/tmp/cc-indicator --cc-indicator-hook"), 1)
            settings = json.loads((claude_dir / "settings.json").read_text(encoding="utf-8"))
            self.assertEqual(settings["env"]["ANTHROPIC_API_KEY"], "sk-test")
            self.assertEqual(settings["model"], "opus")
            self.assertTrue(hooks.is_installed(codex_dir, claude_dir))
            self.assertEqual(len(list(codex_dir.glob("*.bak"))), 1)
            self.assertEqual(len(list(claude_dir.glob("*.bak"))), 1)

    def test_claude_settings_skip_permission_request_and_use_notification(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            codex_dir, claude_dir = self._homes(temp)
            hooks.install(codex_dir, claude_dir, command="tool --cc-indicator-hook")
            settings = json.loads((claude_dir / "settings.json").read_text(encoding="utf-8"))
            self.assertNotIn("PermissionRequest", settings["hooks"])
            ours = [
                group
                for group in settings["hooks"].get("Notification", [])
                if any(handler["command"] == "tool --cc-indicator-hook" for handler in group["hooks"])
            ]
            self.assertEqual([group.get("matcher") for group in ours], ["permission_prompt|idle_prompt"])
            session_start = [
                group
                for group in settings["hooks"].get("SessionStart", [])
                if any(handler["command"] == "tool --cc-indicator-hook" for handler in group["hooks"])
            ]
            self.assertEqual(
                [group.get("matcher") for group in session_start],
                ["startup|resume|clear|compact|fork"],
            )

    def test_uninstall_removes_only_our_handlers(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            codex_dir, claude_dir = self._homes(temp)
            hooks.install(codex_dir, claude_dir, command="tool --cc-indicator-hook")
            document = json.loads((codex_dir / "hooks.json").read_text(encoding="utf-8"))
            document["hooks"]["Stop"].append(
                {"hooks": [{"type": "command", "command": "keep-me"}]}
            )
            (codex_dir / "hooks.json").write_text(json.dumps(document), encoding="utf-8")

            hooks.uninstall(codex_dir, claude_dir)
            result = json.loads((codex_dir / "hooks.json").read_text(encoding="utf-8"))
            self.assertFalse(hooks.is_installed(codex_dir, claude_dir))
            self.assertEqual(result["hooks"]["Stop"][0]["hooks"][0]["command"], "keep-me")
            settings = json.loads((claude_dir / "settings.json").read_text(encoding="utf-8"))
            self.assertEqual(settings["hooks"], {})

    def test_invalid_json_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            codex_dir, claude_dir = self._homes(temp)
            path = codex_dir / "hooks.json"
            path.write_text("not-json", encoding="utf-8")
            with self.assertRaises(json.JSONDecodeError):
                hooks.install(codex_dir, claude_dir, command="tool --cc-indicator-hook")
            self.assertEqual(path.read_text(encoding="utf-8"), "not-json")

    def test_invalid_claude_settings_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            codex_dir, claude_dir = self._homes(temp)
            path = claude_dir / "settings.json"
            path.write_text("not-json", encoding="utf-8")
            with self.assertRaises(json.JSONDecodeError):
                hooks.install(codex_dir, claude_dir, command="tool --cc-indicator-hook")
            self.assertEqual(path.read_text(encoding="utf-8"), "not-json")

    def test_claude_bypass_toggle_preserves_rest_and_reverts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            _, claude_dir = self._homes(temp)
            path = claude_dir / "settings.json"
            path.write_text(
                json.dumps(
                    {
                        "env": {"ANTHROPIC_API_KEY": "sk-test"},
                        "permissions": {"defaultMode": "acceptEdits", "deny": ["Bash(rm -rf /)"]},
                    }
                ),
                encoding="utf-8",
            )
            self.assertFalse(hooks.claude_bypass_enabled(claude_dir))
            self.assertTrue(hooks.set_claude_bypass(True, claude_dir))
            document = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(document["permissions"]["defaultMode"], "bypassPermissions")
            self.assertEqual(document["permissions"]["deny"], ["Bash(rm -rf /)"])
            self.assertEqual(document["env"]["ANTHROPIC_API_KEY"], "sk-test")
            self.assertTrue(hooks.claude_bypass_enabled(claude_dir))
            self.assertFalse(hooks.set_claude_bypass(True, claude_dir))  # no-op when already on
            self.assertTrue(hooks.set_claude_bypass(False, claude_dir))
            document = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotIn("defaultMode", document["permissions"])
            self.assertEqual(document["permissions"]["deny"], ["Bash(rm -rf /)"])
            self.assertFalse(hooks.claude_bypass_enabled(claude_dir))

    def test_claude_bypass_creates_file_and_noops_when_nothing_changed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            _, claude_dir = self._homes(temp)
            self.assertFalse(hooks.set_claude_bypass(False, claude_dir))
            self.assertFalse((claude_dir / "settings.json").exists())
            self.assertTrue(hooks.set_claude_bypass(True, claude_dir))
            self.assertTrue(hooks.claude_bypass_enabled(claude_dir))
            (claude_dir / "settings.json").write_text(
                json.dumps({"permissions": {"deny": []}}), encoding="utf-8"
            )
            self.assertFalse(hooks.set_claude_bypass(False, claude_dir))
            document = json.loads((claude_dir / "settings.json").read_text(encoding="utf-8"))
            self.assertNotIn("defaultMode", document["permissions"])
            self.assertTrue(hooks.claude_bypass_enabled(claude_dir) is False)

    def test_invalid_claude_settings_not_overwritten_by_bypass(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            _, claude_dir = self._homes(temp)
            path = claude_dir / "settings.json"
            path.write_text("not-json", encoding="utf-8")
            with self.assertRaises(json.JSONDecodeError):
                hooks.set_claude_bypass(True, claude_dir)
            self.assertEqual(path.read_text(encoding="utf-8"), "not-json")
            self.assertFalse(hooks.claude_bypass_enabled(claude_dir))


if __name__ == "__main__":
    unittest.main()
