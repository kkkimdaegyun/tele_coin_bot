import logging
import os
import unittest
from unittest.mock import patch

from secure_logging import SecretRedactingFilter


class SecretLoggingTests(unittest.TestCase):
    def test_redacts_message_and_url_argument(self):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "unit-test-secret"}):
            filter_ = SecretRedactingFilter()
            record = logging.LogRecord(
                "test",
                logging.INFO,
                __file__,
                1,
                "request %s",
                ("https://example.test/botunit-test-secret/send",),
                None,
            )
            filter_.filter(record)
            rendered = record.getMessage()
            self.assertNotIn("unit-test-secret", rendered)
            self.assertIn("<redacted>", rendered)


if __name__ == "__main__":
    unittest.main()
