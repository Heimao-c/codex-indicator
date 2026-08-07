from __future__ import annotations

import os
import sys
from pathlib import Path


# Display name; on-disk directory names below are kept stable so existing
# hooks, autostart entries, and state survive upgrades.
APP_NAME = "CC Indicator"
APP_ID = "com.heimaoc.codex-indicator"


def codex_home() -> Path:
    configured = os.environ.get("CODEX_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".codex"


def claude_home() -> Path:
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(configured).expanduser() if configured else Path.home() / ".claude"


def state_dir() -> Path:
    configured = os.environ.get("CODEX_INDICATOR_STATE_DIR")
    if configured:
        return Path(configured).expanduser()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "Codex Indicator"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Codex Indicator"
    base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return base / "codex-indicator"


def session_state_dir() -> Path:
    return state_dir() / "sessions"


def ensure_state_dirs() -> None:
    state_dir().mkdir(parents=True, exist_ok=True)
    session_state_dir().mkdir(parents=True, exist_ok=True)


def app_log_path() -> Path:
    ensure_state_dirs()
    return state_dir() / "app.log"


def hook_log_path() -> Path:
    ensure_state_dirs()
    return state_dir() / "hook.log"


def lock_path() -> Path:
    ensure_state_dirs()
    return state_dir() / "app.lock"


def linux_autostart_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "autostart" / "codex-indicator.desktop"


def linux_systemd_service_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "systemd" / "user" / "codex-indicator.service"


def macos_launch_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{APP_ID}.plist"
