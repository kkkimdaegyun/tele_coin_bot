from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent
SERVER_PATH = PROJECT_DIR / "chart_teacher_server.py"
PYTHONW_PATH = PROJECT_DIR / ".venv" / "Scripts" / "pythonw.exe"
LOG_DIR = PROJECT_DIR / "logs"


def _build_logger() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        LOG_DIR / "supervisor.log",
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    )
    logger = logging.getLogger("chart-teacher-supervisor")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def main() -> int:
    os.chdir(PROJECT_DIR)
    log = _build_logger()

    if not PYTHONW_PATH.exists():
        log.error("Python executable not found: %s", PYTHONW_PATH)
        return 1
    if not SERVER_PATH.exists():
        log.error("Server program not found: %s", SERVER_PATH)
        return 1

    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    restart_delay_seconds = 2

    while True:
        log.info("Starting Chart Teacher server worker")
        try:
            child = subprocess.Popen(
                [str(PYTHONW_PATH), str(SERVER_PATH)],
                cwd=PROJECT_DIR,
                creationflags=creation_flags,
                close_fds=True,
            )
            exit_code = child.wait()
        except Exception:
            log.exception("Could not run Chart Teacher server worker")
            return 1

        if exit_code == 0:
            log.info("Server worker stopped normally; supervisor is exiting")
            return 0

        log.error(
            "Server worker exited abnormally with code %s; restarting in %s seconds",
            exit_code,
            restart_delay_seconds,
        )
        time.sleep(restart_delay_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
