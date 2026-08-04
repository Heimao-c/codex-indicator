from __future__ import annotations

import locale

from codex_indicator.models import SessionStatus


def is_chinese() -> bool:
    language = (locale.getlocale()[0] or "").lower()
    return language.startswith("zh")


ZH = {
    "attention": "等待操作",
    "working": "运行中",
    "idle": "空闲",
    "done": "已完成",
    "unknown": "未知",
    "closed": "已关闭",
    "no_sessions": "暂无活动 Codex 会话",
    "hooks_install": "安装/修复 Codex Hooks",
    "hooks_installed": "Codex Hooks 已安装",
    "autostart": "开机自动启动",
    "refresh": "立即刷新",
    "quit": "退出",
    "about": "Codex Indicator",
    "header": "状态 · 项目 — 对话名称",
}

EN = {
    "attention": "Needs attention",
    "working": "Working",
    "idle": "Idle",
    "done": "Done",
    "unknown": "Unknown",
    "closed": "Closed",
    "no_sessions": "No active Codex sessions",
    "hooks_install": "Install/repair Codex hooks",
    "hooks_installed": "Codex hooks installed",
    "autostart": "Start at login",
    "refresh": "Refresh now",
    "quit": "Quit",
    "about": "Codex Indicator",
    "header": "Status · project — conversation",
}

SYMBOLS = {
    SessionStatus.ATTENTION: "◐",
    SessionStatus.WORKING: "●",
    SessionStatus.IDLE: "○",
    SessionStatus.DONE: "✓",
    SessionStatus.UNKNOWN: "?",
    SessionStatus.CLOSED: "×",
}


def text(key: str) -> str:
    return (ZH if is_chinese() else EN).get(key, key)


def status_text(status: SessionStatus) -> str:
    return text(status.value)
