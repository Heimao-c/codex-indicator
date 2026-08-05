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
    "header": "点击对话跳转终端 · 状态 · 项目 — 名称",
    "local": "本机",
    "new_terminal": "新建本机 Codex 终端",
    "new_here": "在此项目新建 Codex 终端",
    "rename": "修改对话名称…",
    "rename_title": "修改 Codex 对话名称",
    "rename_prompt": "新的对话名称",
    "rename_success": "对话名称已更新",
    "archive": "归档对话…",
    "archive_title": "归档 Codex 对话",
    "archive_confirm": "确定归档“{title}”吗？\n正在运行的对应终端可能会退出。",
    "archive_success": "对话已归档",
    "cancel": "取消",
    "save": "保存",
    "remote": "服务器",
    "manage": "管理对话",
    "focus_success": "已切换到对应终端",
    "approve_all": "允许当前全部待审批…",
    "approve_all_title": "允许全部待审批",
    "approve_all_confirm": "将一次性允许当前检测到的 {count} 个审批请求，相关命令或文件修改会立即执行。\n\n不会自动允许之后出现的新请求，普通选项询问也不会被回答。",
    "approve_all_button": "全部允许",
    "approve_all_success": "已允许 {approved} 个审批请求",
    "approve_all_partial": "已允许 {approved} 个，跳过 {skipped} 个非批准询问或已消失请求",
    "approve_all_errors": "已允许 {approved} 个，{errors} 个处理失败",
    "approve_all_none": "没有可安全自动允许的审批请求",
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
    "header": "Click a conversation to focus · status · project — name",
    "local": "Local",
    "new_terminal": "New local Codex terminal",
    "new_here": "New Codex terminal here",
    "rename": "Rename conversation…",
    "rename_title": "Rename Codex conversation",
    "rename_prompt": "New conversation name",
    "rename_success": "Conversation renamed",
    "archive": "Archive conversation…",
    "archive_title": "Archive Codex conversation",
    "archive_confirm": "Archive “{title}”?\nIts running terminal may exit.",
    "archive_success": "Conversation archived",
    "cancel": "Cancel",
    "save": "Save",
    "remote": "Remote",
    "manage": "Manage conversations",
    "focus_success": "Focused the terminal",
    "approve_all": "Approve all current requests…",
    "approve_all_title": "Approve all pending requests",
    "approve_all_confirm": "Approve the {count} requests currently detected? Their commands or file changes will run immediately.\n\nFuture requests will not be approved automatically, and ordinary questions will not be answered.",
    "approve_all_button": "Approve all",
    "approve_all_success": "Approved {approved} requests",
    "approve_all_partial": "Approved {approved}; skipped {skipped} ordinary or expired requests",
    "approve_all_errors": "Approved {approved}; {errors} failed",
    "approve_all_none": "No approval request could be safely approved",
}

SYMBOLS = {
    SessionStatus.ATTENTION: "◐",
    SessionStatus.WORKING: "●",
    SessionStatus.IDLE: "○",
    SessionStatus.DONE: "✓",
    SessionStatus.UNKNOWN: "?",
    SessionStatus.CLOSED: "×",
}

COLOR_SYMBOLS = {
    SessionStatus.ATTENTION: "🟠",
    SessionStatus.WORKING: "🟢",
    SessionStatus.IDLE: "⚪",
    SessionStatus.DONE: "🔵",
    SessionStatus.UNKNOWN: "⚫",
    SessionStatus.CLOSED: "⚫",
}

STATUS_COLORS = {
    SessionStatus.ATTENTION: "#e5a50a",
    SessionStatus.WORKING: "#2ec27e",
    SessionStatus.IDLE: "#9a9996",
    SessionStatus.DONE: "#3584e4",
    SessionStatus.UNKNOWN: "#77767b",
    SessionStatus.CLOSED: "#77767b",
}


def text(key: str) -> str:
    return (ZH if is_chinese() else EN).get(key, key)


def status_text(status: SessionStatus) -> str:
    return text(status.value)
