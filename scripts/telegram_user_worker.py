"""Run the allowlisted Telegram User API polling worker."""

import asyncio
import logging
import signal

from app.config.logging import configure_logging
from app.config.settings import get_settings
from app.services.telegram_user_worker import TelegramUserWorker


async def run() -> None:
    configure_logging()
    settings = get_settings()
    if not settings.telegram_user_worker_enabled:
        raise RuntimeError("TELEGRAM_USER_WORKER_ENABLED must be true")
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signal_name, stop_event.set)
    worker = TelegramUserWorker(settings)
    logging.getLogger(__name__).info("Telegram User API worker started")
    while not stop_event.is_set():
        processed = await worker.run_once()
        if not processed:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=settings.telegram_user_poll_interval_seconds)
            except asyncio.TimeoutError:
                pass
    logging.getLogger(__name__).info("Telegram User API worker stopped")


if __name__ == "__main__":
    asyncio.run(run())
