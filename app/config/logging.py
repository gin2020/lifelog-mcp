"""Конфигурация логирования приложения."""

import logging

from app.utils.redaction import install_log_redaction


def configure_logging() -> None:
    """Configure application logs when no root handler exists yet."""
    root_logger = logging.getLogger()
    if not root_logger.handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )

    logging.getLogger("app").setLevel(logging.INFO)
    install_log_redaction()
