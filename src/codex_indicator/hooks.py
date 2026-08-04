from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from codex_indicator.paths import codex_home


HOOK_ARGUMENT = "--codex-indicator-hook"
HOOK_STATUS_MESSAGE = "Codex Indicator: update local session status"
EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PermissionRequest",
    "PostToolUse",
    "PreCompact",
    "PostCompact",
    "SubagentStart",
    "SubagentStop",
    "Stop",
    "SessionEnd",
)


def _frozen_hook_helper() -> Path | None:
    executable = Path(sys.executable).resolve()
    suffix = ".exe" if sys.platform == "win32" else ""
    helper_name = f"CodexIndicatorHook{suffix}"
    candidates = [executable.with_name(helper_name)]
    for ancestor in list(executable.parents)[:5]:
        candidates.append(ancestor / "CodexIndicatorHook" / helper_name)
    return next((path for path in candidates if path.is_file()), None)


def application_argv() -> list[str]:
    if getattr(sys, "frozen", False):
        return [str(Path(sys.executable).resolve())]
    configured = os.environ.get("CODEX_INDICATOR_LAUNCHER")
    if configured and Path(configured).expanduser().is_file():
        return [str(Path(configured).expanduser().resolve())]
    found = shutil.which("codex-indicator")
    if found:
        return [str(Path(found).resolve())]
    return [sys.executable, "-m", "codex_indicator"]


def hook_argv() -> list[str]:
    if getattr(sys, "frozen", False):
        helper = _frozen_hook_helper()
        if helper:
            return [str(helper), HOOK_ARGUMENT]
    return [*application_argv(), HOOK_ARGUMENT]


def command_string(arguments: list[str] | None = None) -> str:
    args = arguments or hook_argv()
    return subprocess.list2cmdline(args) if sys.platform == "win32" else shlex.join(args)


def _is_ours(handler: Any) -> bool:
    return isinstance(handler, dict) and (
        HOOK_ARGUMENT in str(handler.get("command", ""))
        or handler.get("statusMessage") == HOOK_STATUS_MESSAGE
    )


def _remove_ours(document: dict[str, Any]) -> None:
    hooks = document.get("hooks")
    if not isinstance(hooks, dict):
        return
    for event in list(hooks):
        groups = hooks.get(event)
        if not isinstance(groups, list):
            continue
        retained_groups = []
        for group in groups:
            if not isinstance(group, dict):
                retained_groups.append(group)
                continue
            handlers = group.get("hooks")
            if not isinstance(handlers, list):
                retained_groups.append(group)
                continue
            retained_handlers = [handler for handler in handlers if not _is_ours(handler)]
            if retained_handlers:
                copied = dict(group)
                copied["hooks"] = retained_handlers
                retained_groups.append(copied)
        if retained_groups:
            hooks[event] = retained_groups
        else:
            del hooks[event]


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"hooks": {}}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    if "hooks" not in value:
        value["hooks"] = {}
    if not isinstance(value["hooks"], dict):
        raise ValueError(f"{path}: 'hooks' must be a JSON object")
    return value


def _atomic_write(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def install(home: Path | None = None, command: str | None = None) -> Path:
    root = home or codex_home()
    path = root / "hooks.json"
    original = path.read_bytes() if path.exists() else None
    document = _load(path)
    _remove_ours(document)
    handler_command = command or command_string()
    hooks = document["hooks"]
    for event in EVENTS:
        group: dict[str, Any] = {
            "hooks": [
                {
                    "type": "command",
                    "command": handler_command,
                    "timeout": 3,
                    "statusMessage": HOOK_STATUS_MESSAGE,
                }
            ]
        }
        if event == "SessionStart":
            group["matcher"] = "startup|resume|clear|compact"
        hooks.setdefault(event, []).append(group)
    rendered = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if original == rendered:
        return path
    if original is not None:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = path.with_name(f"hooks.json.codex-indicator-{stamp}.bak")
        if backup.exists():
            backup = path.with_name(f"hooks.json.codex-indicator-{stamp}-{os.getpid()}.bak")
        backup.write_bytes(original)
    _atomic_write(path, document)
    return path


def uninstall(home: Path | None = None) -> Path:
    path = (home or codex_home()) / "hooks.json"
    if not path.exists():
        return path
    document = _load(path)
    before = json.dumps(document, sort_keys=True)
    _remove_ours(document)
    if json.dumps(document, sort_keys=True) != before:
        _atomic_write(path, document)
    return path


def is_installed(home: Path | None = None) -> bool:
    path = (home or codex_home()) / "hooks.json"
    try:
        document = _load(path)
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    hooks = document.get("hooks", {})
    installed_events: set[str] = set()
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                continue
            if any(_is_ours(handler) for handler in group["hooks"]):
                installed_events.add(event)
                break
    return set(EVENTS).issubset(installed_events)
