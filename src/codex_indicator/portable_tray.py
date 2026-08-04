from __future__ import annotations

import logging
import threading
from typing import Any

from codex_indicator import __version__, autostart, hooks
from codex_indicator.i18n import SYMBOLS, status_text, text
from codex_indicator.models import SessionStatus
from codex_indicator.service import SessionService, SessionView


LOG = logging.getLogger(__name__)


class PortableTrayApp:
    def __init__(self, service: SessionService | None = None) -> None:
        try:
            import pystray
            from PIL import Image, ImageDraw
        except ImportError as error:
            raise RuntimeError("Install pystray and Pillow to use the Windows/macOS tray") from error
        self.pystray = pystray
        self.Image = Image
        self.ImageDraw = ImageDraw
        self.service = service or SessionService()
        self.icon = pystray.Icon("codex-indicator")
        self.icon.icon = self._image("idle")
        self.icon.title = "Codex Indicator"
        self.icon.menu = self._menu([])
        self._stop = threading.Event()
        self._fingerprint: tuple[tuple[object, ...], ...] | None = None

    def _image(self, state: str) -> Any:
        colors = {
            "attention": (232, 170, 20, 255),
            "working": (48, 173, 94, 255),
            "done": (61, 120, 216, 255),
            "idle": (105, 112, 122, 255),
        }
        image = self.Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = self.ImageDraw.Draw(image)
        draw.rounded_rectangle((5, 5, 59, 59), radius=14, fill=colors[state])
        draw.arc((17, 15, 49, 49), 55, 305, fill=(255, 255, 255, 255), width=7)
        return image

    @staticmethod
    def _row(session: SessionView) -> str:
        title = session.title if len(session.title) <= 72 else f"{session.title[:71].rstrip()}…"
        return f"{SYMBOLS[session.status]} {status_text(session.status)} · {session.project} — {title}"

    def _menu(self, sessions: list[SessionView]) -> Any:
        Menu = self.pystray.Menu
        Item = self.pystray.MenuItem
        noop = lambda *_args: None
        rows = [Item(text("header"), noop, enabled=False)]
        if sessions:
            rows.extend(Item(self._row(session), noop, enabled=False) for session in sessions[:30])
        else:
            rows.append(Item(text("no_sessions"), noop, enabled=False))
        rows.extend(
            [
                Menu.SEPARATOR,
                Item(text("hooks_installed") if hooks.is_installed() else text("hooks_install"), self._install_hooks),
                Item(text("autostart"), self._toggle_autostart, checked=lambda _item: autostart.is_enabled()),
                Item(text("refresh"), self._refresh_clicked),
                Menu.SEPARATOR,
                Item(f"{text('about')} {__version__}", noop, enabled=False),
                Item(text("quit"), self._quit),
            ]
        )
        return Menu(*rows)

    def _install_hooks(self, _icon: Any, _item: Any) -> None:
        try:
            hooks.install()
        except Exception:
            LOG.exception("Could not install hooks")
        self._refresh(force=True)

    def _toggle_autostart(self, _icon: Any, _item: Any) -> None:
        try:
            (autostart.disable if autostart.is_enabled() else autostart.enable)()
        except Exception:
            LOG.exception("Could not change autostart")
        self._refresh(force=True)

    def _refresh_clicked(self, _icon: Any, _item: Any) -> None:
        self._refresh(force=True)

    def _quit(self, _icon: Any, _item: Any) -> None:
        self._stop.set()
        self.icon.stop()

    def _refresh(self, force: bool = False) -> None:
        sessions = self.service.sessions()
        fingerprint = self.service.fingerprint(sessions)
        if not force and fingerprint == self._fingerprint:
            return
        counts = self.service.counts(sessions)
        state = (
            "attention"
            if counts[SessionStatus.ATTENTION]
            else "working"
            if counts[SessionStatus.WORKING]
            else "done"
            if counts[SessionStatus.DONE]
            else "idle"
        )
        summary = f"Codex: {counts[SessionStatus.WORKING]} working, {counts[SessionStatus.ATTENTION]} attention"
        self.icon.icon = self._image(state)
        self.icon.title = summary
        self.icon.menu = self._menu(sessions)
        self.icon.update_menu()
        self._fingerprint = fingerprint

    def _setup(self, _icon: Any) -> None:
        self.icon.visible = True
        while not self._stop.wait(2):
            try:
                self._refresh()
            except Exception:
                LOG.exception("Tray refresh failed")

    def run(self) -> int:
        self._refresh(force=True)
        self.icon.run(setup=self._setup)
        return 0
