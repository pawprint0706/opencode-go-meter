"""Application configuration management."""

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_LIMITS = {
    "rolling": 12.0,   # $ per 5-hour window
    "weekly": 30.0,    # $ per week
    "monthly": 60.0,   # $ per month
}


@dataclass
class Config:
    workspace_id: Optional[str] = None
    session_cookie: Optional[str] = None
    refresh_interval: int = 10  # minutes
    limits: dict = field(default_factory=lambda: dict(DEFAULT_LIMITS))


def config_dir() -> str:
    path = os.path.expanduser("~/.opencode-go-meter")
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def config_path() -> str:
    return os.path.join(config_dir(), "config.json")


def load_config() -> Config:
    path = config_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return Config()
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Config file unreadable ({e}); starting with defaults")
        return Config()

    raw_limits = data.get("limits") or {}
    try:
        limits = {k: float(raw_limits.get(k, v)) for k, v in DEFAULT_LIMITS.items()}
    except (TypeError, ValueError):
        limits = dict(DEFAULT_LIMITS)

    try:
        interval = int(data.get("refresh_interval") or 10)
    except (TypeError, ValueError):
        interval = 10

    return Config(
        workspace_id=data.get("workspace_id"),
        session_cookie=data.get("session_cookie"),
        refresh_interval=interval,
        limits=limits,
    )


def save_config(config: Config):
    """Atomically write the config with owner-only permissions.

    The file holds the session cookie, so 0600 matters (best effort on
    Windows, where POSIX mode bits are ignored).
    """
    path = config_path()
    data = {
        "workspace_id": config.workspace_id,
        "session_cookie": config.session_cookie,
        "refresh_interval": config.refresh_interval,
        "limits": config.limits,
    }
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
