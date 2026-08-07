from __future__ import annotations

import os
import plistlib
import shlex
import shutil
import subprocess
import sys

from codex_indicator.hooks import application_argv
from codex_indicator.paths import (
    APP_ID,
    linux_autostart_path,
    linux_systemd_service_path,
    macos_launch_agent_path,
)


def _windows_command(arguments: list[str]) -> str:
    return subprocess.list2cmdline(arguments)


def _systemd_available() -> bool:
    if sys.platform != "linux" or not shutil.which("systemctl"):
        return False
    try:
        result = subprocess.run(
            ["systemctl", "--user", "show-environment"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _systemctl(*arguments: str) -> None:
    subprocess.run(
        ["systemctl", "--user", *arguments],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=5,
    )


def enable(arguments: list[str] | None = None) -> None:
    argv = arguments or application_argv()
    if sys.platform == "win32":
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
            winreg.SetValueEx(key, "CodexIndicator", 0, winreg.REG_SZ, _windows_command(argv))
        return
    if sys.platform == "darwin":
        path = macos_launch_agent_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "Label": APP_ID,
            "ProgramArguments": argv,
            "RunAtLoad": True,
            "KeepAlive": False,
        }
        temporary = path.with_suffix(".tmp")
        with temporary.open("wb") as output:
            plistlib.dump(payload, output)
        os.replace(temporary, path)
        return
    if _systemd_available():
        path = linux_systemd_service_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        command = " ".join(shlex.quote(part) for part in argv)
        content = "\n".join(
            (
                "[Unit]",
                "Description=CC Indicator: tray status for Codex and Claude CLI sessions",
                "After=graphical-session.target",
                "PartOf=graphical-session.target",
                "",
                "[Service]",
                "Type=simple",
                f"ExecStart={command}",
                "Restart=on-failure",
                "RestartSec=3",
                "",
                "[Install]",
                "WantedBy=graphical-session.target",
                "",
            )
        )
        temporary = path.with_suffix(".tmp")
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, path)
        try:
            linux_autostart_path().unlink()
        except FileNotFoundError:
            pass
        _systemctl("daemon-reload")
        _systemctl("enable", "codex-indicator.service")
        return
    path = linux_autostart_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    command = " ".join(shlex.quote(part) for part in argv)
    content = "\n".join(
        (
            "[Desktop Entry]",
            "Type=Application",
            "Name=CC Indicator",
            f"Exec={command}",
            "Icon=codex-indicator-symbolic",
            "Terminal=false",
            "X-GNOME-Autostart-enabled=true",
            "Comment=Show and manage Codex and Claude CLI session status",
            "",
        )
    )
    temporary = path.with_suffix(".tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def disable() -> None:
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_SET_VALUE,
            ) as key:
                winreg.DeleteValue(key, "CodexIndicator")
        except FileNotFoundError:
            pass
        return
    if sys.platform == "linux":
        service = linux_systemd_service_path()
        if _systemd_available():
            try:
                _systemctl("disable", "codex-indicator.service")
            except subprocess.CalledProcessError:
                pass
            try:
                service.unlink()
            except FileNotFoundError:
                pass
            _systemctl("daemon-reload")
        try:
            linux_autostart_path().unlink()
        except FileNotFoundError:
            pass
        return
    path = macos_launch_agent_path()
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def is_enabled() -> bool:
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
                winreg.QueryValueEx(key, "CodexIndicator")
            return True
        except FileNotFoundError:
            return False
    if sys.platform == "linux":
        if linux_systemd_service_path().exists() and _systemd_available():
            try:
                result = subprocess.run(
                    ["systemctl", "--user", "is-enabled", "codex-indicator.service"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=2,
                )
                return result.returncode == 0
            except (OSError, subprocess.SubprocessError):
                pass
        return linux_autostart_path().exists()
    return macos_launch_agent_path().exists()
