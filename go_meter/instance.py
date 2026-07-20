"""Single-instance lock and cross-process stop/replace support.

Two files under the config dir:
  - ``app.lock``: held (OS file lock) by the running instance for its
    lifetime; released automatically by the OS when the process exits.
  - ``app.pid``: the lock holder's PID. Only trusted while the lock is
    actually held, so stale PIDs after a crash are never killed.

Killing by locked PID (instead of pkill/taskkill by name) guarantees we
never terminate an unrelated Python process.
"""

import logging
import os
import signal
import subprocess
import sys
import time

logger = logging.getLogger(__name__)


def _lock_path() -> str:
    from . import config

    return os.path.join(config.config_dir(), "app.lock")


def _pid_path() -> str:
    from . import config

    return os.path.join(config.config_dir(), "app.pid")


def _try_lock():
    """Attempt the OS lock; returns the open file or None. No side effects."""
    f = open(_lock_path(), "a+")
    try:
        if sys.platform == "win32":
            import msvcrt

            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f


def _release(f) -> None:
    try:
        if sys.platform == "win32":
            import msvcrt

            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
    except OSError:
        pass
    f.close()


def acquire_lock():
    """Become the single running instance.

    Returns the held lock file (keep it referenced for the process
    lifetime) or None if another instance is running. Records our PID so
    a later ``--stop``/``--replace`` can find us.
    """
    f = _try_lock()
    if f is None:
        return None
    try:
        with open(_pid_path(), "w", encoding="ascii") as pf:
            pf.write(str(os.getpid()))
    except OSError:
        logger.warning("Could not write PID file", exc_info=True)
    return f


def stop_running(timeout: float = 10.0) -> bool:
    """Terminate the running instance, if any. True when none remains."""
    probe = _try_lock()
    if probe is not None:
        _release(probe)
        return True  # nothing is running

    try:
        with open(_pid_path(), "r", encoding="ascii") as pf:
            pid = int(pf.read().strip())
    except (OSError, ValueError):
        logger.warning("An instance holds the lock but its PID is unknown")
        return False

    logger.info(f"Stopping running instance (pid {pid})")
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except PermissionError:
            logger.warning(f"No permission to stop pid {pid}")
            return False

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        probe = _try_lock()
        if probe is not None:
            _release(probe)
            return True
        time.sleep(0.2)

    if sys.platform != "win32":
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        time.sleep(0.5)
        probe = _try_lock()
        if probe is not None:
            _release(probe)
            return True

    logger.warning("Running instance did not exit in time")
    return False
