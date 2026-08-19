"""Logging configuration: one call at the entry point, loggers everywhere else.

Two rules this file exists to enforce.

FILES ROTATE. A scan writes a line per stage and a warning per unreachable
symbol; over a few hundred runs an unrotated log becomes the largest file in
the project and the one nobody can open. RotatingFileHandler caps it.

LIBRARY MODULES NEVER CONFIGURE. Only an entry point (advisor.py, app.py,
main.py) calls setup_logging(); every other module calls get_logger(__name__)
and inherits whatever the entry point chose. A module that calls
basicConfig() on import steals that decision from whoever imported it -- and
in a Flask process, silently reroutes Werkzeug's logs too.

Console and file deliberately differ. The console shows INFO and above,
without timestamps, because a human is watching a run in progress. The file
records DEBUG with timestamps and module names, because the question it
answers is asked days later.
"""

import logging
import logging.handlers
import os
import sys

import config


LOG_DIR = os.path.join(config.BASE_DIR, "logs")

LOG_FILE = os.path.join(LOG_DIR, "sirat.log")

MAX_BYTES = 5 * 1024 * 1024

BACKUP_COUNT = 5

FILE_FORMAT = "%(asctime)s  %(levelname)-8s  %(name)-18s  %(message)s"

CONSOLE_FORMAT = "%(levelname)-8s %(message)s"

_configured = False


def setup_logging(level=logging.INFO, file_level=logging.DEBUG, quiet=False):
    """Configure the root logger. Safe to call more than once.

    A second call is a no-op rather than a second set of handlers: the usual
    way a log ends up printing every line three times is an entry point that
    imports another entry point.
    """

    global _configured

    if _configured:
        return logging.getLogger()

    root = logging.getLogger()
    root.setLevel(min(level, file_level))

    for handler in list(root.handlers):
        root.removeHandler(handler)

    # ---- file ------------------------------------------------------------

    try:
        os.makedirs(LOG_DIR, exist_ok=True)

        file_handler = logging.handlers.RotatingFileHandler(
            LOG_FILE,
            maxBytes=MAX_BYTES,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setLevel(file_level)
        file_handler.setFormatter(logging.Formatter(FILE_FORMAT))

        root.addHandler(file_handler)

    except OSError as exc:
        # A read-only directory is not a reason to abort a market scan. Say so
        # on the console once and carry on without a file log.
        print(f"WARNING: file logging disabled ({exc})", file=sys.stderr)

    # ---- console ---------------------------------------------------------

    if not quiet:

        console = logging.StreamHandler(sys.stderr)
        console.setLevel(level)
        console.setFormatter(logging.Formatter(CONSOLE_FORMAT))

        root.addHandler(console)

    # urllib3 logs a line per connection; at DEBUG a whole-market fetch buries
    # everything else under retry chatter.
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    _configured = True

    root.debug("logging configured -> %s", LOG_FILE)

    return root


def get_logger(name):
    """Module-level logger. Never configures anything."""

    return logging.getLogger(name)


def log_path():
    return LOG_FILE
