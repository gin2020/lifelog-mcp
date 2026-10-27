"""Secret redaction for application logs and diagnostics."""

from __future__ import annotations

import logging
import re
from threading import RLock
from urllib.parse import quote


REDACTED = "[REDACTED]"

_SECRET_VALUES: set[str] = set()
_SECRET_LOCK = RLock()
_FACTORY_INSTALLED = False

# Telegram Bot API puts the bot token in the URL path.  The token may have an
# URL-encoded colon, so matching the whole path segment is more reliable than
# matching only the conventional ``digits:letters`` token shape.
_TELEGRAM_BOT_URL = re.compile(
    r"(?P<prefix>(?:(?:https?://)?api\.telegram\.org)/bot)"
    r"(?P<token>[^\s/?#\"'<>]+)",
    re.IGNORECASE,
)
_AUTH_HEADER = re.compile(
    r"(?P<prefix>\b(?:authorization|proxy-authorization|x-telegram-bot-api-secret-token)\b\s*[:=]\s*)"
    r"(?P<value>[^\s,;]+)",
    re.IGNORECASE,
)
_CONNECTION_PASSWORD = re.compile(
    r"(?P<prefix>\b[a-z][a-z0-9+.-]*://[^\s/@:]+:)(?P<password>[^\s/@]+)(?P<suffix>@)",
    re.IGNORECASE,
)


def register_secret(value: str | None) -> None:
    """Register one runtime secret for redaction without exposing its value."""
    if value is None or not value.strip():
        return
    with _SECRET_LOCK:
        _SECRET_VALUES.add(value)


def redact_secrets(value: object) -> str:
    """Return a safe representation of a value for logs and diagnostics."""
    text = str(value)
    text = _TELEGRAM_BOT_URL.sub(rf"\g<prefix>{REDACTED}", text)
    text = _AUTH_HEADER.sub(rf"\g<prefix>{REDACTED}", text)
    text = _CONNECTION_PASSWORD.sub(rf"\g<prefix>{REDACTED}\g<suffix>", text)

    with _SECRET_LOCK:
        secrets = sorted(_SECRET_VALUES, key=len, reverse=True)
    for secret in secrets:
        text = text.replace(secret, REDACTED)
        encoded = quote(secret, safe="")
        if encoded != secret:
            text = re.sub(re.escape(encoded), REDACTED, text, flags=re.IGNORECASE)
    return text


def _redact_record(record: logging.LogRecord) -> logging.LogRecord:
    """Sanitize a record before any handler can format it."""
    try:
        message = record.getMessage()
    except Exception:
        message = repr(record.msg)
    record.msg = redact_secrets(message)
    record.args = ()

    if record.exc_info:
        exception_text = logging.Formatter().formatException(record.exc_info)
        record.exc_text = redact_secrets(exception_text)
        # Keep the already-rendered, sanitized traceback and prevent another
        # handler from formatting the original exception object.
        record.exc_info = None
    elif record.exc_text:
        record.exc_text = redact_secrets(record.exc_text)
    if record.stack_info:
        record.stack_info = redact_secrets(record.stack_info)
    return record


class SecretRedactionFilter(logging.Filter):
    """Sanitize records for handlers created after application startup."""

    def filter(self, record: logging.LogRecord) -> bool:
        _redact_record(record)
        return True


def install_log_redaction() -> None:
    """Install process-wide record and handler redaction exactly once."""
    global _FACTORY_INSTALLED
    root_logger = logging.getLogger()
    redaction_filter = SecretRedactionFilter()
    for handler in root_logger.handlers:
        if not any(isinstance(item, SecretRedactionFilter) for item in handler.filters):
            handler.addFilter(redaction_filter)

    if _FACTORY_INSTALLED:
        return

    original_factory = logging.getLogRecordFactory()

    def factory(*args, **kwargs):
        return _redact_record(original_factory(*args, **kwargs))

    logging.setLogRecordFactory(factory)
    _FACTORY_INSTALLED = True
