"""Windows-only tray tweaks.

pystray's win32 backend pops the context menu only on a right click
(``WM_RBUTTONUP``); a left click (``WM_LBUTTONUP``) just fires the default
menu item, which we don't set, so a left click appears to do nothing. This
module wraps the icon's ``WM_NOTIFY`` handler so both buttons open the menu,
matching what users expect from a taskbar tray icon.

Everything here reaches into pystray internals, so it is strictly best-effort:
on any failure the stock right-click-only behavior remains.
"""

import ctypes
import logging
from ctypes import wintypes

logger = logging.getLogger(__name__)


def enable_left_click_menu(tray_icon) -> None:
    """Make a left click open the same context menu as a right click.

    Call after the tray icon's window exists (e.g. from pystray's setup
    callback). No-op on any error.
    """
    try:
        from pystray._util import win32
    except Exception:
        logger.debug("pystray win32 util unavailable; left-click menu skipped",
                     exc_info=True)
        return

    def show_menu():
        menu_handle = getattr(tray_icon, "_menu_handle", None)
        if not menu_handle:
            return
        # TrackPopupMenuEx misbehaves unless our systray window is foreground.
        win32.SetForegroundWindow(tray_icon._hwnd)
        point = wintypes.POINT()
        win32.GetCursorPos(ctypes.byref(point))
        hmenu, descriptors = menu_handle
        index = win32.TrackPopupMenuEx(
            hmenu,
            win32.TPM_RIGHTALIGN | win32.TPM_BOTTOMALIGN | win32.TPM_RETURNCMD,
            point.x,
            point.y,
            tray_icon._menu_hwnd,
            None,
        )
        if index > 0:
            descriptors[index - 1](tray_icon)

    def on_notify(wparam, lparam):
        if lparam in (win32.WM_LBUTTONUP, win32.WM_RBUTTONUP):
            try:
                show_menu()
            except Exception:
                logger.debug("Left-click menu popup failed", exc_info=True)
        return 0

    try:
        tray_icon._message_handlers[win32.WM_NOTIFY] = on_notify
    except Exception:
        logger.debug("Could not install left-click menu handler", exc_info=True)
