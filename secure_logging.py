from __future__ import annotations

import logging
import os


class SecretRedactingFilter(logging.Filter):
    def __init__(self):
        super().__init__()
        self.secrets = tuple(
            value
            for name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "WEBHOOK_SECRET")
            if (value := os.getenv(name, "").strip())
        )

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._redact(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(self._redact(value) for value in record.args)
        elif isinstance(record.args, dict):
            record.args = {key: self._redact(value) for key, value in record.args.items()}
        return True

    def _redact(self, value):
        text = str(value)
        if not any(secret in text for secret in self.secrets):
            return value
        for secret in self.secrets:
            text = text.replace(secret, "<redacted>")
        return text
