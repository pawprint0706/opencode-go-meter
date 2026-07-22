"""System tray application for monitoring OpenCode Go plan usage.

pystray notes that shape this module:
  - ``MenuItem.text`` is a read-only property and ``Icon`` has no
    run-on-main-thread helper. The supported way to change a menu is to
    hand ``pystray.Menu`` a *callable* that regenerates the items and
    call ``icon.update_menu()`` after every state change.
  - ``Icon.run()`` must own the process main thread, so all work
    (fetching, login polling) happens on daemon threads.
"""

import logging
import os
import sys
import threading
import time
import webbrowser
from typing import Optional

import pystray
import requests

from . import api, auth, autostart, config, icon
from .i18n import tr

logger = logging.getLogger(__name__)

# Refresh-interval choices, in minutes; the menu label is localized at render.
REFRESH_OPTIONS = [5, 10, 30, 60]

# (Korean label, English label, UsageData attribute == config limits key)
PERIODS = [
    ("5시간", "5h", "rolling"),
    ("주간", "Week", "weekly"),
    ("월간", "Month", "monthly"),
]

LOGIN_WAIT_SECONDS = 180
LOGIN_POLL_SECONDS = 6
UI_TICK_SECONDS = 60
# Windows: how often to re-check the taskbar theme so the icon ink follows a
# live light/dark switch (macOS handles this via the template flag instead).
THEME_TICK_SECONDS = 5


def _macos_notify(title: str, message: str) -> bool:
    """Post a native banner owned by THIS process. Returns True on success.

    pystray's macOS notify shells out to `osascript -e 'display notification'`,
    whose banner is owned by Script Editor — so clicking it launches Script
    Editor. Delivering via NSUserNotificationCenter makes our own process the
    owner instead, so the banner just dismisses on click (no app is launched).
    NSUserNotification is deprecated but still the only no-bundle-required path.
    """
    try:
        from Foundation import NSUserNotification, NSUserNotificationCenter

        center = NSUserNotificationCenter.defaultUserNotificationCenter()
        if center is None:
            return False
        note = NSUserNotification.alloc().init()
        note.setTitle_(title)
        note.setInformativeText_(message)
        center.deliverNotification_(note)
        return True
    except Exception:
        logger.debug("Native notification failed", exc_info=True)
        return False


def _hide_dock_icon():
    """macOS: run as a menu-bar-only (accessory) app so no Dock icon or app
    menu appears — only the tray icon. Must run on the main thread before the
    tray loop starts. Accessory (policy 1), not Prohibited (2), so any future
    dialogs still work. No-op off macOS or if AppKit is unavailable."""
    if sys.platform != "darwin":
        return
    try:
        from AppKit import NSApplication

        NSApplication.sharedApplication().setActivationPolicy_(1)
    except Exception:
        logger.debug("Could not set accessory activation policy", exc_info=True)


def _fmt_duration(sec: float) -> str:
    """Compact localized duration: '3h 39m' / '3시간 39분'."""
    m_u, h_u, d_u = tr("분", "m"), tr("시간", "h"), tr("일", "d")
    sec = int(sec)
    if sec < 60:
        return tr("<1분", "<1m")
    minutes = sec // 60
    if minutes < 60:
        return f"{minutes}{m_u}"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}{h_u} {minutes}{m_u}" if minutes else f"{hours}{h_u}"
    days, hours = divmod(hours, 24)
    return f"{days}{d_u} {hours}{h_u}" if hours else f"{days}{d_u}"


class GoMeterApp:
    def __init__(self):
        self.cfg = config.load_config()
        self.usage: Optional[api.UsageData] = None
        self.status = tr("시작하는 중...", "Starting...")
        self.tray_icon: Optional[pystray.Icon] = None
        self._running = False
        self._timer: Optional[threading.Timer] = None
        self._ui_timer: Optional[threading.Timer] = None
        self._theme_timer: Optional[threading.Timer] = None
        # Last taskbar theme the Windows icon was rendered for (None = unknown).
        self._icon_is_light: Optional[bool] = None
        self._timer_lock = threading.Lock()
        self._fetching = threading.Lock()
        self._login_in_progress = False
        # monotonic timestamp of the last successful fetch; lets the menu
        # count resetInSec down between refreshes
        self._fetched_at: Optional[float] = None

    @property
    def is_logged_in(self) -> bool:
        return bool(self.cfg.session_cookie)

    # ------------------------------------------------------------- lifecycle

    def run(self):
        """Create the tray icon and block until quit (main thread only)."""
        self._running = True
        self.tray_icon = pystray.Icon(
            "opencode_go_meter",
            icon.get_icon(),
            "OpenCode Go Meter",
            pystray.Menu(self._menu_items),
        )
        if sys.platform == "win32":
            # Record the theme the first icon was rendered for so the theme
            # watcher only re-renders on an actual change.
            self._icon_is_light = icon._is_light_theme()
        # pystray created the shared NSApplication above; make it an accessory
        # app before the run loop starts so no Python Dock icon appears.
        _hide_dock_icon()
        self.tray_icon.run(setup=self._on_ready)

    def _on_ready(self, tray_icon):
        # pystray joins the setup thread on stop, so return immediately
        # and do the real startup work on our own daemon thread.
        tray_icon.visible = True
        # visible=True makes pystray build the NSImage; now flag it as a
        # template so macOS recolors the monochrome logo for light/dark bars.
        icon.apply_macos_template(tray_icon)
        if sys.platform == "win32":
            # A left click should open the menu too (pystray shows it only on
            # right click), and the ink must follow live taskbar-theme changes.
            from . import win_tray

            win_tray.enable_left_click_menu(tray_icon)
            self._schedule_theme_tick()
        threading.Thread(target=self._startup_worker, daemon=True).start()

    def _startup_worker(self):
        autostart.refresh_if_stale()
        if self.is_logged_in:
            self.status = ""
            self._refresh_usage()
            return
        if os.environ.get("GO_METER_NO_AUTO") == "1":
            self.status = tr("로그인되지 않음", "Not logged in")
            self._update_ui()
            return
        self.status = tr("브라우저 세션을 찾는 중...", "Looking for a browser session...")
        self._update_ui()
        cookie = auth.extract_auth_cookie(timeout=15)
        if cookie:
            logger.info("Auto-login via browser cookie")
            self._set_session(cookie)
        else:
            self.status = tr("로그인되지 않음", "Not logged in")
            self._update_ui()

    def stop(self):
        self._running = False
        with self._timer_lock:
            if self._timer:
                self._timer.cancel()
                self._timer = None
            if self._ui_timer:
                self._ui_timer.cancel()
                self._ui_timer = None
            if self._theme_timer:
                self._theme_timer.cancel()
                self._theme_timer = None
        if self.tray_icon:
            self.tray_icon.stop()

    # ------------------------------------------------------------------ menu

    def _menu_items(self):
        """Generate menu items; re-evaluated on every update_menu()."""
        items = []
        # Keep usage lines enabled so they remain readable on macOS and can
        # open the corresponding console page when clicked.
        if self.is_logged_in:
            for ko, en, key in PERIODS:
                items.append(
                    pystray.MenuItem(
                        self._usage_line(tr(ko, en), key),
                        self._on_open_usage,
                    )
                )
            balance_line = self._balance_line()
            if balance_line:
                items.append(
                    pystray.MenuItem(
                        balance_line,
                        self._on_open_balance,
                    )
                )
        if self.status:
            items.append(pystray.MenuItem(self.status, None))
        items.append(pystray.Menu.SEPARATOR)
        if self.is_logged_in:
            items.append(
                pystray.MenuItem(tr("지금 새로고침", "Refresh Now"), self._on_refresh)
            )
            items.append(
                pystray.MenuItem(
                    tr("사용량 페이지 열기", "Open Usage Page"),
                    self._on_open_usage_page,
                )
            )
        items.append(
            pystray.MenuItem(
                tr("새로고침 주기", "Refresh Interval"),
                pystray.Menu(*[
                    pystray.MenuItem(
                        tr(f"{minutes}분", f"{minutes} min"),
                        self._make_interval_setter(minutes),
                        checked=lambda item, m=minutes: self.cfg.refresh_interval == m,
                        radio=True,
                    )
                    for minutes in REFRESH_OPTIONS
                ]),
            )
        )
        items.append(
            pystray.MenuItem(
                tr("로그인 시 자동 시작", "Start at Login"),
                self._on_toggle_autostart,
                checked=lambda item: autostart.is_enabled(),
            )
        )
        items.append(pystray.Menu.SEPARATOR)
        if self.is_logged_in:
            items.append(pystray.MenuItem(tr("로그아웃", "Logout"), self._on_logout))
        else:
            items.append(
                pystray.MenuItem(
                    tr("브라우저로 로그인...", "Log in via Browser..."),
                    self._on_login,
                    enabled=not self._login_in_progress,
                )
            )
            items.append(
                pystray.MenuItem(
                    tr("클립보드에서 쿠키 붙여넣기", "Paste Cookie from Clipboard"),
                    self._on_paste_cookie,
                )
            )
            items.append(
                pystray.MenuItem(
                    tr("쿠키 직접 입력...", "Enter Cookie..."), self._on_enter_cookie
                )
            )
        items.append(pystray.Menu.SEPARATOR)
        items.append(pystray.MenuItem(tr("종료", "Quit"), self._on_quit))
        return items

    @staticmethod
    def _pct(d: Optional[dict]) -> Optional[float]:
        """usagePercent from a usage object, searching nested dicts too."""
        if not isinstance(d, dict):
            return None
        v = d.get("usagePercent")
        if v is None:
            v = api.find_nested_key(d, "usagePercent")
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
        return float(v)

    def _remaining_sec(self, d: Optional[dict]) -> Optional[float]:
        """Seconds until this window resets, counted down since the fetch."""
        if not isinstance(d, dict):
            return None
        v = d.get("resetInSec")
        if v is None:
            v = api.find_nested_key(d, "resetInSec")
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
        elapsed = time.monotonic() - self._fetched_at if self._fetched_at else 0.0
        return max(0.0, float(v) - elapsed)

    def _balance_line(self) -> Optional[str]:
        """Zen credit balance line, or None when no balance is available."""
        bal = getattr(self.usage, "balance", None) if self.usage else None
        if bal is None:
            return None
        label = tr("잔액", "Balance")
        return f"{label}: ${bal:.2f}"

    def _usage_line(self, label: str, key: str) -> str:
        d = getattr(self.usage, key, None) if self.usage else None
        if not d:
            return f"{label}: ..."
        pct = self._pct(d)
        if pct is None:
            return f"{label}: {d.get('status', 'n/a')}"
        limit = float(self.cfg.limits.get(key, 0))
        used = pct / 100.0 * limit
        line = f"{label}: ${used:.2f} / ${limit:.0f} ({pct:.0f}%)"
        remaining = self._remaining_sec(d)
        if remaining is not None:
            dur = _fmt_duration(remaining)
            line += tr(f" · {dur} 후 초기화", f" · resets in {dur}")
        status = d.get("status")
        if status not in (None, "ok"):
            line += f" [{status}]"
        return line

    def _tooltip(self) -> str:
        if not (self.usage and self.is_logged_in):
            return "OpenCode Go Meter"
        parts = []
        for ko, en, key in PERIODS:
            label = tr(ko, en)
            pct = self._pct(getattr(self.usage, key, None))
            parts.append(f"{label} {pct:.0f}%" if pct is not None else f"{label} ?")
        tip = "Go " + " | ".join(parts)
        bal = getattr(self.usage, "balance", None)
        if bal is not None:
            tip += tr(f" | 잔액 ${bal:.2f}", f" | Bal ${bal:.2f}")
        return tip

    def _update_ui(self):
        tray = self.tray_icon
        if not tray:
            return
        try:
            tray.title = self._tooltip()
            tray.update_menu()
        except Exception:
            logger.exception("Tray UI update failed")

    def _notify(self, message: str):
        title = "OpenCode Go Meter"
        # macOS: post our own banner so clicking it doesn't launch Script Editor
        # (pystray's osascript-based toast is owned by Script Editor).
        if sys.platform == "darwin" and _macos_notify(title, message):
            return
        tray = self.tray_icon
        if not tray:
            return
        try:
            if tray.HAS_NOTIFICATION:
                tray.notify(message, title)
        except Exception:
            logger.warning("Notification failed", exc_info=True)

    # --------------------------------------------------------------- actions

    def _on_open_usage(self):
        self._open_workspace_page("/go")

    def _on_open_balance(self):
        self._open_workspace_page()

    def _on_open_usage_page(self):
        self._open_workspace_page("/usage")

    def _open_workspace_page(self, suffix: str = ""):
        workspace_id = self.cfg.workspace_id
        if not workspace_id:
            logger.warning("Cannot open console page: workspace ID is unavailable")
            return
        url = f"{api.CONSOLE_BASE}/workspace/{workspace_id}{suffix}"
        logger.info("Opening browser: %s", url)
        try:
            if not webbrowser.open(url, new=2):
                logger.warning("The default browser did not accept the URL")
        except Exception:
            logger.exception("Could not open browser")

    def _make_interval_setter(self, minutes: int):
        def setter():
            self.cfg.refresh_interval = minutes
            config.save_config(self.cfg)
            self._schedule_next_refresh()
        return setter

    def _on_refresh(self):
        self._refresh_usage()

    def _on_toggle_autostart(self):
        try:
            if autostart.is_enabled():
                autostart.disable()
            else:
                autostart.enable()
        except Exception as e:
            logger.exception("Autostart toggle failed")
            self.status = tr("자동 시작 변경 실패 - 로그 확인", "Autostart change failed - see log")
            self._notify(tr(f"로그인 항목을 업데이트하지 못했습니다: {e}",
                            f"Could not update the login item: {e}"))
        self._update_ui()

    def _on_quit(self):
        self.stop()

    def _on_logout(self):
        self._clear_session(tr("로그인되지 않음", "Not logged in"))

    def _on_login(self):
        if self._login_in_progress:
            return
        self._login_in_progress = True
        self._update_ui()
        webbrowser.open(f"{api.CONSOLE_BASE}/auth")
        threading.Thread(target=self._login_worker, daemon=True).start()

    def _login_worker(self):
        try:
            deadline = time.monotonic() + LOGIN_WAIT_SECONDS
            cookie = None
            while self._running and time.monotonic() < deadline:
                remaining = int(deadline - time.monotonic())
                self.status = tr(f"브라우저 로그인 대기 중... ({remaining}초)",
                                 f"Waiting for browser login... ({remaining}s)")
                self._update_ui()
                cookie = auth.extract_auth_cookie(timeout=10)
                if cookie:
                    break
                time.sleep(LOGIN_POLL_SECONDS)
            if not cookie and self._running:
                self.status = tr("쿠키를 자동으로 찾지 못했습니다 - 대화상자에 붙여넣으세요",
                                 "Could not auto-detect the cookie - paste it in the dialog")
                self._update_ui()
                cookie = auth.prompt_cookie_dialog()
            if cookie:
                self._set_session(cookie)
            elif self._running:
                self.status = tr("로그인 실패 - '클립보드에서 쿠키 붙여넣기'를 사용하세요",
                                 "Login failed - try 'Paste Cookie from Clipboard'")
                self._update_ui()
                self._notify(tr(
                    "auth 쿠키를 읽지 못했습니다. DevTools에서 값을 복사한 뒤"
                    "(README 참고) '클립보드에서 쿠키 붙여넣기'를 사용하세요.",
                    "Could not read the auth cookie. Copy it from DevTools "
                    "(see README), then use 'Paste Cookie from Clipboard'."
                ))
        finally:
            self._login_in_progress = False
            self._update_ui()

    def _on_paste_cookie(self):
        threading.Thread(target=self._paste_worker, daemon=True).start()

    def _paste_worker(self):
        cookie = auth.clean_cookie(auth.read_clipboard())
        if not cookie:
            self.status = tr("클립보드가 auth 쿠키 형식이 아닙니다",
                             "Clipboard does not look like an auth cookie")
            self._update_ui()
            return
        self._set_session(cookie)

    def _on_enter_cookie(self):
        def worker():
            cookie = auth.prompt_cookie_dialog()
            if cookie:
                self._set_session(cookie)
        threading.Thread(target=worker, daemon=True).start()

    # ---------------------------------------------------------------- session

    def _set_session(self, cookie: str):
        cookie = auth.clean_cookie(cookie)
        if not cookie:
            self.status = tr("잘못된 쿠키 값", "Invalid cookie value")
            self._update_ui()
            return
        valid = api.check_auth(cookie)
        if valid is False:
            self.status = tr("콘솔이 쿠키를 거부했습니다 - 'auth' 쿠키가 맞나요?",
                             "Cookie rejected by the console - is it the 'auth' cookie?")
            self._update_ui()
            return
        # valid is True or None (network unknown): keep it and let the
        # refresh cycle report a definitive answer.
        self.cfg.session_cookie = cookie
        config.save_config(self.cfg)
        self.status = tr("로그인됨", "Logged in")
        self._update_ui()
        self._refresh_usage()

    def _clear_session(self, message: str):
        self.cfg.session_cookie = None
        self.cfg.workspace_id = None
        self.usage = None
        self._fetched_at = None
        config.save_config(self.cfg)
        with self._timer_lock:
            if self._ui_timer:
                self._ui_timer.cancel()
                self._ui_timer = None
        self.status = message
        self._update_ui()

    # --------------------------------------------------------------- refresh

    def _refresh_usage(self):
        if not self.cfg.session_cookie:
            return
        threading.Thread(target=self._fetch_worker, daemon=True).start()

    def _fetch_worker(self):
        if not self._fetching.acquire(blocking=False):
            return  # a fetch is already in flight
        try:
            self.status = tr("가져오는 중...", "Fetching...")
            self._update_ui()
            if not self.cfg.workspace_id:
                self.cfg.workspace_id = api.find_workspace_id(self.cfg.session_cookie)
                if self.cfg.workspace_id:
                    config.save_config(self.cfg)
                else:
                    self.status = tr("이 계정에 워크스페이스가 없습니다",
                                     "No workspace found for this account")
                    return
            self.usage = api.fetch_usage(self.cfg.session_cookie, self.cfg.workspace_id)
            self._fetched_at = time.monotonic()
            self.status = time.strftime(tr("%H:%M 업데이트됨", "Updated %H:%M"))
        except api.AuthExpiredError:
            logger.warning("Session expired; logging out")
            self._clear_session(tr("세션 만료 - 다시 로그인하세요",
                                   "Session expired - log in again"))
            self._notify(tr("OpenCode 세션이 만료되었습니다. 메뉴를 열어 다시 로그인하세요.",
                            "OpenCode session expired. Open the menu to log in again."))
        except requests.RequestException as e:
            # Transient network problems must never clear the session.
            logger.warning(f"Network error: {e}")
            self.status = tr("네트워크 오류 - 재시도 예정", "Network error - will retry")
        except api.ParseError as e:
            logger.error(f"Parse error: {e}")
            self.status = tr("콘솔에서 사용량을 읽지 못했습니다 - 로그 확인",
                             "Could not read usage from console - see log")
        except api.FetchError as e:
            logger.error(f"Fetch error: {e}")
            if "404" in str(e):
                # Workspace gone/renamed: rediscover on the next cycle.
                self.cfg.workspace_id = None
                config.save_config(self.cfg)
            self.status = str(e)
        except Exception:
            logger.exception("Unexpected error during refresh")
            self.status = tr("예기치 않은 오류 - 로그 확인", "Unexpected error - see log")
        finally:
            self._fetching.release()
            self._schedule_next_refresh()
            self._schedule_ui_tick()
            self._update_ui()

    def _schedule_next_refresh(self):
        if not self._running or not self.is_logged_in:
            return
        with self._timer_lock:
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(
                self.cfg.refresh_interval * 60, self._refresh_usage
            )
            self._timer.daemon = True
            self._timer.start()

    def _schedule_ui_tick(self):
        """Re-render the menu every minute so the reset countdowns stay
        current between fetches (no network involved)."""
        if not self._running or not self.is_logged_in or not self.usage:
            return
        with self._timer_lock:
            if self._ui_timer:
                self._ui_timer.cancel()
            self._ui_timer = threading.Timer(UI_TICK_SECONDS, self._ui_tick)
            self._ui_timer.daemon = True
            self._ui_timer.start()

    def _ui_tick(self):
        self._update_ui()
        self._schedule_ui_tick()

    # ----------------------------------------------------------- theme (win32)

    def _schedule_theme_tick(self):
        """Windows: poll the taskbar theme so the icon ink can follow a live
        light/dark switch. Runs regardless of login state (unlike the UI tick),
        since the icon is visible even when logged out."""
        if not self._running or sys.platform != "win32":
            return
        with self._timer_lock:
            if self._theme_timer:
                self._theme_timer.cancel()
            self._theme_timer = threading.Timer(
                THEME_TICK_SECONDS, self._theme_tick
            )
            self._theme_timer.daemon = True
            self._theme_timer.start()

    def _theme_tick(self):
        self._refresh_icon_for_theme()
        self._schedule_theme_tick()

    def _refresh_icon_for_theme(self):
        """Re-render the tray icon if the taskbar theme changed. On Windows the
        ink color is baked in at render time (no template), so without this the
        glyph keeps its startup color after a light/dark switch."""
        if sys.platform != "win32" or not self.tray_icon:
            return
        light = icon._is_light_theme()
        if light == self._icon_is_light:
            return
        self._icon_is_light = light
        try:
            self.tray_icon.icon = icon.get_icon()
        except Exception:
            logger.debug("Tray icon theme refresh failed", exc_info=True)
