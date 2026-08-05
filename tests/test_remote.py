import unittest

from codex_indicator.remote import ssh_target


class RemoteScannerTests(unittest.TestCase):
    def test_extracts_ssh_alias_after_options(self) -> None:
        self.assertEqual(
            ssh_target(["ssh", "-p", "2222", "-o", "BatchMode=yes", "robot-server"]),
            "robot-server",
        )

    def test_extracts_user_at_host(self) -> None:
        self.assertEqual(ssh_target(["/usr/bin/ssh", "root@10.0.0.2"]), "root@10.0.0.2")
