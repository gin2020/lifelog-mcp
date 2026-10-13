"""Run the allowlisted MAX User API polling worker."""

import asyncio
import logging
import signal

from app.config.logging import configure_logging
from app.config.settings import get_settings
from app.services.max_user_worker import MaxUserWorker


async def run() -> None:
    configure_logging()
    settings = get_settings()
    if not settings.max_user_worker_enabled:
        raise RuntimeError("MAX_USER_WORKER_ENABLED must be true")
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signal_name, stop_event.set)
    worker = MaxUserWorker(settings)
    logging.getLogger(__name__).info("MAX User API worker started")
    await worker.run_forever(stop_event, settings.max_user_worker_poll_interval_seconds)
    logging.getLogger(__name__).info("MAX User API worker stopped")


if __name__ == "__main__":
    asyncio.run(run())
