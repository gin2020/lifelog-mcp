"""Run the Lifelog notification dispatcher as a standalone PM2-managed process."""

import asyncio
import logging
import signal

from app.config.logging import configure_logging
from app.services.notification_dispatcher import NotificationDispatcher


POLL_INTERVAL_SECONDS = 1.0


async def run() -> None:
    """Run the dispatcher until SIGINT or SIGTERM is received."""
    configure_logging()
    logger = logging.getLogger(__name__)
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def request_stop() -> None:
        """Signal the polling loop to finish after its current operation."""
        logger.info("Notification dispatcher graceful shutdown requested")
        stop_event.set()

    for signal_name in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signal_name, request_stop)

    dispatcher = NotificationDispatcher()
    logger.info("Notification dispatcher started")
    while not stop_event.is_set():
        processed = await dispatcher.run_once()
        if not processed:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=POLL_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                pass
    logger.info("Notification dispatcher stopped")


if __name__ == "__main__":
    asyncio.run(run())
