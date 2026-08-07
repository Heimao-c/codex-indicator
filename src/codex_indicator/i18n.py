from __future__ import annotations

import locale

from codex_indicator.models import SessionStatus


def is_chinese() -> bool:
    language = (locale.getlocale()[0] or "").lower()
    return language.startswith("zh")


ZH = {
    "attention": "等待操作",
    "working": "运行中",
    "done": "已完成",
    "unknown": "未知",
    "closed": "已关闭",
    "no_sessions": "暂无活动 Codex/Claude 会话",
    "hooks_install": "安装/修复 Codex + Claude Hooks",
    "hooks_installed": "Codex + Claude Hooks 已安装",
    "hooks_repair": "Codex + Claude Hooks 已安装（点击修复）",
    "autostart": "开机自动启动",
    "refresh": "立即刷新",
    "quit": "退出",
    "about": "CC Indicator",
    "header": "点击对话跳转终端 · 状态 · [工具] 项目 — 名称",
    "local": "本机",
    "tool_codex": "Codex",
    "tool_claude": "Claude",
    "new_terminal": "新建终端…",
    "new_codex_terminal": "新建本机 Codex 终端",
    "new_claude_terminal": "新建本机 Claude 终端",
    "new_terminal_success": "已新建终端",
    "new_here": "在此项目新建终端",
    "rename": "修改对话名称…",
    "rename_title": "修改对话名称",
    "rename_prompt": "新的对话名称",
    "rename_success": "对话名称已更新",
    "archive": "归档对话…",
    "archive_title": "归档对话",
    "archive_confirm": "确定归档“{title}”吗？",
    "archive_confirm_codex": "确定归档“{title}”吗？\n对应的 Codex 对话将被删除，正在运行的终端可能会退出。",
    "archive_success": "对话已归档",
    "cancel": "取消",
    "save": "保存",
    "remote": "服务器",
    "manage": "管理对话",
    "focus_success": "已切换到对应终端",
    "approve_all": "允许当前全部待审批",
    "approve_all_title": "允许全部待审批",
    "approve_all_button": "全部允许",
    "approve_all_success": "已允许 {approved} 个审批请求",
    "approve_all_partial": "已允许 {approved} 个，跳过 {skipped} 个非批准询问或已消失请求",
    "approve_all_errors": "已允许 {approved} 个，{errors} 个处理失败",
    "approve_all_none": "没有可安全自动允许的审批请求",
    "approve_high_risk_title": "检测到高危审批",
    "approve_high_risk_confirm": "检测到 {count} 个可能造成不可逆数据损失的请求。普通审批已经直接处理；以下高危请求仍保持等待：\n\n{details}\n\n仍然允许这些高危操作吗？",
    "approve_high_risk_button": "仍然允许",
    "approve_high_risk_deferred": "已允许 {approved} 个；保留 {dangerous} 个高危请求待你处理",
    "claude_allow_all": "Claude 自动允许全部操作",
    "claude_allow_all_confirm": "开启后，Claude 将对所有新的操作请求自动放行（你已配置的危险命令拒绝规则仍然生效）。如已有等待中的审批，可能还需在终端按一次回车。\n\n确定开启吗？",
    "claude_allow_all_button": "开启自动允许",
    "claude_allow_all_on": "已开启 Claude 自动允许全部操作",
    "claude_allow_all_off": "已关闭 Claude 自动允许，恢复逐个询问",
}

EN = {
    "attention": "Needs attention",
    "working": "Working",
    "done": "Done",
    "unknown": "Unknown",
    "closed": "Closed",
    "no_sessions": "No active Codex/Claude sessions",
    "hooks_install": "Install/repair Codex + Claude hooks",
    "hooks_installed": "Codex + Claude hooks installed",
    "hooks_repair": "Codex + Claude hooks installed (click to repair)",
    "autostart": "Start at login",
    "refresh": "Refresh now",
    "quit": "Quit",
    "about": "CC Indicator",
    "header": "Click a conversation to focus · status · [tool] project — name",
    "local": "Local",
    "tool_codex": "Codex",
    "tool_claude": "Claude",
    "new_terminal": "New terminal…",
    "new_codex_terminal": "New local Codex terminal",
    "new_claude_terminal": "New local Claude terminal",
    "new_terminal_success": "New terminal opened",
    "new_here": "New terminal here",
    "rename": "Rename conversation…",
    "rename_title": "Rename conversation",
    "rename_prompt": "New conversation name",
    "rename_success": "Conversation renamed",
    "archive": "Archive conversation…",
    "archive_title": "Archive conversation",
    "archive_confirm": "Archive “{title}”?",
    "archive_confirm_codex": "Archive “{title}”?\nThe Codex conversation will be deleted and its running terminal may exit.",
    "archive_success": "Conversation archived",
    "cancel": "Cancel",
    "save": "Save",
    "remote": "Remote",
    "manage": "Manage conversations",
    "focus_success": "Focused the terminal",
    "approve_all": "Approve all current requests",
    "approve_all_title": "Approve all pending requests",
    "approve_all_button": "Approve all",
    "approve_all_success": "Approved {approved} requests",
    "approve_all_partial": "Approved {approved}; skipped {skipped} ordinary or expired requests",
    "approve_all_errors": "Approved {approved}; {errors} failed",
    "approve_all_none": "No approval request could be safely approved",
    "approve_high_risk_title": "High-risk approval detected",
    "approve_high_risk_confirm": "{count} requests may cause irreversible data loss. Ordinary approvals were handled immediately; these high-risk requests are still waiting:\n\n{details}\n\nApprove these high-risk operations anyway?",
    "approve_high_risk_button": "Approve anyway",
    "approve_high_risk_deferred": "Approved {approved}; left {dangerous} high-risk requests waiting",
    "claude_allow_all": "Auto-approve all Claude operations",
    "claude_allow_all_confirm": "Claude will then auto-approve every new operation request (your existing deny rules for dangerous commands still apply). An already-pending prompt may still need one Enter key in its terminal.\n\nEnable it?",
    "claude_allow_all_button": "Enable auto-approve",
    "claude_allow_all_on": "Claude auto-approve enabled",
    "claude_allow_all_off": "Claude auto-approve disabled; asking again",
}

SYMBOLS = {
    SessionStatus.ATTENTION: "◐",
    SessionStatus.WORKING: "●",
    SessionStatus.DONE: "✓",
    SessionStatus.UNKNOWN: "?",
    SessionStatus.CLOSED: "×",
}

COLOR_SYMBOLS = {
    SessionStatus.ATTENTION: "🟠",
    SessionStatus.WORKING: "🟢",
    SessionStatus.DONE: "🔵",
    SessionStatus.UNKNOWN: "⚫",
    SessionStatus.CLOSED: "⚫",
}

STATUS_COLORS = {
    SessionStatus.ATTENTION: "#e5a50a",
    SessionStatus.WORKING: "#2ec27e",
    SessionStatus.DONE: "#3584e4",
    SessionStatus.UNKNOWN: "#77767b",
    SessionStatus.CLOSED: "#77767b",
}


def text(key: str) -> str:
    return (ZH if is_chinese() else EN).get(key, key)


def status_text(status: SessionStatus) -> str:
    return text(status.value)
