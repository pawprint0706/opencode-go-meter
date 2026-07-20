"""Entry point for OpenCode Go Meter."""

import argparse
import logging
import os
import sys

from . import config, instance
from .tray import GoMeterApp


def _build_handlers():
    handlers = []
    # Under pythonw.exe there is no console and sys.stderr is None.
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler(sys.stderr))
    try:
        handlers.append(
            logging.FileHandler(
                os.path.join(config.config_dir(), "app.log"), encoding="utf-8"
            )
        )
    except OSError:
        pass
    return handlers


def main():
    parser = argparse.ArgumentParser(
        prog="opencode-go-meter",
        description="System tray monitor for OpenCode Go plan usage.",
    )
    parser.add_argument(
        "--stop", action="store_true",
        help="stop the running instance and exit",
    )
    parser.add_argument(
        "--replace", action="store_true",
        help="stop the running instance (if any) before starting",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=_build_handlers(),
    )
    log = logging.getLogger(__name__)

    if args.stop:
        stopped = instance.stop_running()
        log.info("Stop requested: %s", "done" if stopped else "FAILED")
        sys.exit(0 if stopped else 1)

    if args.replace:
        if not instance.stop_running():
            log.error("Could not stop the running instance; aborting")
            sys.exit(1)

    lock = instance.acquire_lock()
    if lock is None:
        log.warning("OpenCode Go Meter is already running; exiting.")
        return

    app = GoMeterApp()
    try:
        app.run()
    except KeyboardInterrupt:
        app.stop()
    except Exception:
        log.exception("Fatal error")
        sys.exit(1)


if __name__ == "__main__":
    main()
