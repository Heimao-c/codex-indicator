import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from cc_indicator.remote import REMOTE_CLAUDE_TOGGLE, _socket_source_port, ssh_target


class RemoteScannerTests(unittest.TestCase):
    def test_extracts_ssh_alias_after_options(self) -> None:
        self.assertEqual(
            ssh_target(["ssh", "-p", "2222", "-o", "BatchMode=yes", "robot-server"]),
            "robot-server",
        )

    def test_extracts_user_at_host(self) -> None:
        self.assertEqual(ssh_target(["/usr/bin/ssh", "root@10.0.0.2"]), "root@10.0.0.2")

    def test_reads_ssh_source_port_from_process_socket(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            process = Path(temp) / "123"
            (process / "fd").mkdir(parents=True)
            (process / "net").mkdir()
            (process / "fd" / "3").symlink_to("socket:[45678]")
            header = "sl local_address rem_address st tx_queue rx_queue tr tm->when retrnsmt uid timeout inode"
            row = "0: 0100007F:CFAE 0100007F:0016 01 0:0 0:0 0 1000 0 45678"
            (process / "net" / "tcp").write_text(f"{header}\n{row}\n", encoding="utf-8")
            self.assertEqual(_socket_source_port(process), 0xCFAE)

    @staticmethod
    def _run_toggle(settings_dir: Path, *args: str) -> None:
        environment = dict(os.environ, CLAUDE_CONFIG_DIR=str(settings_dir))
        subprocess.run(
            ["python3", "-c", REMOTE_CLAUDE_TOGGLE, *args],
            check=True,
            capture_output=True,
            env=environment,
        )

    def test_remote_toggle_script_sets_and_removes_bypass(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            settings_dir = Path(temp) / "claude-cc"
            settings = settings_dir / "settings.json"
            settings.parent.mkdir(parents=True)
            settings.write_text(
                json.dumps(
                    {
                        "env": {"ANTHROPIC_AUTH_TOKEN": "sk-test"},
                        "permissions": {"defaultMode": "acceptEdits", "deny": ["Bash(rm -rf /)"]},
                    }
                ),
                encoding="utf-8",
            )

            self._run_toggle(settings_dir, "on")
            document = json.loads(settings.read_text(encoding="utf-8"))
            self.assertEqual(document["permissions"]["defaultMode"], "bypassPermissions")
            self.assertEqual(document["permissions"]["deny"], ["Bash(rm -rf /)"])
            self.assertEqual(document["env"]["ANTHROPIC_AUTH_TOKEN"], "sk-test")

            self._run_toggle(settings_dir, "off")
            document = json.loads(settings.read_text(encoding="utf-8"))
            self.assertNotIn("defaultMode", document["permissions"])
            self.assertEqual(document["permissions"]["deny"], ["Bash(rm -rf /)"])
            self.assertEqual(document["env"]["ANTHROPIC_AUTH_TOKEN"], "sk-test")

    def test_remote_toggle_script_creates_missing_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            settings_dir = Path(temp) / "claude-cc"
            self._run_toggle(settings_dir, "on")
            document = json.loads((settings_dir / "settings.json").read_text(encoding="utf-8"))
            self.assertEqual(document["permissions"]["defaultMode"], "bypassPermissions")
            self._run_toggle(settings_dir, "off")
            document = json.loads((settings_dir / "settings.json").read_text(encoding="utf-8"))
            self.assertNotIn("permissions", document)
