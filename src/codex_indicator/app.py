from __future__ import annotations

import logging
import sys

from codex_indicator.runtime import InstanceLock, configure_logging


LOG = logging.getLogger(__name__)


def run() -> int:
    configure_logging()
    lock = InstanceLock()
    if not lock.acquire():
        LOG.info("Another Codex Indicator instance is already running")
        return 0
    try:
        if sys.platform.startswith("linux"):
            try:
                from codex_indicator.linux_indicator import LinuxIndicatorApp

                return LinuxIndicatorApp().run()
            except (ImportError, ValueError) as error:
                LOG.warning("Native AppIndicator unavailable, trying portable tray: %s", error)
        from codex_indicator.portable_tray import PortableTrayApp

        return PortableTrayApp().run()
    except Exception:
        LOG.exception("Codex Indicator terminated unexpectedly")
        return 1
    finally:
        lock.release()
