import json
import tempfile
import unittest
from pathlib import Path

from codex_indicator import hooks


class HookConfigTests(unittest.TestCase):
    def test_install_preserves_existing_hooks_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
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
            (home / "hooks.json").write_text(json.dumps(original), encoding="utf-8")
            hooks.install(home, command="/tmp/codex-indicator --codex-indicator-hook")
            hooks.install(home, command="/tmp/codex-indicator --codex-indicator-hook")

            result = json.loads((home / "hooks.json").read_text(encoding="utf-8"))
            stop_commands = [
                handler["command"]
                for group in result["hooks"]["Stop"]
                for handler in group["hooks"]
            ]
            self.assertEqual(stop_commands.count("python3 existing.py"), 1)
            self.assertEqual(stop_commands.count("/tmp/codex-indicator --codex-indicator-hook"), 1)
            self.assertTrue(hooks.is_installed(home))
            self.assertEqual(len(list(home.glob("*.bak"))), 1)

    def test_uninstall_removes_only_our_handlers(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            hooks.install(home, command="tool --codex-indicator-hook")
            document = json.loads((home / "hooks.json").read_text(encoding="utf-8"))
            document["hooks"]["Stop"].append(
                {"hooks": [{"type": "command", "command": "keep-me"}]}
            )
            (home / "hooks.json").write_text(json.dumps(document), encoding="utf-8")

            hooks.uninstall(home)
            result = json.loads((home / "hooks.json").read_text(encoding="utf-8"))
            self.assertFalse(hooks.is_installed(home))
            self.assertEqual(result["hooks"]["Stop"][0]["hooks"][0]["command"], "keep-me")

    def test_invalid_json_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            path = home / "hooks.json"
            path.write_text("not-json", encoding="utf-8")
            with self.assertRaises(json.JSONDecodeError):
                hooks.install(home, command="tool --codex-indicator-hook")
            self.assertEqual(path.read_text(encoding="utf-8"), "not-json")


if __name__ == "__main__":
    unittest.main()
