from __future__ import annotations

import ctypes
import hashlib
import json
import logging
import logging.config
import os
import secrets
import sys
from datetime import UTC, datetime
from pathlib import Path

import uvicorn
from dotenv import load_dotenv


PROJECT_DIR = Path(__file__).resolve().parent
LOG_DIR = PROJECT_DIR / "logs"
RUNTIME_DIR = PROJECT_DIR / "runtime"
CONTROL_FILE = RUNTIME_DIR / "control.json"
SERVICE_NAME = "chart-teacher-bot"


def _logging_config() -> dict:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    common = {
        "class": "logging.handlers.RotatingFileHandler",
        "maxBytes": 5 * 1024 * 1024,
        "backupCount": 5,
        "encoding": "utf-8",
        "delay": False,
        "formatter": "standard",
    }
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "standard": {
                "format": "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            },
        },
        "filters": {
            "redact_secrets": {
                "()": "secure_logging.SecretRedactingFilter",
            },
        },
        "handlers": {
            "main_file": {
                **common,
                "filename": str(LOG_DIR / "chart_teacher.log"),
                "level": "INFO",
                "filters": ["redact_secrets"],
            },
            "error_file": {
                **common,
                "filename": str(LOG_DIR / "error.log"),
                "level": "ERROR",
                "filters": ["redact_secrets"],
            },
        },
        "root": {
            "handlers": ["main_file", "error_file"],
            "level": "INFO",
        },
        "loggers": {
            "uvicorn": {
                "handlers": ["main_file", "error_file"],
                "level": "INFO",
                "propagate": False,
            },
            "uvicorn.access": {
                "handlers": ["main_file", "error_file"],
                "level": "INFO",
                "propagate": False,
            },
            "httpx": {
                "handlers": ["main_file", "error_file"],
                "level": "WARNING",
                "propagate": False,
            },
            "httpcore": {
                "handlers": ["main_file", "error_file"],
                "level": "WARNING",
                "propagate": False,
            },
        },
    }


def _acquire_single_instance_mutex():
    if os.name != "nt":
        return None

    digest = hashlib.sha256(str(PROJECT_DIR).lower().encode("utf-8")).hexdigest()[:20]
    name = f"Local\\ChartTeacherBot-{digest}"
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.CreateMutexW(None, False, name)
    if not handle:
        raise OSError(ctypes.get_last_error(), "Could not create instance mutex")
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(handle)
        return False
    return handle


def _write_control_file(token: str) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "service": SERVICE_NAME,
        "pid": os.getpid(),
        "project": str(PROJECT_DIR),
        "server": str(Path(__file__).resolve()),
        "executable": sys.executable,
        "started_at": datetime.now(UTC).isoformat(),
        "control_token": token,
    }
    temporary = CONTROL_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=True), encoding="utf-8")
    temporary.replace(CONTROL_FILE)


def _remove_own_control_file() -> None:
    try:
        data = json.loads(CONTROL_FILE.read_text(encoding="utf-8"))
        if data.get("pid") == os.getpid():
            CONTROL_FILE.unlink(missing_ok=True)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass


def main() -> int:
    os.chdir(PROJECT_DIR)
    load_dotenv(PROJECT_DIR / ".env")
    logging.config.dictConfig(_logging_config())
    log = logging.getLogger("chart-teacher-server")

    mutex = _acquire_single_instance_mutex()
    if mutex is False:
        log.info("Another Chart Teacher server instance is already running")
        return 0

    token = secrets.token_urlsafe(32)
    exit_code = 1

    try:
        from app import app

        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=8787,
            log_config=None,
            access_log=True,
            timeout_graceful_shutdown=10,
        )
        server = uvicorn.Server(config)
        app.state.control_token = token
        app.state.uvicorn_server = server
        app.state.controlled_shutdown = False
        _write_control_file(token)

        log.info("Chart Teacher starting on http://127.0.0.1:8787")
        server.run()

        if app.state.controlled_shutdown:
            log.info("Chart Teacher stopped by a control request")
            exit_code = 0
        else:
            log.error("Chart Teacher stopped unexpectedly")
            exit_code = 1
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        log.error("Chart Teacher startup failed with exit code %s", code)
        exit_code = code or 1
    except Exception:
        log.exception("Chart Teacher crashed")
        exit_code = 1
    finally:
        _remove_own_control_file()
        if os.name == "nt" and mutex not in (None, False):
            ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(mutex)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
