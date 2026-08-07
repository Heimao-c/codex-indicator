from __future__ import annotations

import html
import logging
from importlib.resources import as_file, files
from typing import Callable

from cc_indicator import __version__
from cc_indicator import autostart, hooks
from cc_indicator.i18n import COLOR_SYMBOLS, STATUS_COLORS, text
from cc_indicator.models import SessionStatus
from cc_indicator.presentation import session_row, shorten
from cc_indicator.service import SessionService, SessionView


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
        self._asset_context = as_file(files("cc_indicator.assets"))
        self._asset_dir = self._asset_context.__enter__()
        self.indicator = AyatanaAppIndicator3.Indicator.new(
            "cc-indicator",
            "cc-indicator-symbolic",
            AyatanaAppIndicator3.IndicatorCategory.APPLICATION_STATUS,
        )
        self.indicator.set_icon_theme_path(str(self._asset_dir))
        self.indicator.set_status(AyatanaAppIndicator3.IndicatorStatus.ACTIVE)
        self.indicator.set_title("CC Indicator")
        self._rebuild_menu([])

    def _disabled_item(self, label: str) -> object:
        item = self.Gtk.MenuItem(label=label)
        item.set_sensitive(False)
        return item

    def _session_item(self, session: SessionView) -> object:
        item = self.Gtk.MenuItem()
        label = self.Gtk.Label()
        label.set_xalign(0)
        label.set_markup(
            f'<span foreground="{STATUS_COLORS[session.status]}">'
            f'{html.escape(session_row(session))}</span>'
        )
        item.add(label)
        item.connect("activate", self._focus, session)
        return item

    def _management_item(self, session: SessionView) -> object:
        label = f"{session.project} — {shorten(session.title, 14)}"
        item = self.Gtk.MenuItem(label=label)
        actions = self.Gtk.Menu()
        rename_item = self.Gtk.MenuItem(label=text("rename"))
        rename_item.connect("activate", self._rename, session)
        actions.append(rename_item)
        archive_item = self.Gtk.MenuItem(label=text("archive"))
        archive_item.connect("activate", self._archive, session)
        actions.append(archive_item)
        actions.append(self.Gtk.SeparatorMenuItem())
        new_item = self.Gtk.MenuItem(label=text("new_here"))
        new_item.connect("activate", self._new_terminal, session)
        actions.append(new_item)
        item.set_submenu(actions)
        return item

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
        try:
            hooks.install()
            self._message = ""
        except Exception as error:
            LOG.exception("Could not install Codex hooks")
            self._message = str(error)
        self._fingerprint = None
        self._refresh()

    def _new_terminal(
        self,
        _item: object,
        session: SessionView | None = None,
        tool: str | None = None,
    ) -> None:
        target = session.tool if session else (tool or "codex")
        self._run_safely(
            lambda: self.service.new_terminal(session, tool=target),
            text("new_terminal_success"),
        )

    def _focus(self, _item: object, session: SessionView) -> None:
        self._run_safely(lambda: self.service.focus(session), text("focus_success"))

    def _approve_all(self, _item: object, sessions: list[SessionView]) -> None:
        pending = [
            session
            for session in sessions
            if session.status == SessionStatus.ATTENTION and session.tool == "codex"
        ]
        if not pending:
            self._message = text("approve_all_none")
            self._fingerprint = None
            self._refresh()
            return
        try:
            result = self.service.approve_all(pending)
            if result.high_risk:
                details = "\n".join(
                    f"• {item.session.location}/{item.session.project}: {shorten(item.summary, 120)}"
                    for item in result.high_risk[:8]
                )
                dialog = self.Gtk.MessageDialog(
                    flags=self.Gtk.DialogFlags.MODAL,
                    message_type=self.Gtk.MessageType.WARNING,
                    buttons=self.Gtk.ButtonsType.NONE,
                    text=text("approve_high_risk_title"),
                )
                dialog.format_secondary_text(
                    text("approve_high_risk_confirm").format(
                        count=len(result.high_risk),
                        details=details,
                    )
                )
                dialog.add_button(text("cancel"), self.Gtk.ResponseType.CANCEL)
                dialog.add_button(text("approve_high_risk_button"), self.Gtk.ResponseType.OK)
                response = dialog.run()
                dialog.destroy()
                if response == self.Gtk.ResponseType.OK:
                    high_risk_result = self.service.approve_all(
                        [item.session for item in result.high_risk],
                        allow_high_risk=True,
                    )
                    result = result.merged(high_risk_result)
            if result.high_risk:
                self._message = text("approve_high_risk_deferred").format(
                    approved=result.approved,
                    dangerous=len(result.high_risk),
                )
            elif result.errors:
                self._message = text("approve_all_errors").format(
                    approved=result.approved,
                    errors=len(result.errors),
                )
            elif result.skipped:
                self._message = text("approve_all_partial").format(
                    approved=result.approved,
                    skipped=result.skipped,
                )
            elif result.approved:
                self._message = text("approve_all_success").format(approved=result.approved)
            else:
                self._message = text("approve_all_none")
        except Exception as error:
            LOG.exception("Could not approve pending Codex requests")
            self._message = str(error)
        self._fingerprint = None
        self._refresh()

    def _rename(self, _item: object, session: SessionView) -> None:
        dialog = self.Gtk.Dialog(title=text("rename_title"), flags=self.Gtk.DialogFlags.MODAL)
        dialog.add_button(text("cancel"), self.Gtk.ResponseType.CANCEL)
        dialog.add_button(text("save"), self.Gtk.ResponseType.OK)
        box = dialog.get_content_area()
        prompt = self.Gtk.Label(label=text("rename_prompt"))
        prompt.set_xalign(0)
        entry = self.Gtk.Entry()
        entry.set_text(session.title)
        entry.set_activates_default(True)
        dialog.set_default_response(self.Gtk.ResponseType.OK)
        box.set_spacing(8)
        box.set_border_width(12)
        box.add(prompt)
        box.add(entry)
        dialog.show_all()
        entry.grab_focus()
        entry.select_region(0, -1)
        response = dialog.run()
        name = entry.get_text().strip()
        dialog.destroy()
        if response == self.Gtk.ResponseType.OK and name:
            self._run_safely(lambda: self.service.rename(session, name), text("rename_success"))

    def _archive(self, _item: object, session: SessionView) -> None:
        key = "archive_confirm_codex" if session.tool == "codex" and session.manageable else "archive_confirm"
        message = text(key).format(title=shorten(session.title, 28))
        dialog = self.Gtk.MessageDialog(
            flags=self.Gtk.DialogFlags.MODAL,
            message_type=self.Gtk.MessageType.WARNING,
            buttons=self.Gtk.ButtonsType.NONE,
            text=text("archive_title"),
        )
        dialog.format_secondary_text(message)
        dialog.add_button(text("cancel"), self.Gtk.ResponseType.CANCEL)
        dialog.add_button(text("archive").rstrip("…"), self.Gtk.ResponseType.OK)
        response = dialog.run()
        dialog.destroy()
        if response == self.Gtk.ResponseType.OK:
            self._run_safely(lambda: self.service.archive(session), text("archive_success"))

    def _toggle_claude_allow_all(self, item: object) -> None:
        enabled = bool(item.get_active())
        if enabled == self.service.claude_allow_all:
            return
        if enabled:
            dialog = self.Gtk.MessageDialog(
                flags=self.Gtk.DialogFlags.MODAL,
                message_type=self.Gtk.MessageType.WARNING,
                buttons=self.Gtk.ButtonsType.NONE,
                text=text("claude_allow_all"),
            )
            dialog.format_secondary_text(text("claude_allow_all_confirm"))
            dialog.add_button(text("cancel"), self.Gtk.ResponseType.CANCEL)
            dialog.add_button(text("claude_allow_all_button"), self.Gtk.ResponseType.OK)
            response = dialog.run()
            dialog.destroy()
            if response != self.Gtk.ResponseType.OK:
                # Reverting the check re-fires toggled; the state guard above
                # swallows that second call because nothing changed.
                item.set_active(False)
                return
        try:
            self.service.set_claude_allow_all(enabled)
            self._message = text("claude_allow_all_on" if enabled else "claude_allow_all_off")
        except Exception as error:
            LOG.exception("Could not toggle Claude auto-approve")
            self._message = str(error)
        self._fingerprint = None
        self._refresh()

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
                menu.append(self._session_item(session))
        else:
            menu.append(self._disabled_item(text("no_sessions")))
        if self.service.supports_approvals and any(
            session.status == SessionStatus.ATTENTION and session.tool == "codex"
            for session in sessions
        ):
            menu.append(self.Gtk.SeparatorMenuItem())
            approve_item = self.Gtk.MenuItem(label=text("approve_all"))
            approve_item.connect("activate", self._approve_all, sessions)
            menu.append(approve_item)
        if self._message:
            menu.append(self.Gtk.SeparatorMenuItem())
            menu.append(self._disabled_item(self._message))
        menu.append(self.Gtk.SeparatorMenuItem())
        if sessions:
            # Rename/archive work for every visible conversation: real Codex
            # threads go through the app server, while Claude sessions and
            # placeholder terminals fall back to local display-only actions.
            manage_item = self.Gtk.MenuItem(label=text("manage"))
            manage_menu = self.Gtk.Menu()
            for session in sessions[:30]:
                manage_menu.append(self._management_item(session))
            manage_item.set_submenu(manage_menu)
            menu.append(manage_item)
        claude_toggle = self.Gtk.CheckMenuItem(label=text("claude_allow_all"))
        claude_toggle.set_active(self.service.claude_allow_all)
        claude_toggle.connect("toggled", self._toggle_claude_allow_all)
        menu.append(claude_toggle)
        new_item = self.Gtk.MenuItem(label=text("new_terminal"))
        new_menu = self.Gtk.Menu()
        codex_item = self.Gtk.MenuItem(label=text("new_codex_terminal"))
        codex_item.connect("activate", self._new_terminal, None, "codex")
        new_menu.append(codex_item)
        claude_item = self.Gtk.MenuItem(label=text("new_claude_terminal"))
        claude_item.connect("activate", self._new_terminal, None, "claude")
        new_menu.append(claude_item)
        new_item.set_submenu(new_menu)
        menu.append(new_item)
        hook_label = text("hooks_repair") if hooks.is_installed() else text("hooks_install")
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
            parts.append(f"{COLOR_SYMBOLS[SessionStatus.WORKING]}{working}")
        if attention:
            parts.append(f"{COLOR_SYMBOLS[SessionStatus.ATTENTION]}{attention}")
        if done:
            parts.append(f"{COLOR_SYMBOLS[SessionStatus.DONE]}{done}")
        summary = " ".join(parts) if parts else "0"
        label = " CC " + summary
        self.indicator.set_label(label, f"CC Indicator · {summary}")
        icon = (
            "cc-indicator-attention"
            if attention
            else "cc-indicator-working"
            if working
            else "cc-indicator-done"
            if done
            else "cc-indicator-idle"
        )
        self.indicator.set_icon_full(icon, "CC Indicator")

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
