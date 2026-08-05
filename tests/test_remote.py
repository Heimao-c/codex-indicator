import tempfile
import unittest
from pathlib import Path

from codex_indicator.remote import _socket_source_port, ssh_target


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
