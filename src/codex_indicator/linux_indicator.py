from __future__ import annotations

import logging
from importlib.resources import as_file, files
from typing import Callable

from codex_indicator import __version__
from codex_indicator import autostart, hooks
from codex_indicator.i18n import SYMBOLS, status_text, text
from codex_indicator.models import SessionStatus
from codex_indicator.service import SessionService, SessionView


LOG = logging.getLogger(__name__)


class LinuxIndicatorApp:
    def __init__(self, service: SessionService | None = None) -> None:
        import gi

        gi.require_version("Gtk", "3.0")
        gi.require_version("AyatanaAppIndicator3", "0.1")
        from gi.repository import AyatanaAppIndicator3, GLib, Gtk

        self.AppIndicator = AyatanaAppIndicator3
        self.GLib = GLib
        self.Gtk = Gtk
        self.service = service or SessionService()
        self._fingerprint: tuple[tuple[object, ...], ...] | None = None
        self._message = ""
        self._asset_context = as_file(files("codex_indicator.assets"))
        self._asset_dir = self._asset_context.__enter__()
        self.indicator = AyatanaAppIndicator3.Indicator.new(
            "codex-indicator",
            "codex-indicator-symbolic",
            AyatanaAppIndicator3.IndicatorCategory.APPLICATION_STATUS,
        )
        self.indicator.set_icon_theme_path(str(self._asset_dir))
        self.indicator.set_status(AyatanaAppIndicator3.IndicatorStatus.ACTIVE)
        self.indicator.set_title("Codex Indicator")
        self._rebuild_menu([])

    def _disabled_item(self, label: str) -> object:
        item = self.Gtk.MenuItem(label=label)
        item.set_sensitive(False)
        return item

    @staticmethod
    def _row(session: SessionView) -> str:
        title = session.title if len(session.title) <= 76 else f"{session.title[:75].rstrip()}…"
        project = session.project if len(session.project) <= 24 else f"{session.project[:23]}…"
        return f"{SYMBOLS[session.status]} {status_text(session.status)} · {project} — {title}"

    def _run_safely(self, operation: Callable[[], None], success: str) -> None:
        try:
            operation()
            self._message = success
        except Exception as error:  # tray callbacks must not terminate the main loop
            LOG.exception("Tray operation failed")
            self._message = str(error)
        self._fingerprint = None
        self._refresh()

    def _install_hooks(self, *_args: object) -> None:
        self._run_safely(hooks.install, text("hooks_installed"))

    def _toggle_autostart(self, item: object) -> None:
        active = bool(item.get_active())
        self._run_safely(autostart.enable if active else autostart.disable, text("autostart"))

    def _refresh_now(self, *_args: object) -> None:
        self._fingerprint = None
        self._refresh()

    def _quit(self, *_args: object) -> None:
        self.indicator.set_status(self.AppIndicator.IndicatorStatus.PASSIVE)
        self.Gtk.main_quit()

    def _rebuild_menu(self, sessions: list[SessionView]) -> None:
        menu = self.Gtk.Menu()
        menu.append(self._disabled_item(text("header")))
        menu.append(self.Gtk.SeparatorMenuItem())
        if sessions:
            for session in sessions[:30]:
                menu.append(self._disabled_item(self._row(session)))
        else:
            menu.append(self._disabled_item(text("no_sessions")))
        if self._message:
            menu.append(self.Gtk.SeparatorMenuItem())
            menu.append(self._disabled_item(self._message))
        menu.append(self.Gtk.SeparatorMenuItem())
        hook_label = text("hooks_installed") if hooks.is_installed() else text("hooks_install")
        hook_item = self.Gtk.MenuItem(label=hook_label)
        hook_item.connect("activate", self._install_hooks)
        menu.append(hook_item)
        start_item = self.Gtk.CheckMenuItem(label=text("autostart"))
        start_item.set_active(autostart.is_enabled())
        start_item.connect("toggled", self._toggle_autostart)
        menu.append(start_item)
        refresh_item = self.Gtk.MenuItem(label=text("refresh"))
        refresh_item.connect("activate", self._refresh_now)
        menu.append(refresh_item)
        menu.append(self.Gtk.SeparatorMenuItem())
        menu.append(self._disabled_item(f"{text('about')} {__version__}"))
        quit_item = self.Gtk.MenuItem(label=text("quit"))
        quit_item.connect("activate", self._quit)
        menu.append(quit_item)
        menu.show_all()
        self.indicator.set_menu(menu)

    def _set_summary(self, sessions: list[SessionView]) -> None:
        counts = self.service.counts(sessions)
        working = counts[SessionStatus.WORKING]
        attention = counts[SessionStatus.ATTENTION]
        done = counts[SessionStatus.DONE]
        parts = []
        if working:
            parts.append(f"●{working}")
        if attention:
            parts.append(f"◐{attention}")
        if done:
            parts.append(f"✓{done}")
        label = " C " + (" ".join(parts) if parts else "0")
        self.indicator.set_label(label, " C ●99 ◐99 ✓99")
        icon = "codex-indicator-attention" if attention else "codex-indicator-symbolic"
        self.indicator.set_icon_full(icon, "Codex Indicator")

    def _refresh(self) -> bool:
        sessions = self.service.sessions()
        fingerprint = self.service.fingerprint(sessions)
        if fingerprint != self._fingerprint:
            self._set_summary(sessions)
            self._rebuild_menu(sessions)
            self._fingerprint = fingerprint
        return True

    def run(self) -> int:
        self._refresh()
        self.GLib.timeout_add_seconds(2, self._refresh)
        try:
            try:
                self.Gtk.main()
            except KeyboardInterrupt:
                pass
        finally:
            self._asset_context.__exit__(None, None, None)
        return 0
