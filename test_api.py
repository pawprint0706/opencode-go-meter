#!/usr/bin/env python3
"""Step-by-step diagnostic for cookie extraction and the console API.

Run: .venv/bin/python test_api.py
"""

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    stream=sys.stderr,
)

import requests  # noqa: E402

from go_meter import api, auth, config  # noqa: E402


def test_cookie_extraction():
    print("\n=== 1. Cookie Extraction ===")
    cookie = auth.extract_auth_cookie(timeout=15)
    if cookie:
        print(f"SUCCESS: found auth cookie (len={len(cookie)})")
    else:
        print("No auth cookie found in browsers.")
        print("Windows note: Chrome/Edge 127+ cannot be auto-extracted; use Firefox or paste manually.")
    return cookie


def test_auth_check(cookie):
    print("\n=== 2. Auth Status ===")
    valid = api.check_auth(cookie)
    if valid is True:
        print("Cookie is VALID")
    elif valid is False:
        print("Cookie is INVALID or EXPIRED")
    else:
        print("Could not verify (network problem?) - continuing anyway")
    return valid


def test_workspace_discovery(cookie):
    print("\n=== 3. Workspace Discovery ===")
    try:
        ws_id = api.find_workspace_id(cookie)
    except api.AuthExpiredError as e:
        print(f"Auth expired: {e}")
        return None
    except requests.RequestException as e:
        print(f"Network error: {e}")
        return None
    print(f"Workspace ID: {ws_id}")
    return ws_id


def test_usage_fetch(cookie, workspace_id):
    print("\n=== 4. Usage Fetch ===")
    try:
        usage = api.fetch_usage(cookie, workspace_id)
    except api.AuthExpiredError as e:
        print(f"Auth expired: {e}")
        return None
    except api.ParseError as e:
        print(f"Parse failed: {e}")
        print("Inspect the saved HTML to see what the console returned.")
        return None
    except (api.ApiError, requests.RequestException) as e:
        print(f"Fetch failed: {type(e).__name__}: {e}")
        return None
    print(f"Rolling: {usage.rolling}")
    print(f"Weekly:  {usage.weekly}")
    print(f"Monthly: {usage.monthly}")
    print(f"use_balance={usage.use_balance} mine={usage.mine}")
    return usage


def main():
    cfg = config.load_config()

    cookie = cfg.session_cookie
    if cookie:
        print(f"Using saved cookie (len={len(cookie)})")
    else:
        cookie = test_cookie_extraction()

    if not cookie and sys.stdin.isatty():
        try:
            cookie = auth.clean_cookie(input("\nPaste auth cookie (Enter to skip): "))
        except (EOFError, KeyboardInterrupt):
            cookie = None

    if not cookie:
        print("\nNo cookie available. Log in at https://opencode.ai/auth and re-run.")
        return

    valid = test_auth_check(cookie)
    if valid is False:
        print("Re-login needed; clearing saved session.")
        cfg.session_cookie = None
        cfg.workspace_id = None
        config.save_config(cfg)
        return

    cfg.session_cookie = cookie
    ws_id = cfg.workspace_id or test_workspace_discovery(cookie)
    if not ws_id:
        print("Could not discover workspace ID.")
        return
    cfg.workspace_id = ws_id
    config.save_config(cfg)

    test_usage_fetch(cookie, ws_id)


if __name__ == "__main__":
    main()
