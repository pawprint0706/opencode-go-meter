"""Console API client for fetching OpenCode Go plan usage data."""

import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Optional, Tuple, Type, Union

import requests

logger = logging.getLogger(__name__)

CONSOLE_BASE = "https://opencode.ai"

USAGE_KEYS = ("rollingUsage", "weeklyUsage", "monthlyUsage")

# Zen credit balance is stored as dollars * 1e8 (integer) in the console SSR.
BALANCE_SCALE = 100_000_000

_REDIRECT_CODES = (301, 302, 303, 307, 308)


class ApiError(Exception):
    """Base class for console API errors."""


class AuthExpiredError(ApiError):
    """The session cookie is missing, invalid or expired."""


class FetchError(ApiError):
    """The console returned an unexpected HTTP response."""


class ParseError(ApiError):
    """The console HTML could not be parsed."""


@dataclass
class UsageData:
    rolling: dict
    weekly: dict
    monthly: dict
    use_balance: bool = False
    mine: bool = True
    balance: Optional[float] = None  # Zen credit balance in USD, or None


def _session(cookie: str) -> requests.Session:
    s = requests.Session()
    s.cookies.set("auth", cookie, domain="opencode.ai", path="/")
    return s


# --------------------------------------------------------------------------
# Seroval/JS object literal parser
#
# The console is a SolidStart app; usage data arrives embedded in the SSR
# HTML as seroval-serialized JS, e.g.:
#   rollingUsage:$R[12]={status:"ok",usagePercent:53,...}
# A real recursive parser (not a regex) reads the value directly following
# each key, so an error-state object can never swallow the next period's
# data, nested objects stay nested, and !0/!1 booleans parse correctly.
# --------------------------------------------------------------------------

_WS = " \t\r\n"
_NUM_RE = re.compile(r"-?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
_IDENT_RE = re.compile(r"[A-Za-z_$][\w$]*")
_KEY_RE = re.compile(r"[\w$]+")
_REF_ASSIGN_RE = re.compile(r"\$R\[\d+\]\s*=\s*")
_REF_RE = re.compile(r"\$R\[\d+\]")

_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}


def _skip_ws(s: str, i: int) -> int:
    while i < len(s) and s[i] in _WS:
        i += 1
    return i


def _parse_value(s: str, i: int) -> Tuple[Any, int]:
    """Parse one JS value starting at ``s[i]``; returns (value, next index).

    Supports objects, arrays, strings, numbers (incl. exponents), !0/!1
    booleans, null/undefined/void 0, and ``$R[n]=`` reference assignments.
    Bare ``$R[n]`` back-references cannot be resolved and become None.
    """
    i = _skip_ws(s, i)
    while True:
        m = _REF_ASSIGN_RE.match(s, i)
        if not m:
            break
        i = m.end()
    if i >= len(s):
        raise ParseError("unexpected end of input")
    c = s[i]
    if c == "{":
        return _parse_object(s, i)
    if c == "[":
        return _parse_array(s, i)
    if c in "\"'":
        return _parse_string(s, i)
    if c == "!" and i + 1 < len(s) and s[i + 1] in "01":
        return s[i + 1] == "0", i + 2
    if s.startswith("-Infinity", i):
        return None, i + len("-Infinity")
    m = _NUM_RE.match(s, i)
    if m:
        text = m.group(0)
        num = float(text)
        if num.is_integer() and "." not in text and "e" not in text.lower():
            return int(num), m.end()
        return num, m.end()
    m = _REF_RE.match(s, i)
    if m:
        return None, m.end()
    m = _IDENT_RE.match(s, i)
    if m:
        word, j = m.group(0), m.end()
        if word in ("null", "undefined", "NaN", "Infinity"):
            return None, j
        if word == "true":
            return True, j
        if word == "false":
            return False, j
        if word == "void":
            m2 = re.compile(r"\s*0").match(s, j)
            return None, (m2.end() if m2 else j)
        return word, j  # bare identifier -- keep as string
    raise ParseError(f"unexpected character at {i}: {s[i:i + 20]!r}")


def _parse_object(s: str, i: int) -> Tuple[dict, int]:
    obj: dict = {}
    i = _skip_ws(s, i + 1)  # skip '{'
    if i < len(s) and s[i] == "}":
        return obj, i + 1
    while i < len(s):
        i = _skip_ws(s, i)
        if i < len(s) and s[i] in "\"'":
            key, i = _parse_string(s, i)
        else:
            m = _KEY_RE.match(s, i)
            if not m:
                raise ParseError(f"bad object key at {i}: {s[i:i + 20]!r}")
            key, i = m.group(0), m.end()
        i = _skip_ws(s, i)
        if i >= len(s) or s[i] != ":":
            raise ParseError(f"expected ':' at {i}")
        val, i = _parse_value(s, i + 1)
        obj[key] = val
        i = _skip_ws(s, i)
        if i < len(s) and s[i] == ",":
            i += 1
            continue
        if i < len(s) and s[i] == "}":
            return obj, i + 1
        raise ParseError(f"expected ',' or '}}' at {i}")
    raise ParseError("unterminated object")


def _parse_array(s: str, i: int) -> Tuple[list, int]:
    arr: list = []
    i = _skip_ws(s, i + 1)  # skip '['
    if i < len(s) and s[i] == "]":
        return arr, i + 1
    while i < len(s):
        val, i = _parse_value(s, i)
        arr.append(val)
        i = _skip_ws(s, i)
        if i < len(s) and s[i] == ",":
            i += 1
            continue
        if i < len(s) and s[i] == "]":
            return arr, i + 1
        raise ParseError(f"expected ',' or ']' at {i}")
    raise ParseError("unterminated array")


def _parse_string(s: str, i: int) -> Tuple[str, int]:
    quote = s[i]
    i += 1
    out = []
    while i < len(s):
        c = s[i]
        if c == "\\":
            if i + 1 >= len(s):
                raise ParseError("unterminated escape")
            nxt = s[i + 1]
            if nxt in ("u", "x"):
                n = 4 if nxt == "u" else 2
                try:
                    out.append(chr(int(s[i + 2:i + 2 + n], 16)))
                except ValueError:
                    raise ParseError(f"bad \\{nxt} escape at {i}")
                i += 2 + n
                continue
            out.append(_ESCAPES.get(nxt, nxt))
            i += 2
            continue
        if c == quote:
            return "".join(out), i + 1
        out.append(c)
        i += 1
    raise ParseError("unterminated string")


def _find_and_parse(html: str, key: str, want: Type) -> Optional[Union[dict, bool]]:
    """Parse the value directly following each ``key:`` occurrence and
    return the first one of the wanted type (first non-empty dict, or
    first bool). Occurrences whose value fails to parse are skipped, so a
    loading/void placeholder earlier in the page cannot mask real data."""
    for m in re.finditer(rf"\b{re.escape(key)}[\"']?\s*:", html):
        try:
            val, _ = _parse_value(html, m.end())
        except (ParseError, IndexError):
            continue
        if want is bool:
            if isinstance(val, bool):
                return val
        elif isinstance(val, want) and val:
            return val
    return None


def _parse_solidjs_ssr(html: str) -> Optional[dict]:
    """Extract the Go subscription usage objects from SolidStart SSR HTML."""
    result: dict = {}
    for key in USAGE_KEYS:
        obj = _find_and_parse(html, key, dict)
        if obj:
            result[key] = obj

    # mine/useBalance are common words; only search near the usage block.
    anchor = html.find("rollingUsage")
    window = html[max(0, anchor - 5000):anchor + 5000] if anchor >= 0 else html
    for key in ("useBalance", "mine"):
        val = _find_and_parse(window, key, bool)
        if val is not None:
            result[key] = val

    return result or None


def _parse_zen_balance(html: str) -> Optional[float]:
    """Extract the Zen credit balance (USD) from the workspace home SSR HTML.

    The console embeds the billing object as seroval-serialized JS, e.g.
    ``customerID:"cus_...",balance:1613089290``. Balance is dollars * 1e8.
    Only a number directly following a ``balance:`` key counts, so a stray
    word elsewhere on the page cannot be mistaken for the value.
    """
    for m in re.finditer(rf"\bbalance[\"']?\s*:", html):
        try:
            val, _ = _parse_value(html, m.end())
        except (ParseError, IndexError):
            continue
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            continue
        return float(val) / BALANCE_SCALE
    return None


def find_nested_key(data: Any, key: str) -> Any:
    """Depth-first search for ``key`` in nested dicts/lists."""
    if isinstance(data, dict):
        if key in data:
            return data[key]
        for v in data.values():
            r = find_nested_key(v, key)
            if r is not None:
                return r
    elif isinstance(data, list):
        for v in data:
            r = find_nested_key(v, key)
            if r is not None:
                return r
    return None


# --------------------------------------------------------------------------
# HTTP layer
# --------------------------------------------------------------------------

def find_workspace_id(cookie: str) -> Optional[str]:
    """Discover the workspace ID via the /auth redirect.

    :raises AuthExpiredError: if the console redirects to the login page.
    :raises requests.RequestException: on network failure.
    """
    s = _session(cookie)
    resp = s.get(f"{CONSOLE_BASE}/auth", allow_redirects=False, timeout=10)
    if resp.status_code in _REDIRECT_CODES:
        location = resp.headers.get("location", "")
        m = re.search(r"/workspace/(wrk_[a-zA-Z0-9]+)", location)
        if m:
            return m.group(1)
        if "/auth/" in location:
            raise AuthExpiredError(f"not logged in (redirected to {location})")
        return None
    if resp.status_code == 200:
        m = re.search(r"wrk_[a-zA-Z0-9]+", resp.text)
        return m.group(0) if m else None
    logger.warning(f"/auth returned HTTP {resp.status_code}")
    return None


def fetch_usage(cookie: str, workspace_id: Optional[str] = None) -> UsageData:
    """Fetch Go plan usage data by parsing the console SSR HTML.

    :raises AuthExpiredError: when the session cookie is missing/expired.
    :raises FetchError: on unexpected HTTP status or missing workspace.
    :raises ParseError: when usage data cannot be located in the HTML
        (the page is dumped to the config dir for diagnosis).
    :raises requests.RequestException: on network failure.
    """
    if not cookie:
        raise AuthExpiredError("no session cookie")

    if workspace_id is None:
        workspace_id = find_workspace_id(cookie)
        if workspace_id is None:
            raise FetchError("could not discover workspace ID")

    s = _session(cookie)
    url = f"{CONSOLE_BASE}/workspace/{workspace_id}/go"
    resp = s.get(url, timeout=15, allow_redirects=False)

    if resp.status_code in _REDIRECT_CODES:
        location = resp.headers.get("location", "?")
        raise AuthExpiredError(f"session expired (redirected to {location})")
    if resp.status_code in (401, 403):
        raise AuthExpiredError(f"HTTP {resp.status_code}")
    if resp.status_code != 200:
        raise FetchError(f"Go page returned HTTP {resp.status_code}")

    data = _parse_solidjs_ssr(resp.text)
    if not data or not any(k in data for k in USAGE_KEYS):
        dump = _dump_html(resp.text)
        raise ParseError(f"no usage data found in HTML (page saved to {dump})")

    # The Zen credit balance lives on the workspace home page (not /go).
    # It's best-effort: a failure leaves balance as None without failing usage.
    balance: Optional[float] = None
    try:
        home_resp = s.get(
            f"{CONSOLE_BASE}/workspace/{workspace_id}", timeout=15, allow_redirects=False
        )
        if home_resp.status_code == 200:
            balance = _parse_zen_balance(home_resp.text)
    except requests.RequestException as e:
        logger.warning(f"Could not fetch Zen balance: {e}")

    return UsageData(
        rolling=data.get("rollingUsage", {}),
        weekly=data.get("weeklyUsage", {}),
        monthly=data.get("monthlyUsage", {}),
        use_balance=data.get("useBalance", False),
        mine=data.get("mine", True),
        balance=balance,
    )


def _dump_html(html: str) -> str:
    """Save the unparseable page for diagnosis; returns the path."""
    from . import config

    path = os.path.join(config.config_dir(), "last_fetch.html")
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
    except OSError as e:
        logger.warning(f"Could not dump HTML: {e}")
        return "<dump failed>"
    return path


def check_auth(cookie: str) -> Optional[bool]:
    """Check whether the session cookie is valid.

    Returns True (valid), False (invalid/expired) or None when the check
    could not be performed (network error / unexpected response). Callers
    must not treat None as an expired session.
    """
    if not cookie:
        return False
    s = _session(cookie)
    try:
        resp = s.get(f"{CONSOLE_BASE}/auth/status", timeout=10)
    except requests.RequestException as e:
        logger.warning(f"Auth check failed (network): {e}")
        return None
    if resp.status_code in (401, 403):
        return False
    if resp.status_code != 200:
        logger.warning(f"/auth/status returned HTTP {resp.status_code}")
        return None
    try:
        data = resp.json()
    except ValueError:
        return None
    return bool(data.get("account"))
