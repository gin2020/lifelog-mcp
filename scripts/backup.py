"""Run the standalone daily PostgreSQL backup process."""

import argparse
import asyncio
import logging
import signal

from app.config.logging import configure_logging
from app.services.backup_crypto import decrypt_file
from app.services.backup_service import BackupError, BackupScheduler, BackupService
from app.config.settings import get_settings


async def run_daemon() -> None:
    """Run the timezone-aware scheduler until PM2 sends a termination signal."""
    configure_logging()
    logger = logging.getLogger(__name__)
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def request_stop() -> None:
        logger.info("Backup scheduler graceful shutdown requested")
        stop_event.set()

    for signal_name in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signal_name, request_stop)
    logger.info("Backup scheduler started")
    await BackupScheduler().run_forever(stop_event)
    logger.info("Backup scheduler stopped")


def main() -> int:
    parser = argparse.ArgumentParser(description="LifeLog PostgreSQL backup job")
    parser.add_argument("--run-once", action="store_true", help="create and send one backup now")
    parser.add_argument("--test", action="store_true", help="create, decrypt and validate one backup")
    parser.add_argument("--decrypt", metavar="ENCRYPTED_FILE", help="decrypt a downloaded backup")
    parser.add_argument("--output", metavar="DUMP_FILE", help="output path for --decrypt")
    args = parser.parse_args()
    configure_logging()
    try:
        settings = get_settings()
        if args.decrypt:
            if not args.output:
                parser.error("--decrypt requires --output")
            decrypt_file(args.decrypt, args.output, settings.backup_encryption_key)
            logging.getLogger(__name__).info("Backup decrypted to %s", args.output)
        elif args.test:
            BackupService(settings).verify_restore()
        elif args.run_once:
            asyncio.run(BackupService(settings).run_backup())
        else:
            asyncio.run(run_daemon())
    except BackupError as error:
        logging.getLogger(__name__).error("Backup command failed: %s", error)
        return 1
    except Exception as error:
        logging.getLogger(__name__).error(
            "Backup command failed: %s: %s", type(error).__name__, error
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
