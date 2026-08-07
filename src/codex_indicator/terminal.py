from __future__ import annotations

import shlex
import shutil
import subprocess
import sys
from pathlib import Path


class TerminalLaunchError(RuntimeError):
    pass


def _launch_terminal(
    cwd: str | None,
    host: str | None,
    command: str,
    window_title: str,
) -> None:
    working_directory = str(Path(cwd or Path.home()).expanduser())
    remote_command = f"cd -- {shlex.quote(working_directory)} && exec {command}"
    if sys.platform.startswith("linux"):
        terminal = shutil.which("gnome-terminal")
        if terminal:
            if host:
                launch = [terminal, "--", "ssh", "-t", host, remote_command]
            else:
                launch = [terminal, f"--working-directory={working_directory}", "--", command]
            subprocess.Popen(launch, start_new_session=True)
            return
        terminal = shutil.which("x-terminal-emulator")
        if terminal:
            child = ["ssh", "-t", host, remote_command] if host else [command]
            subprocess.Popen(
                [terminal, "-e", *child],
                cwd=None if host else working_directory,
                start_new_session=True,
            )
            return
        raise TerminalLaunchError("找不到 gnome-terminal 或 x-terminal-emulator")

    if sys.platform == "darwin":
        if host:
            shell_command = f"ssh -t {shlex.quote(host)} {shlex.quote(remote_command)}"
        else:
            shell_command = f"cd -- {shlex.quote(working_directory)} && exec {command}"
        script = f'tell application "Terminal" to do script {json_string(shell_command)}'
        subprocess.Popen(["osascript", "-e", script])
        return

    if sys.platform == "win32":
        terminal = shutil.which("wt.exe") or shutil.which("wt")
        child = ["ssh", "-t", host, remote_command] if host else [command]
        if terminal:
            prefix = [terminal] if host else [terminal, "-d", working_directory]
            subprocess.Popen([*prefix, *child])
            return
        if host:
            subprocess.Popen(["cmd.exe", "/c", "start", window_title, *child])
        else:
            subprocess.Popen(["cmd.exe", "/c", "start", window_title, "/D", working_directory, command])
        return

    raise TerminalLaunchError(f"不支持的系统：{sys.platform}")


def launch_codex(cwd: str | None = None, host: str | None = None) -> None:
    _launch_terminal(cwd, host, command="codex", window_title="Codex")


def launch_claude(cwd: str | None = None) -> None:
    _launch_terminal(cwd, None, command="claude", window_title="Claude")


def json_string(value: str) -> str:
    # AppleScript accepts JSON-style quoted strings for ordinary command text.
    import json

    return json.dumps(value)
