# ============================================================
#  tradebot / utils / logger.py
#  Centralised logging setup for the entire system.
#  Call setup_logging() once at startup.
# ============================================================

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from config.settings import LOG_DIR, LOGGING


def setup_logging(name: str = "tradebot") -> logging.Logger:
    """
    Configure root logger with rotating file + console output.
    Call once at the start of main.py / any entry point.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    level = getattr(logging, LOGGING["level"], logging.INFO)
    fmt   = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)-25s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    if LOGGING["to_file"]:
        fh = RotatingFileHandler(
            LOG_DIR / f"{name}.log",
            maxBytes=LOGGING["max_mb"] * 1024 * 1024,
            backupCount=LOGGING["backup_count"],
        )
        fh.setFormatter(fmt)
        root.addHandler(fh)

    if LOGGING["to_console"]:
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(fmt)
        root.addHandler(ch)

    return logging.getLogger(name)
