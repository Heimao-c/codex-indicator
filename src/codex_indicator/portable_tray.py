from __future__ import annotations

import logging
import threading
from typing import Any

from codex_indicator import __version__, autostart, hooks
from codex_indicator.i18n import text
from codex_indicator.models import SessionStatus
from codex_indicator.presentation import session_row, shorten
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
        self.icon.icon = self._image("neutral")
        self.icon.title = "Codex Indicator"
        self.icon.menu = self._menu([])
        self._stop = threading.Event()
        self._fingerprint: tuple[tuple[object, ...], ...] | None = None

    def _image(self, state: str) -> Any:
        colors = {
            "attention": (232, 170, 20, 255),
            "working": (48, 173, 94, 255),
            "done": (61, 120, 216, 255),
            "neutral": (105, 112, 122, 255),
        }
        image = self.Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = self.ImageDraw.Draw(image)
        draw.rounded_rectangle((5, 5, 59, 59), radius=14, fill=colors[state])
        draw.arc((17, 15, 49, 49), 55, 305, fill=(255, 255, 255, 255), width=7)
        return image

    @staticmethod
    def _row(session: SessionView) -> str:
        return session_row(session)

    def _session_menu(self, session: SessionView) -> Any:
        Item = self.pystray.MenuItem
        return Item(self._row(session), lambda *_args: self._focus(session))

    def _management_menu(self, sessions: list[SessionView]) -> Any:
        Menu = self.pystray.Menu
        Item = self.pystray.MenuItem
        items = []
        for session in sessions[:30]:
            items.append(
                Item(
                    f"{session.project} — {shorten(session.title, 14)}",
                    Menu(
                        Item(text("rename"), lambda *_args, current=session: self._rename(current)),
                        Item(text("archive"), lambda *_args, current=session: self._archive(current)),
                        Menu.SEPARATOR,
                        Item(text("new_here"), lambda *_args, current=session: self._new_terminal(current)),
                    ),
                )
            )
        return Item(
            text("manage"),
            Menu(*items),
        )

    def _menu(self, sessions: list[SessionView]) -> Any:
        Menu = self.pystray.Menu
        Item = self.pystray.MenuItem
        noop = lambda *_args: None
        rows = [Item(text("header"), noop, enabled=False)]
        if sessions:
            rows.extend(self._session_menu(session) for session in sessions[:30])
        else:
            rows.append(Item(text("no_sessions"), noop, enabled=False))
        if self.service.supports_approvals and any(
            session.status == SessionStatus.ATTENTION for session in sessions
        ):
            rows.extend(
                [
                    Menu.SEPARATOR,
                    Item(text("approve_all"), lambda *_args: self._approve_all(sessions)),
                ]
            )
        rows.extend(
            [
                Menu.SEPARATOR,
                *(
                    [self._management_menu([session for session in sessions if session.manageable])]
                    if any(session.manageable for session in sessions)
                    else []
                ),
                Item(text("new_terminal"), lambda *_args: self._new_terminal()),
                Item(text("hooks_repair") if hooks.is_installed() else text("hooks_install"), self._install_hooks),
                Item(text("autostart"), self._toggle_autostart, checked=lambda _item: autostart.is_enabled()),
                Item(text("refresh"), self._refresh_clicked),
                Menu.SEPARATOR,
                Item(f"{text('about')} {__version__}", noop, enabled=False),
                Item(text("quit"), self._quit),
            ]
        )
        return Menu(*rows)

    def _focus(self, session: SessionView) -> None:
        try:
            self.service.focus(session)
        except Exception:
            LOG.exception("Could not focus Codex terminal")

    @staticmethod
    def _dialog_modules() -> tuple[Any, Any, Any]:
        import tkinter
        from tkinter import messagebox, simpledialog

        return tkinter, messagebox, simpledialog

    def _rename(self, session: SessionView) -> None:
        tkinter, _messagebox, simpledialog = self._dialog_modules()
        root = tkinter.Tk()
        root.withdraw()
        try:
            name = simpledialog.askstring(
                text("rename_title"), text("rename_prompt"), initialvalue=session.title, parent=root
            )
        finally:
            root.destroy()
        if not name or not name.strip():
            return
        try:
            self.service.rename(session, name)
        except Exception:
            LOG.exception("Could not rename Codex conversation")
        self._refresh(force=True)

    def _archive(self, session: SessionView) -> None:
        tkinter, messagebox, _simpledialog = self._dialog_modules()
        root = tkinter.Tk()
        root.withdraw()
        try:
            confirmed = messagebox.askyesno(
                text("archive_title"),
                text("archive_confirm").format(title=shorten(session.title, 28)),
                parent=root,
            )
        finally:
            root.destroy()
        if not confirmed:
            return
        try:
            self.service.archive(session)
        except Exception:
            LOG.exception("Could not archive Codex conversation")
        self._refresh(force=True)

    def _approve_all(self, sessions: list[SessionView]) -> None:
        pending = [session for session in sessions if session.status == SessionStatus.ATTENTION]
        if not pending:
            return
        tkinter, messagebox, _simpledialog = self._dialog_modules()
        root = tkinter.Tk()
        root.withdraw()
        try:
            result = self.service.approve_all(pending)
            if result.high_risk:
                details = "\n".join(
                    f"• {item.session.location}/{item.session.project}: {shorten(item.summary, 120)}"
                    for item in result.high_risk[:8]
                )
                confirmed = messagebox.askyesno(
                    text("approve_high_risk_title"),
                    text("approve_high_risk_confirm").format(
                        count=len(result.high_risk),
                        details=details,
                    ),
                    parent=root,
                )
                if confirmed:
                    high_risk_result = self.service.approve_all(
                        [item.session for item in result.high_risk],
                        allow_high_risk=True,
                    )
                    result = result.merged(high_risk_result)
            if result.high_risk:
                message = text("approve_high_risk_deferred").format(
                    approved=result.approved,
                    dangerous=len(result.high_risk),
                )
            elif result.errors:
                message = text("approve_all_errors").format(
                    approved=result.approved,
                    errors=len(result.errors),
                )
            elif result.skipped:
                message = text("approve_all_partial").format(
                    approved=result.approved,
                    skipped=result.skipped,
                )
            elif result.approved:
                message = text("approve_all_success").format(approved=result.approved)
            else:
                message = text("approve_all_none")
            messagebox.showinfo(text("approve_all_title"), message, parent=root)
        except Exception:
            LOG.exception("Could not approve pending Codex requests")
        finally:
            root.destroy()
        self._refresh(force=True)

    def _new_terminal(self, session: SessionView | None = None) -> None:
        try:
            self.service.new_terminal(session)
        except Exception:
            LOG.exception("Could not open Codex terminal")

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
            else "neutral"
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
