"""Tiny localization: Korean if the OS prefers Korean, otherwise English.

Usage:  from .i18n import tr;  tr("한국어 문구", "English text")

Language resolution order:
  1. GO_METER_LANG env var ("ko" | "en") — explicit override (also used by tests)
  2. macOS preferred language (Foundation.NSLocale.preferredLanguages)
  3. Windows user UI language (GetUserDefaultUILanguage)
  4. POSIX locale env vars (LANGUAGE / LC_ALL / LC_MESSAGES / LANG)
  5. fallback: English
"""

import logging
import os
import sys

logger = logging.getLogger(__name__)

_detected = None


def _detect():
    if sys.platform == "darwin":
        try:
            from Foundation import NSLocale

            langs = NSLocale.preferredLanguages()
            if langs and len(langs):
                return "ko" if str(langs[0]).lower().startswith("ko") else "en"
        except Exception:  # noqa: BLE001 — Foundation unavailable
            logger.debug("NSLocale language detection failed", exc_info=True)
    if sys.platform == "win32":
        # POSIX locale vars are usually unset on Windows; ask the OS for the
        # user's UI language. LANG_KOREAN primary id == 0x12.
        try:
            import ctypes

            langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            return "ko" if (langid & 0x3FF) == 0x12 else "en"
        except Exception:  # noqa: BLE001
            logger.debug("Windows language detection failed", exc_info=True)
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        val = os.environ.get(var)
        if val:
            return "ko" if val.lower().startswith("ko") else "en"
    return "en"


def current_lang() -> str:
    override = os.environ.get("GO_METER_LANG")
    if override in ("ko", "en"):
        return override
    global _detected
    if _detected is None:
        _detected = _detect()
    return _detected


def tr(ko: str, en: str) -> str:
    """Return `ko` when the UI language is Korean, else `en`."""
    return ko if current_lang() == "ko" else en
