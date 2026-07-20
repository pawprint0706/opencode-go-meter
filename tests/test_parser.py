#!/usr/bin/env python3
"""Regression tests for the SSR parser (plain asserts, no test framework).

Run: .venv/bin/python tests/test_parser.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from go_meter.api import (  # noqa: E402
    ParseError,
    _parse_solidjs_ssr,
    _parse_value,
    _parse_zen_balance,
    find_nested_key,
)


def parse(s):
    val, _ = _parse_value(s, 0)
    return val


def test_scalars():
    assert parse("53") == 53
    assert parse("-7") == -7
    assert parse("-12.5") == -12.5          # regression: int("-12.5") crash
    assert parse("1.5e-7") == 1.5e-7        # exponent support
    assert parse("!0") is True              # regression: seroval booleans
    assert parse("!1") is False
    assert parse("null") is None
    assert parse("undefined") is None
    assert parse("void 0") is None
    assert parse('"a\\"b\\nc"') == 'a"b\nc'
    assert parse('"\\uD55C"') == "한"


def test_structures():
    # regression: nested objects must stay nested (no flatten/overwrite)
    obj = parse('{status:"ok",usage:{amount:6.3},limit:{amount:12}}')
    assert obj["usage"]["amount"] == 6.3
    assert obj["limit"]["amount"] == 12
    assert parse("[1,!0,null,{a:2}]") == [1, True, None, {"a": 2}]
    assert parse("$R[4]={a:$R[5]=1,b:$R[6]}") == {"a": 1, "b": None}
    assert parse("{}") == {}
    assert parse('{"quoted":1,bare:2}') == {"quoted": 1, "bare": 2}


def test_ssr_extraction():
    # field shape matches the live console as of 2026-07:
    # {status:"ok", resetInSec:13174, usagePercent:16}
    html = (
        'x;mine:!0,useBalance:!1,region:"us",'
        'rollingUsage:$R[1]={status:"ok",resetInSec:13174,usagePercent:53,cost:-0.5},'
        'weeklyUsage:$R[2]={status:"ok",usagePercent:20},'
        'monthlyUsage:$R[3]={status:"ok",usagePercent:10}}more'
    )
    r = _parse_solidjs_ssr(html)
    assert r["rollingUsage"]["usagePercent"] == 53
    assert r["rollingUsage"]["resetInSec"] == 13174
    assert r["rollingUsage"]["cost"] == -0.5
    assert r["weeklyUsage"]["usagePercent"] == 20
    assert r["monthlyUsage"]["usagePercent"] == 10
    assert r["useBalance"] is False   # regression: !1 parsed as True before
    assert r["mine"] is True


def test_no_cross_contamination():
    # regression: an error-state rolling object must not steal weekly's data
    html = (
        'rollingUsage:$R[1]={status:"error",message:"x"},'
        'weeklyUsage:$R[2]={status:"ok",usagePercent:20}}'
    )
    r = _parse_solidjs_ssr(html)
    assert r["rollingUsage"] == {"status": "error", "message": "x"}
    assert r["weeklyUsage"]["usagePercent"] == 20


def test_skips_unparseable_occurrences():
    # a loading placeholder earlier in the page must not mask real data
    html = (
        'rollingUsage:void 0,junk:1,'
        'rollingUsage:{status:"ok",usagePercent:7}}'
    )
    r = _parse_solidjs_ssr(html)
    assert r["rollingUsage"]["usagePercent"] == 7


def test_nested_percent_lookup():
    assert find_nested_key({"a": {"b": {"usagePercent": 42}}}, "usagePercent") == 42
    assert find_nested_key({"a": [1, {"usagePercent": 9}]}, "usagePercent") == 9
    assert find_nested_key({"a": 1}, "usagePercent") is None


def test_garbage_does_not_crash():
    assert _parse_solidjs_ssr("<html>login page</html>") is None
    try:
        parse("###")
        raise AssertionError("expected ParseError")
    except ParseError:
        pass


def test_zen_balance():
    # balance is dollars * 1e8, embedded in the billing SSR object
    html = (
        '$R[36]($R[16],$R[274]={customerID:"cus_x",'
        'balance:1613089290,reload:null,reloadAmount:20,monthlyLimit:30});'
    )
    assert _parse_zen_balance(html) == 16.1308929
    # only a number directly after balance: counts
    assert _parse_zen_balance('balance:"nope",cost:1') is None
    # integer and float both supported
    assert _parse_zen_balance("balance:500000000") == 5.0
    assert _parse_zen_balance("balance:0") == 0.0


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                failures += 1
                print(f"FAIL {name}: {e}")
    sys.exit(1 if failures else 0)
