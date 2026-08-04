from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from pathlib import Path


def _looks_like_codex(name: str, command: str) -> bool:
    name = name.lower().strip()
    command = command.lower()
    if (
        "codex-indicator" in name
        or "codexindicator" in name
        or "codex-indicator" in command
        or "codexindicator" in command
    ):
        return False
    if name in {"codex", "codex.exe", "codex-cli", "codex-cli.exe"}:
        return True
    return "@openai/codex" in command or "/codex" in command or "\\codex" in command


def _linux_process_info(pid: int) -> tuple[int, str, str] | None:
    root = Path("/proc") / str(pid)
    try:
        status = (root / "status").read_text(encoding="utf-8", errors="replace")
        command = (root / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        return None
    parent = 0
    name = ""
    for line in status.splitlines():
        if line.startswith("Name:"):
            name = line.split(":", 1)[1].strip()
        elif line.startswith("PPid:"):
            parent = int(line.split(":", 1)[1].strip())
    return parent, name, command


def _unix_ps_process_info(pid: int) -> tuple[int, str, str] | None:
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "ppid=", "-o", "comm=", "-o", "command="],
            check=False,
            capture_output=True,
            text=True,
            timeout=1,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    fields = result.stdout.strip().split(None, 2)
    if len(fields) < 2:
        return None
    return int(fields[0]), fields[1], fields[2] if len(fields) > 2 else fields[1]


def _windows_parent_map() -> dict[int, int]:
    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", ctypes.c_ulong),
            ("cntUsage", ctypes.c_ulong),
            ("th32ProcessID", ctypes.c_ulong),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", ctypes.c_ulong),
            ("cntThreads", ctypes.c_ulong),
            ("th32ParentProcessID", ctypes.c_ulong),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", ctypes.c_ulong),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    kernel32 = ctypes.windll.kernel32
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        return {}
    mapping: dict[int, int] = {}
    names: dict[int, str] = {}
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(entry)
    try:
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            mapping[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
            names[int(entry.th32ProcessID)] = entry.szExeFile
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    _windows_parent_map.names = names  # type: ignore[attr-defined]
    return mapping


def find_codex_ancestor(start_pid: int | None = None) -> int | None:
    pid = start_pid or os.getppid()
    seen: set[int] = set()
    if sys.platform == "win32":
        mapping = _windows_parent_map()
        names = getattr(_windows_parent_map, "names", {})
        while pid > 0 and pid not in seen:
            seen.add(pid)
            name = names.get(pid, "")
            if _looks_like_codex(name, name):
                return pid
            pid = mapping.get(pid, 0)
        return None

    reader = _linux_process_info if sys.platform.startswith("linux") else _unix_ps_process_info
    while pid > 1 and pid not in seen:
        seen.add(pid)
        info = reader(pid)
        if not info:
            break
        parent, name, command = info
        if _looks_like_codex(name, command):
            return pid
        pid = parent
    return None


def pid_is_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    if sys.platform == "win32":
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        code = ctypes.c_ulong()
        try:
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, OSError):
        return False
    return True


def terminal_identity(environment: dict[str, str] | None = None) -> str | None:
    env = environment or dict(os.environ)
    for key in (
        "GNOME_TERMINAL_SCREEN",
        "WT_SESSION",
        "TERM_SESSION_ID",
        "KONSOLE_DBUS_SESSION",
        "TMUX_PANE",
        "WINDOWID",
    ):
        if env.get(key):
            return f"{key}:{env[key]}"
    return None
