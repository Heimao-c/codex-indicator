from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import sys

from codex_indicator import __version__, autostart, hooks
from codex_indicator.app import run as run_app
from codex_indicator.paths import hook_log_path
from codex_indicator.service import SessionService
from codex_indicator.state_store import StateStore


def _hook_logging() -> None:
    handler = logging.handlers.RotatingFileHandler(
        hook_log_path(), maxBytes=256 * 1024, backupCount=1, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s: %(message)s"))
    logger = logging.getLogger()
    logger.setLevel(logging.WARNING)
    logger.handlers.clear()
    logger.addHandler(handler)


def handle_hook() -> int:
    _hook_logging()
    try:
        payload = json.load(sys.stdin)
        if isinstance(payload, dict):
            StateStore().record_hook(payload)
    except Exception:
        logging.exception("Hook event could not be recorded")
    # Stop/SubagentStop hooks accept JSON. Empty JSON is also harmless for other events.
    if sys.stdout is not None:
        sys.stdout.write("{}\n")
        sys.stdout.flush()
    return 0


def dump_status() -> int:
    sessions = SessionService().sessions()
    payload = [
        {
            "session_id": item.session_id,
            "thread_id": item.thread_id,
            "status": item.status.value,
            "project": item.project,
            "title": item.title,
            "cwd": item.cwd,
            "source": item.source_host or "local",
            "terminal_id": item.terminal_id,
            "window_id": hex(item.window_id) if item.window_id is not None else None,
            "window_title": item.window_title,
            "updated_at": item.updated_at,
        }
        for item in sessions
    ]
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def doctor() -> int:
    report = {
        "version": __version__,
        "hooks_installed": hooks.is_installed(),
        "autostart_enabled": autostart.is_enabled(),
        "hook_command": hooks.command_string(),
        "visible_sessions": len(SessionService().sessions()),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Minimal tray status for Codex CLI sessions")
    parser.add_argument("--version", action="version", version=__version__)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument(hooks.HOOK_ARGUMENT, action="store_true", dest="hook")
    actions.add_argument("--hook", action="store_true", dest="hook")
    actions.add_argument("--install-hooks", action="store_true")
    actions.add_argument("--uninstall-hooks", action="store_true")
    actions.add_argument("--install-autostart", action="store_true")
    actions.add_argument("--uninstall-autostart", action="store_true")
    actions.add_argument("--dump-status", action="store_true")
    actions.add_argument("--doctor", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.hook:
        return handle_hook()
    if args.install_hooks:
        print(hooks.install())
        return 0
    if args.uninstall_hooks:
        print(hooks.uninstall())
        return 0
    if args.install_autostart:
        autostart.enable()
        return 0
    if args.uninstall_autostart:
        autostart.disable()
        return 0
    if args.dump_status:
        return dump_status()
    if args.doctor:
        return doctor()
    return run_app()
