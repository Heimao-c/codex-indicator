from __future__ import annotations

import shlex
import shutil
import subprocess
import sys
from pathlib import Path


class TerminalLaunchError(RuntimeError):
    pass


def _remote_command(cwd: str) -> str:
    return f"cd -- {shlex.quote(cwd)} && exec codex"


def launch_codex(cwd: str | None = None, host: str | None = None) -> None:
    working_directory = str(Path(cwd or Path.home()).expanduser())
    if sys.platform.startswith("linux"):
        terminal = shutil.which("gnome-terminal")
        if terminal:
            if host:
                command = [terminal, "--", "ssh", "-t", host, _remote_command(working_directory)]
            else:
                command = [terminal, f"--working-directory={working_directory}", "--", "codex"]
            subprocess.Popen(command, start_new_session=True)
            return
        terminal = shutil.which("x-terminal-emulator")
        if terminal:
            child = ["ssh", "-t", host, _remote_command(working_directory)] if host else ["codex"]
            subprocess.Popen([terminal, "-e", *child], cwd=None if host else working_directory, start_new_session=True)
            return
        raise TerminalLaunchError("找不到 gnome-terminal 或 x-terminal-emulator")

    if sys.platform == "darwin":
        if host:
            shell_command = f"ssh -t {shlex.quote(host)} {shlex.quote(_remote_command(working_directory))}"
        else:
            shell_command = f"cd -- {shlex.quote(working_directory)} && exec codex"
        script = f'tell application "Terminal" to do script {json_string(shell_command)}'
        subprocess.Popen(["osascript", "-e", script])
        return

    if sys.platform == "win32":
        terminal = shutil.which("wt.exe") or shutil.which("wt")
        child = ["ssh", "-t", host, _remote_command(working_directory)] if host else ["codex"]
        if terminal:
            prefix = [terminal] if host else [terminal, "-d", working_directory]
            subprocess.Popen([*prefix, *child])
            return
        if host:
            subprocess.Popen(["cmd.exe", "/c", "start", "Codex", *child])
        else:
            subprocess.Popen(["cmd.exe", "/c", "start", "Codex", "/D", working_directory, "codex"])
        return

    raise TerminalLaunchError(f"不支持的系统：{sys.platform}")


def json_string(value: str) -> str:
    # AppleScript accepts JSON-style quoted strings for ordinary command text.
    import json

    return json.dumps(value)
