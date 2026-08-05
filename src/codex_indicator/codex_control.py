from __future__ import annotations

import json
import queue
import shutil
import subprocess
import threading
import time
from typing import Any

from codex_indicator import __version__


class CodexControlError(RuntimeError):
    pass


class CodexAppServerClient:
    """Small one-request client for Codex's documented app-server protocol."""

    def __init__(self, host: str | None = None, timeout: float = 10.0) -> None:
        self.host = host
        self.timeout = timeout

    def _command(self) -> list[str]:
        if self.host:
            remote = "codex app-server --stdio"
            return [
                "ssh", "-T", "-o", "ClearAllForwardings=yes", "-o", "BatchMode=yes",
                "-o", "ConnectTimeout=3", "-o", "LogLevel=ERROR", self.host, remote,
            ]
        executable = shutil.which("codex")
        if not executable:
            raise CodexControlError("找不到 codex 命令")
        return [executable, "app-server", "--stdio"]

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            process = subprocess.Popen(
                self._command(),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise CodexControlError(str(error)) from error
        if not process.stdin or not process.stdout:
            process.kill()
            raise CodexControlError("无法连接 Codex app-server")
        responses: queue.Queue[dict[str, Any] | None] = queue.Queue()

        def read_stdout() -> None:
            assert process.stdout is not None
            for line in process.stdout:
                try:
                    value = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    continue
                if isinstance(value, dict):
                    responses.put(value)
            responses.put(None)

        threading.Thread(target=read_stdout, daemon=True).start()
        deadline = time.monotonic() + self.timeout

        def send(value: dict[str, Any]) -> None:
            process.stdin.write(json.dumps(value, ensure_ascii=False) + "\n")
            process.stdin.flush()

        def wait_for(request_id: int) -> dict[str, Any]:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise CodexControlError("Codex app-server 请求超时")
                try:
                    value = responses.get(timeout=remaining)
                except queue.Empty as error:
                    raise CodexControlError("Codex app-server 请求超时") from error
                if value is None:
                    message = ""
                    if process.stderr:
                        message = process.stderr.read().strip()
                    raise CodexControlError(message or "Codex app-server 提前退出")
                if value.get("id") == request_id:
                    return value

        try:
            send(
                {
                    "method": "initialize",
                    "id": 1,
                    "params": {
                        "clientInfo": {
                            "name": "codex_indicator",
                            "title": "Codex Indicator",
                            "version": __version__,
                        }
                    },
                }
            )
            initialized = wait_for(1)
            if initialized.get("error"):
                raise CodexControlError(str(initialized["error"]))
            send({"method": "initialized", "params": {}})
            send({"method": method, "id": 2, "params": params})
            response = wait_for(2)
        finally:
            try:
                process.stdin.close()
            except OSError:
                pass
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)
        if response.get("error"):
            error = response["error"]
            message = error.get("message") if isinstance(error, dict) else str(error)
            raise CodexControlError(str(message))
        result = response.get("result")
        return result if isinstance(result, dict) else {}

    def rename(self, thread_id: str, name: str) -> None:
        value = " ".join(name.split()).strip()
        if not value:
            raise CodexControlError("对话名称不能为空")
        self.request("thread/name/set", {"threadId": thread_id, "name": value})

    def archive(self, thread_id: str) -> None:
        self.request("thread/archive", {"threadId": thread_id})
