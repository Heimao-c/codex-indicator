from __future__ import annotations

import ctypes
import logging
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from cc_indicator.models import SessionStatus


if TYPE_CHECKING:
    from cc_indicator.service import SessionView


LOG = logging.getLogger(__name__)
WINDOW_LINE = re.compile(
    r'^\s*(0x[0-9a-fA-F]+)\s+"(.*)":\s+\("gnome-terminal-server"\s+"Gnome-terminal"\)'
)
ATTENTION_MARKERS = (
    "action required",
    "needs attention",
    "approval required",
    "permission required",
    "待审批",
    "等待审批",
    "需要审批",
    "需要操作",
)
APPROVAL_PROMPT_MARKERS = (
    "would you like to run the following command?",
    "do you want to approve network access to",
    "would you like to grant these permissions?",
    "would you like to make the following edits?",
)
APPROVAL_ACCEPT_MARKERS = (
    "yes, proceed",
    "yes, just this once",
    "yes, grant these permissions for this turn",
)
HIGH_RISK_APPROVAL_PATTERNS = (
    re.compile(r"\b(?:mkfs(?:\.[a-z0-9_-]+)?|wipefs|blkdiscard)\b", re.IGNORECASE),
    re.compile(r"\bdd\b[^\n]*(?:\bof\s*=\s*/dev/|\bif\s*=\s*/dev/(?:zero|random|urandom))", re.IGNORECASE),
    re.compile(r"\bshred\b[^\n]*/dev/", re.IGNORECASE),
    re.compile(r"\bsgdisk\b[^\n]*(?:--zap-all|(?:^|\s)-z(?:\s|$))", re.IGNORECASE),
    re.compile(r"\bparted\b[^\n]*\bmklabel\b", re.IGNORECASE),
    re.compile(r"\b(?:format(?:\.com)?\s+[a-z]:|clear-disk\b|remove-partition\b)", re.IGNORECASE),
    re.compile(r"\bdiskpart\b[\s\S]*?\bclean(?:\s+all)?\b", re.IGNORECASE),
    re.compile(
        r"\brm\s+(?=[^\n]*(?:-[^\s]*[rR][^\s]*|--recursive))[^\n]*?"
        r"(?:^|\s)(?:--\s+)?(?:/|/(?:boot|etc|opt|root|usr|var)/?|/home(?:/[^/\s;&|]+)?/?|~/?|"
        r"\$(?:HOME|\{HOME\})/?)(?:\*+)?(?=\s|$|[;&|])",
        re.IGNORECASE,
    ),
    re.compile(r"\bfind\s+/(?:\s|[^\n]*\s)-(?:delete|exec\s+rm)\b", re.IGNORECASE),
    re.compile(r"\bgit\s+(?:reset\s+--hard|clean\s+-[^\s]*f[^\s]*[dx]|clean\s+-[^\s]*[dx][^\s]*f)\b", re.IGNORECASE),
    re.compile(r"\b(?:drop\s+(?:database|schema)|truncate\s+table)\b", re.IGNORECASE),
    re.compile(r"\bterraform\s+destroy\b", re.IGNORECASE),
    re.compile(r"\bkubectl\s+delete\s+(?:namespace|persistentvolume|persistentvolumeclaim)\b", re.IGNORECASE),
)
GEOMETRY_PATTERNS = {
    "x": re.compile(r"Absolute upper-left X:\s*(-?\d+)"),
    "y": re.compile(r"Absolute upper-left Y:\s*(-?\d+)"),
    "width": re.compile(r"Width:\s*(\d+)"),
    "height": re.compile(r"Height:\s*(\d+)"),
}

MACOS_FOCUS_SCRIPT = r'''
on run argv
    set targetTTY to item 1 of argv
    tell application "Terminal"
        repeat with terminalWindow in windows
            repeat with terminalTab in tabs of terminalWindow
                if tty of terminalTab is targetTTY then
                    set selected tab of terminalWindow to terminalTab
                    set index of terminalWindow to 1
                    activate
                    return "focused"
                end if
            end repeat
        end repeat
    end tell
    error "Codex terminal tab was not found"
end run
'''.strip()


@dataclass(frozen=True)
class TerminalWindow:
    window_id: int
    title: str

    @property
    def needs_attention(self) -> bool:
        lowered = self.title.casefold()
        return any(marker in lowered for marker in ATTENTION_MARKERS)

    @property
    def is_working(self) -> bool:
        title = self.title.lstrip()
        return bool(title and "\u2800" <= title[0] <= "\u28ff")


@dataclass(frozen=True)
class HighRiskApproval:
    session: "SessionView"
    summary: str


@dataclass(frozen=True)
class ApprovalBatchResult:
    approved: int
    skipped: int
    errors: tuple[str, ...] = ()
    high_risk: tuple[HighRiskApproval, ...] = ()

    def merged(self, other: "ApprovalBatchResult") -> "ApprovalBatchResult":
        return ApprovalBatchResult(
            approved=self.approved + other.approved,
            skipped=self.skipped + other.skipped,
            errors=(*self.errors, *other.errors),
            high_risk=other.high_risk,
        )


def _approval_pane(value: str) -> str:
    lowered = value.casefold()
    start = max((lowered.rfind(marker) for marker in APPROVAL_PROMPT_MARKERS), default=-1)
    return value[start:] if start >= 0 else ""


def is_approval_screen(value: str) -> bool:
    """Return true only for Codex approve/deny panes whose first row is one-shot accept."""
    pane = _approval_pane(value).casefold()
    return bool(pane) and any(marker in pane for marker in APPROVAL_ACCEPT_MARKERS)


def high_risk_approval_summary(value: str) -> str | None:
    """Return a short current-pane summary when approval may cause irreversible data loss."""
    pane = _approval_pane(value)
    if not pane:
        return None
    for pattern in HIGH_RISK_APPROVAL_PATTERNS:
        match = pattern.search(pane)
        if not match:
            continue
        line_start = pane.rfind("\n", 0, match.start()) + 1
        line_end = pane.find("\n", match.end())
        line = pane[line_start : line_end if line_end >= 0 else len(pane)]
        summary = " ".join(line.split()).strip()
        return f"{summary[:157].rstrip()}..." if len(summary) > 160 else summary
    return None


class TerminalWindowResolver:
    def __init__(self, cache_seconds: float = 1.0) -> None:
        self.cache_seconds = cache_seconds
        self._cached_at = 0.0
        self._cached: list[TerminalWindow] = []

    def windows(self, force: bool = False) -> list[TerminalWindow]:
        if not sys.platform.startswith("linux"):
            return []
        now = time.monotonic()
        if not force and now - self._cached_at < self.cache_seconds:
            return list(self._cached)
        try:
            result = subprocess.run(
                ["xwininfo", "-root", "-tree"],
                check=False,
                capture_output=True,
                text=True,
                timeout=1,
            )
        except (OSError, subprocess.SubprocessError):
            return []
        windows: list[TerminalWindow] = []
        for line in result.stdout.splitlines():
            match = WINDOW_LINE.match(line)
            if not match:
                continue
            windows.append(TerminalWindow(window_id=int(match.group(1), 16), title=match.group(2)))
        self._cached = windows
        self._cached_at = now
        return list(windows)

    @staticmethod
    def _score(session: "SessionView", window: TerminalWindow) -> int:
        title = window.title.casefold()
        project = session.project.casefold().strip()
        score = 0
        if project and project != "—" and project in title:
            score += 60
        if session.source_host and session.source_host.casefold() in title:
            score += 40
        if window.needs_attention:
            if session.status == SessionStatus.ATTENTION:
                score += 45
            elif session.status == SessionStatus.WORKING:
                score += 25
            elif session.source_host:
                score += 15
        elif session.status == SessionStatus.DONE:
            score += 15
            if project and title.strip() == project:
                score += 20
        elif session.status == SessionStatus.WORKING and title and not title[0].isalnum():
            score += 10
        return score

    def match(self, sessions: list["SessionView"]) -> dict[str, TerminalWindow]:
        candidates = []
        for session in sessions:
            for window in self.windows():
                score = self._score(session, window)
                if score >= 50:
                    candidates.append((score, session.session_id, window))
        candidates.sort(key=lambda item: item[0], reverse=True)
        matched: dict[str, TerminalWindow] = {}
        used_windows: set[int] = set()
        for _score, session_id, window in candidates:
            if session_id in matched or window.window_id in used_windows:
                continue
            matched[session_id] = window
            used_windows.add(window.window_id)
        return matched


class _XClientMessageEvent(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int),
        ("serial", ctypes.c_ulong),
        ("send_event", ctypes.c_int),
        ("display", ctypes.c_void_p),
        ("window", ctypes.c_ulong),
        ("message_type", ctypes.c_ulong),
        ("format", ctypes.c_int),
        ("data", ctypes.c_long * 5),
    ]


class _XEvent(ctypes.Union):
    _fields_ = [("xclient", _XClientMessageEvent), ("pad", ctypes.c_long * 24)]


def focus_x11_window(window_id: int) -> None:
    if not sys.platform.startswith("linux"):
        raise RuntimeError("当前系统暂不支持按窗口跳转")
    try:
        x11 = ctypes.cdll.LoadLibrary("libX11.so.6")
    except OSError as error:
        raise RuntimeError("找不到 X11 窗口库") from error
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    x11.XDefaultRootWindow.restype = ctypes.c_ulong
    x11.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
    x11.XInternAtom.restype = ctypes.c_ulong
    x11.XMapRaised.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    x11.XMapRaised.restype = ctypes.c_int
    x11.XSendEvent.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_int,
        ctypes.c_long,
        ctypes.POINTER(_XEvent),
    ]
    x11.XSendEvent.restype = ctypes.c_int
    x11.XFlush.argtypes = [ctypes.c_void_p]
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
    display = x11.XOpenDisplay(None)
    if not display:
        raise RuntimeError("无法连接当前图形桌面")
    try:
        root = x11.XDefaultRootWindow(display)
        atom = x11.XInternAtom(display, b"_NET_ACTIVE_WINDOW", 0)
        event = _XEvent()
        event.xclient.type = 33  # ClientMessage
        event.xclient.display = display
        event.xclient.window = window_id
        event.xclient.message_type = atom
        event.xclient.format = 32
        event.xclient.data[0] = 2  # pager/user-initiated activation
        event.xclient.data[1] = 0
        x11.XMapRaised(display, window_id)
        mask = (1 << 20) | (1 << 19)  # SubstructureRedirectMask | SubstructureNotifyMask
        sent = x11.XSendEvent(display, root, 0, mask, ctypes.byref(event))
        x11.XFlush(display)
        if not sent:
            raise RuntimeError("桌面拒绝了终端激活请求")
    finally:
        x11.XCloseDisplay(display)


def _ancestor_chain(start_pid: int, parents: dict[int, int]) -> list[int]:
    chain: list[int] = []
    seen: set[int] = set()
    pid = start_pid
    while pid > 0 and pid not in seen:
        chain.append(pid)
        seen.add(pid)
        pid = parents.get(pid, 0)
    return chain


def _windows_process_parents() -> dict[int, int]:
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
    kernel32.CreateToolhelp32Snapshot.argtypes = [ctypes.c_ulong, ctypes.c_ulong]
    kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    kernel32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32FirstW.restype = ctypes.c_bool
    kernel32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.restype = ctypes.c_bool
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    invalid_handle = ctypes.c_void_p(-1).value
    if snapshot == invalid_handle:
        return {}
    parents: dict[int, int] = {}
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(entry)
    try:
        found = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while found:
            parents[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
            found = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return parents


def _windows_visible_windows() -> dict[int, list[int]]:
    user32 = ctypes.windll.user32
    windows: dict[int, list[int]] = {}
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    @callback_type
    def collect(window: int, _parameter: int) -> bool:
        if not user32.IsWindowVisible(window):
            return True
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(window, ctypes.byref(pid))
        if pid.value:
            windows.setdefault(int(pid.value), []).append(int(window))
        return True

    user32.EnumWindows.argtypes = [callback_type, ctypes.c_void_p]
    user32.EnumWindows.restype = ctypes.c_bool
    user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
    user32.IsWindowVisible.restype = ctypes.c_bool
    user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    user32.GetWindowThreadProcessId.restype = ctypes.c_ulong
    if not user32.EnumWindows(collect, 0):
        raise RuntimeError("无法枚举 Windows 桌面窗口")
    return windows


def _nearest_ancestor_window(ancestors: list[int], windows: dict[int, list[int]]) -> int | None:
    for pid in ancestors:
        candidates = windows.get(pid)
        if candidates:
            return candidates[0]
    return None


def focus_windows_terminal(pid: int) -> None:
    if sys.platform != "win32":
        raise RuntimeError("当前系统不是 Windows")
    ancestors = _ancestor_chain(pid, _windows_process_parents())
    window = _nearest_ancestor_window(ancestors, _windows_visible_windows())
    if window is None:
        raise RuntimeError("没有找到这个会话对应的 Windows Terminal 窗口")
    user32 = ctypes.windll.user32
    user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
    user32.ShowWindow.restype = ctypes.c_bool
    user32.BringWindowToTop.argtypes = [ctypes.c_void_p]
    user32.BringWindowToTop.restype = ctypes.c_bool
    user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
    user32.SetForegroundWindow.restype = ctypes.c_bool
    user32.ShowWindow(window, 9)  # SW_RESTORE
    user32.BringWindowToTop(window)
    if not user32.SetForegroundWindow(window):
        raise RuntimeError("Windows 拒绝了终端激活请求")


def _terminal_tty(pid: int) -> str | None:
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "tty="],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip()
    if not value or value in {"?", "??"}:
        return None
    return value if value.startswith("/dev/") else f"/dev/{value}"


def focus_macos_terminal(pid: int) -> None:
    if sys.platform != "darwin":
        raise RuntimeError("当前系统不是 macOS")
    tty = _terminal_tty(pid)
    if not tty:
        raise RuntimeError("无法确定这个 Codex 会话所在的终端")
    try:
        result = subprocess.run(
            ["osascript", "-e", MACOS_FOCUS_SCRIPT, tty],
            check=False,
            capture_output=True,
            text=True,
            timeout=4,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError("无法调用 macOS Terminal") from error
    if result.returncode or result.stdout.strip() != "focused":
        detail = result.stderr.strip()
        raise RuntimeError(detail or "没有找到这个会话对应的 macOS Terminal 标签页")


def focus_terminal(pid: int | None = None, window_id: int | None = None) -> None:
    if sys.platform.startswith("linux"):
        if window_id is None:
            raise RuntimeError("没有找到这个会话对应的终端窗口")
        focus_x11_window(window_id)
        return
    if pid is None or pid <= 0:
        raise RuntimeError("这个会话没有可用的终端进程信息")
    if sys.platform == "win32":
        focus_windows_terminal(pid)
        return
    if sys.platform == "darwin":
        focus_macos_terminal(pid)
        return
    raise RuntimeError("当前系统暂不支持按窗口跳转")


def active_x11_window() -> int | None:
    if not sys.platform.startswith("linux"):
        return None
    try:
        result = subprocess.run(
            ["xprop", "-root", "_NET_ACTIVE_WINDOW"],
            check=False,
            capture_output=True,
            text=True,
            timeout=1,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"window id #\s*(0x[0-9a-fA-F]+)", result.stdout)
    return int(match.group(1), 16) if match else None


def _x11_window_geometry(window_id: int) -> tuple[int, int, int, int] | None:
    try:
        result = subprocess.run(
            ["xwininfo", "-id", hex(window_id)],
            check=False,
            capture_output=True,
            text=True,
            timeout=1,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    values: dict[str, int] = {}
    for name, pattern in GEOMETRY_PATTERNS.items():
        match = pattern.search(result.stdout)
        if not match:
            return None
        values[name] = int(match.group(1))
    return values["x"], values["y"], values["width"], values["height"]


def _find_accessible_terminal(accessible: object) -> object | None:
    try:
        if accessible.get_role_name() == "terminal":
            return accessible
        child_count = accessible.get_child_count()
    except Exception:
        return None
    for index in range(child_count):
        try:
            child = accessible.get_child_at_index(index)
        except Exception:
            continue
        if child:
            terminal = _find_accessible_terminal(child)
            if terminal:
                return terminal
    return None


def approval_screen_text(window_id: int) -> str:
    """Read only the visible VTE screen matched to a top-level GNOME Terminal window."""
    if not sys.platform.startswith("linux"):
        return ""
    geometry = _x11_window_geometry(window_id)
    if not geometry:
        return ""
    try:
        import gi

        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi
    except (ImportError, ValueError):
        return ""
    desktop = Atspi.get_desktop(0)
    for app_index in range(desktop.get_child_count()):
        app = desktop.get_child_at_index(app_index)
        try:
            if not app or (app.get_name() or "") != "gnome-terminal-server":
                continue
            frame_count = app.get_child_count()
        except Exception:
            continue
        for frame_index in range(frame_count):
            frame = app.get_child_at_index(frame_index)
            try:
                if not frame or frame.get_role_name() != "frame":
                    continue
                component = frame.get_component_iface()
                rectangle = component.get_extents(Atspi.CoordType.SCREEN)
                frame_geometry = (rectangle.x, rectangle.y, rectangle.width, rectangle.height)
            except Exception:
                continue
            if any(abs(actual - expected) > 3 for actual, expected in zip(frame_geometry, geometry)):
                continue
            terminal = _find_accessible_terminal(frame)
            if not terminal:
                return ""
            try:
                text_interface = terminal.get_text_iface()
                character_count = text_interface.get_character_count()
                return text_interface.get_text(max(0, character_count - 8000), character_count)
            except Exception:
                return ""
    return ""


def press_enter_x11() -> None:
    if not sys.platform.startswith("linux"):
        raise RuntimeError("当前系统暂不支持自动确认终端审批")
    try:
        x11 = ctypes.cdll.LoadLibrary("libX11.so.6")
        xtst = ctypes.cdll.LoadLibrary("libXtst.so.6")
    except OSError as error:
        raise RuntimeError("找不到 X11 输入模拟库") from error
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    x11.XKeysymToKeycode.restype = ctypes.c_ubyte
    x11.XFlush.argtypes = [ctypes.c_void_p]
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
    xtst.XTestFakeKeyEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
    xtst.XTestFakeKeyEvent.restype = ctypes.c_int
    display = x11.XOpenDisplay(None)
    if not display:
        raise RuntimeError("无法连接当前图形桌面")
    try:
        keycode = int(x11.XKeysymToKeycode(display, 0xFF0D))  # XK_Return
        if not keycode:
            raise RuntimeError("无法解析 Enter 键")
        if not xtst.XTestFakeKeyEvent(display, keycode, 1, 0):
            raise RuntimeError("桌面拒绝了 Enter 按键事件")
        if not xtst.XTestFakeKeyEvent(display, keycode, 0, 0):
            raise RuntimeError("桌面拒绝了 Enter 释放事件")
        x11.XFlush(display)
    finally:
        x11.XCloseDisplay(display)


class TerminalApprovalController:
    def __init__(
        self,
        screen_reader=approval_screen_text,
        activate=focus_x11_window,
        press_enter=press_enter_x11,
        active_window=active_x11_window,
        pause=time.sleep,
    ) -> None:
        self.screen_reader = screen_reader
        self.activate = activate
        self.press_enter = press_enter
        self.active_window = active_window
        self.pause = pause

    @property
    def supported(self) -> bool:
        session_type = os.environ.get("XDG_SESSION_TYPE", "").casefold()
        return (
            sys.platform.startswith("linux")
            and bool(os.environ.get("DISPLAY"))
            and session_type in {"", "x11"}
        )

    def approve_all(
        self,
        sessions: list["SessionView"],
        *,
        allow_high_risk: bool = False,
    ) -> ApprovalBatchResult:
        original_window = self.active_window()
        approved = 0
        skipped = 0
        errors: list[str] = []
        high_risk: list[HighRiskApproval] = []
        try:
            for session in sessions:
                if session.status != SessionStatus.ATTENTION or session.window_id is None:
                    continue
                try:
                    screen = self.screen_reader(session.window_id)
                    if not is_approval_screen(screen):
                        skipped += 1
                        continue
                    risk_summary = high_risk_approval_summary(screen)
                    if risk_summary and not allow_high_risk:
                        high_risk.append(HighRiskApproval(session=session, summary=risk_summary))
                        continue
                    self.activate(session.window_id)
                    self.pause(0.18)
                    if self.active_window() != session.window_id:
                        raise RuntimeError("无法激活对应终端")
                    screen = self.screen_reader(session.window_id)
                    if not is_approval_screen(screen):
                        skipped += 1
                        continue
                    risk_summary = high_risk_approval_summary(screen)
                    if risk_summary and not allow_high_risk:
                        high_risk.append(HighRiskApproval(session=session, summary=risk_summary))
                        continue
                    self.press_enter()
                    approved += 1
                    self.pause(0.12)
                except Exception as error:
                    LOG.warning("Could not approve terminal %s", session.session_id, exc_info=True)
                    errors.append(f"{session.location}: {error}")
        finally:
            if original_window and original_window != self.active_window():
                try:
                    self.activate(original_window)
                except Exception:
                    LOG.debug("Could not restore the previously active window", exc_info=True)
        return ApprovalBatchResult(
            approved=approved,
            skipped=skipped,
            errors=tuple(errors),
            high_risk=tuple(high_risk),
        )
