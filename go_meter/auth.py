"""Browser cookie extraction and manual cookie entry for the console."""

import json
import logging
import platform
import subprocess
import sys
from typing import Optional

from .i18n import tr

logger = logging.getLogger(__name__)


def _cookie_instructions() -> str:
    return tr(
        "1. https://opencode.ai/auth 에서 로그인하세요\n"
        "2. 개발자 도구(F12) -> 애플리케이션/저장소 -> 쿠키 -> https://opencode.ai\n"
        "3. 'auth' 쿠키의 값을 복사하세요",
        "1. Log in at https://opencode.ai/auth\n"
        "2. Open DevTools (F12) -> Application/Storage -> Cookies -> https://opencode.ai\n"
        "3. Copy the value of the 'auth' cookie",
    )


def _browser_order():
    """Ordered (name, browser_cookie3 loader expression) per platform.

    Firefox first everywhere: its cookie store is unencrypted, so no
    keychain/DPAPI interaction is needed. On Windows, Chrome/Edge 127+
    use App-Bound Encryption which browser_cookie3 cannot decrypt, so
    those are tried last and usually fail -- Firefox or manual paste are
    the realistic paths there. On macOS, Chrome triggers a one-time
    Keychain consent prompt and Safari needs Full Disk Access.
    """
    os_name = platform.system()
    if os_name == "Windows":
        return [
            ("firefox", "browser_cookie3.firefox"),
            ("edge", "browser_cookie3.edge"),
            ("chrome", "browser_cookie3.chrome"),
        ]
    if os_name == "Darwin":
        return [
            ("firefox", "browser_cookie3.firefox"),
            ("chrome", "browser_cookie3.chrome"),
            ("safari", "browser_cookie3.safari"),
            ("edge", "browser_cookie3.edge"),
        ]
    return [
        ("firefox", "browser_cookie3.firefox"),
        ("chrome", "browser_cookie3.chrome"),
        ("chromium", "browser_cookie3.chromium"),
        ("edge", "browser_cookie3.edge"),
    ]


def extract_auth_cookie(timeout: int = 10) -> Optional[str]:
    """Extract the auth cookie from an installed browser.

    Runs in a subprocess so that browser DB locks, keychain prompts or
    decryption hangs can never freeze the tray app.
    """
    browsers = _browser_order()
    browsers_str = "[" + ",".join(f'("{n}",{l})' for n, l in browsers) + "]"

    script = f"""import browser_cookie3, json, sys

def try_extract(loader, name):
    try:
        cj = loader(domain_name='opencode.ai')
        for c in cj:
            if c.name == 'auth' and c.value:
                print(json.dumps({{'browser': name, 'cookie': c.value}}))
                sys.exit(0)
    except Exception:
        pass

browsers = {browsers_str}
for name, loader in browsers:
    try_extract(loader, name)

try:
    cj = browser_cookie3.load(domain_name='opencode.ai')
    for c in cj:
        if c.name == 'auth' and c.value:
            print(json.dumps({{'browser': 'load()', 'cookie': c.value}}))
            sys.exit(0)
except Exception:
    pass
"""

    try:
        proc = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            data = json.loads(proc.stdout.strip())
            cookie = clean_cookie(data.get("cookie"))
            if cookie:
                logger.info(f"Cookie extracted from {data.get('browser', '?')}")
                return cookie
    except subprocess.TimeoutExpired:
        logger.warning(f"Cookie extraction timed out after {timeout}s")
    except Exception as e:
        logger.warning(f"Cookie extraction error: {e}")

    return None


def clean_cookie(value: Optional[str]) -> Optional[str]:
    """Normalize a (possibly pasted) cookie value; None if implausible."""
    if not value:
        return None
    v = value.strip().strip('"').strip("'").strip()
    if v.lower().startswith("auth="):
        v = v[len("auth="):]
    if len(v) < 20 or any(ch.isspace() for ch in v):
        return None
    return v


_DIALOG_SCRIPT = """\
import sys
import tkinter as tk
from tkinter import simpledialog

root = tk.Tk()
root.withdraw()
try:
    root.attributes("-topmost", True)
except Exception:
    pass
value = simpledialog.askstring("OpenCode Go Meter", sys.argv[1], parent=root)
root.destroy()
if value and value.strip():
    print(value.strip())
"""


def prompt_cookie_dialog(timeout: int = 300) -> Optional[str]:
    """Show a paste-your-cookie dialog and return the cleaned value.

    Tk must own the main thread of its process, and the tray app's main
    thread belongs to pystray -- so the dialog runs in a subprocess,
    which makes it safe to call from any thread on every platform.
    """
    msg = tr("'auth' 쿠키를 붙여넣으세요:\n\n", "Paste your 'auth' cookie:\n\n") \
        + _cookie_instructions()
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _DIALOG_SCRIPT, msg],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        logger.warning(f"Cookie dialog failed: {e}")
        return None
    if proc.returncode != 0:
        logger.warning(f"Cookie dialog unavailable: {proc.stderr.strip()[:200]}")
        return None
    return clean_cookie(proc.stdout)


def read_clipboard() -> Optional[str]:
    """Read the system clipboard using OS-native tools (no extra deps)."""
    os_name = platform.system()
    if os_name == "Darwin":
        cmd = ["pbpaste"]
    elif os_name == "Windows":
        cmd = ["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"]
    else:
        cmd = ["xclip", "-selection", "clipboard", "-o"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.warning(f"Clipboard read failed: {e}")
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None
