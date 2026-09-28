"""Standalone PostgreSQL backup, encryption and Telegram delivery service."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
import fcntl
import gzip
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any
from urllib.parse import quote, unquote, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import httpx

from app.config.settings import Settings, get_settings
from app.services.backup_crypto import decrypt_file, encrypt_file


logger = logging.getLogger(__name__)


class BackupError(RuntimeError):
    """Raised when a backup cannot be created or delivered."""


class BackupAlreadyRunning(BackupError):
    """Raised internally when another backup process owns the lock."""


class BackupTelegramError(BackupError):
    """Raised when Telegram rejects or cannot receive the encrypted file."""


class BackupTelegramUploader:
    """Direct Telegram Bot API client used only by the backup job."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    async def upload(self, encrypted_path: Path, backup_date: date) -> None:
        """Upload one encrypted document through sendDocument."""
        token = self._settings.telegram_bot_token
        if token is None:
            raise BackupTelegramError("TELEGRAM_BOT_TOKEN is not configured")
        size = encrypted_path.stat().st_size
        if size > self._settings.backup_max_file_size_bytes:
            raise BackupTelegramError(
                "Encrypted backup exceeds the Telegram document size limit"
            )
        caption = (
            "LifeLog PostgreSQL backup\n"
            f"Date: {backup_date.isoformat()}\n"
            f"Size: {size} bytes\n"
            "Status: created successfully"
        )
        url = f"https://api.telegram.org/bot{token.get_secret_value()}/sendDocument"
        try:
            with encrypted_path.open("rb") as document:
                timeout = httpx.Timeout(self._settings.backup_http_timeout_seconds)
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.post(
                        url,
                        data={
                            "chat_id": str(self._settings.default_user_telegram_id),
                            "caption": caption,
                        },
                        files={
                            "document": (
                                encrypted_path.name,
                                document,
                                "application/octet-stream",
                            )
                        },
                    )
        except (OSError, httpx.HTTPError) as error:
            raise BackupTelegramError("Telegram upload request failed") from error

        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.is_success and isinstance(body, dict) and body.get("ok") is True:
            return
        description = body.get("description") if isinstance(body, dict) else None
        raise BackupTelegramError(
            f"Telegram rejected backup upload (HTTP {response.status_code}): "
            f"{description or 'unknown API error'}"
        )


class _ProcessLock:
    """Non-blocking Unix lock preventing concurrent backup attempts."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle = None

    def __enter__(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("a+")
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._handle.close()
            self._handle = None
            return False
        return True

    def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
        if self._handle is not None:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            self._handle.close()


class BackupService:
    """Create, encrypt, verify and deliver a daily PostgreSQL backup."""

    def __init__(
        self,
        settings: Settings | None = None,
        uploader: BackupTelegramUploader | Any | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.uploader = uploader or BackupTelegramUploader(self.settings)

    async def run_backup(self, now: datetime | None = None) -> bool:
        """Run one daily backup; return false when idempotency or lock skips it."""
        self._ensure_enabled()
        timezone = ZoneInfo(self.settings.backup_timezone)
        backup_date = (now or datetime.now(timezone)).astimezone(timezone).date()
        lock_path = Path(self.settings.backup_lock_file)
        with _ProcessLock(lock_path) as acquired:
            if not acquired:
                logger.info("Backup skipped: another backup process is running")
                return False
            state = self._read_state()
            if state.get("date") == backup_date.isoformat() and state.get("status") in {
                "sent",
                "uploading",
            }:
                logger.info("Backup skipped: date=%s already attempted", backup_date)
                return False
            return await self._run_locked(backup_date)

    async def _run_locked(self, backup_date: date) -> bool:
        state_marked = False
        upload_completed = False
        try:
            logger.info("Backup started: date=%s", backup_date)
            with tempfile.TemporaryDirectory(prefix="lifelog-backup-") as directory:
                temporary = Path(directory)
                dump_path = temporary / f"lifelog_{backup_date.isoformat()}.backup"
                self._run_pg_dump(dump_path)
                logger.info("pg_dump completed: size=%s bytes", dump_path.stat().st_size)

                source_path, compressed = self._compress_if_smaller(dump_path, temporary)
                encrypted_path = temporary / f"{dump_path.name}.enc"
                encrypt_file(
                    source_path,
                    encrypted_path,
                    self.settings.backup_encryption_key,
                    compressed=compressed,
                )
                if not encrypted_path.exists() or encrypted_path.stat().st_size == 0:
                    raise BackupError("Encrypted backup was not created")
                logger.info("Backup encrypted: size=%s bytes", encrypted_path.stat().st_size)

                self._write_state(
                    {
                        "date": backup_date.isoformat(),
                        "status": "uploading",
                        "sha256": hashlib.sha256(encrypted_path.read_bytes()).hexdigest(),
                        "started_at": datetime.now().astimezone().isoformat(),
                    }
                )
                state_marked = True
                logger.info("Telegram upload started: date=%s", backup_date)
                await self.uploader.upload(encrypted_path, backup_date)
                upload_completed = True
                logger.info("Telegram upload completed: date=%s", backup_date)
                self._write_state(
                    {
                        "date": backup_date.isoformat(),
                        "status": "sent",
                        "sent_at": datetime.now().astimezone().isoformat(),
                    }
                )
            logger.info("Backup finished: date=%s", backup_date)
            return True
        except Exception as error:
            safe_message = self._safe_error(error)
            if state_marked:
                self._write_state(
                    {
                        "date": backup_date.isoformat(),
                        # Keep an ambiguous post-upload attempt non-retryable. If
                        # the process died after Telegram accepted the request,
                        # the preceding "uploading" marker also causes a skip.
                        "status": "sent" if upload_completed else "failed",
                        "error": safe_message,
                        ("sent_at" if upload_completed else "failed_at"):
                            datetime.now().astimezone().isoformat(),
                    }
                )
            logger.error("Backup failed: %s", safe_message)
            if isinstance(error, BackupError):
                raise
            raise BackupError(safe_message) from error

    def verify_restore(self, now: datetime | None = None) -> None:
        """Create and decrypt a backup, then validate it with pg_restore --list."""
        self._ensure_enabled()
        timezone = ZoneInfo(self.settings.backup_timezone)
        backup_date = (now or datetime.now(timezone)).astimezone(timezone).date()
        try:
            with tempfile.TemporaryDirectory(prefix="lifelog-backup-test-") as directory:
                temporary = Path(directory)
                dump_path = temporary / f"lifelog_{backup_date.isoformat()}.backup"
                self._run_pg_dump(dump_path)
                source_path, compressed = self._compress_if_smaller(dump_path, temporary)
                encrypted_path = temporary / f"{dump_path.name}.enc"
                encrypt_file(
                    source_path,
                    encrypted_path,
                    self.settings.backup_encryption_key,
                    compressed=compressed,
                )
                restored_path = temporary / "restored.backup"
                decrypt_file(encrypted_path, restored_path, self.settings.backup_encryption_key)
                self._run_pg_restore_list(restored_path)
        except Exception as error:
            safe_message = self._safe_error(error)
            logger.error("Backup restore test failed: %s", safe_message)
            if isinstance(error, BackupError):
                raise
            raise BackupError(safe_message) from error
        logger.info("Backup restore test completed: date=%s", backup_date)

    def _ensure_enabled(self) -> None:
        if not self.settings.backup_enabled:
            raise BackupError("BACKUP_ENABLED is false")
        if self.settings.backup_encryption_key is None:
            raise BackupError("BACKUP_ENCRYPTION_KEY is not configured")

    def _run_pg_dump(self, output_path: Path) -> None:
        executable = shutil.which("pg_dump")
        if executable is None:
            raise BackupError("pg_dump executable was not found")
        connection_uri, password = self._database_connection()
        environment = os.environ.copy()
        if password is not None:
            environment["PGPASSWORD"] = password
        try:
            result = subprocess.run(
                [
                    executable,
                    "-Fc",
                    "--file",
                    str(output_path),
                    "--no-password",
                    "--dbname",
                    connection_uri,
                ],
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as error:
            raise BackupError("Could not start pg_dump") from error
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "unknown pg_dump error").strip()
            raise BackupError(f"pg_dump failed: {self._safe_error_text(detail)}")
        if not output_path.exists() or output_path.stat().st_size == 0:
            raise BackupError("pg_dump completed without producing a non-empty file")

    def _run_pg_restore_list(self, dump_path: Path) -> None:
        executable = shutil.which("pg_restore")
        if executable is None:
            raise BackupError("pg_restore executable was not found")
        try:
            result = subprocess.run(
                [executable, "--list", str(dump_path)],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as error:
            raise BackupError("Could not start pg_restore") from error
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "unknown pg_restore error").strip()
            raise BackupError(f"pg_restore validation failed: {self._safe_error_text(detail)}")

    def _database_connection(self) -> tuple[str, str | None]:
        raw_url = str(self.settings.database_url)
        try:
            parsed = urlsplit(raw_url)
            if parsed.scheme not in {"postgresql", "postgresql+psycopg"}:
                raise ValueError("unsupported scheme")
            username = unquote(parsed.username or "")
            password = unquote(parsed.password) if parsed.password is not None else None
            host = parsed.hostname
            port = parsed.port
            database = unquote(parsed.path.lstrip("/"))
            if not database:
                raise ValueError("database name is missing")
            if not host:
                netloc = quote(username, safe="") + "@" if username else ""
            else:
                rendered_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
                netloc = quote(username, safe="") + "@" if username else ""
                netloc += rendered_host
                if port is not None:
                    netloc += f":{port}"
            connection_uri = urlunsplit(
                (
                    "postgresql",
                    netloc,
                    "/" + quote(database, safe=""),
                    parsed.query,
                    "",
                )
            )
        except (TypeError, ValueError) as error:
            raise BackupError("DATABASE_URL is invalid for pg_dump") from error
        return connection_uri, password

    @staticmethod
    def _compress_if_smaller(dump_path: Path, directory: Path) -> tuple[Path, bool]:
        compressed_path = directory / f"{dump_path.name}.gz"
        with compressed_path.open("wb") as compressed_stream:
            with gzip.GzipFile(
                fileobj=compressed_stream,
                mode="wb",
                compresslevel=6,
                mtime=0,
            ) as compressed_file:
                compressed_file.write(dump_path.read_bytes())
        if compressed_path.stat().st_size < dump_path.stat().st_size:
            logger.info("Backup compression selected: size=%s bytes", compressed_path.stat().st_size)
            return compressed_path, True
        logger.info("Backup compression skipped: compressed file is not smaller")
        return dump_path, False

    def _read_state(self) -> dict[str, Any]:
        path = Path(self.settings.backup_state_file)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError) as error:
            logger.warning("Backup state could not be read: %s", self._safe_error(error))
            return {}
        return value if isinstance(value, dict) else {}

    def _write_state(self, value: dict[str, Any]) -> None:
        path = Path(self.settings.backup_state_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.name}.",
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                json.dump(value, temporary_file, ensure_ascii=False)
                temporary_file.flush()
                os.fchmod(temporary_file.fileno(), 0o600)
            os.replace(temporary_path, path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()

    def _safe_error(self, error: BaseException) -> str:
        return self._safe_error_text(f"{type(error).__name__}: {error}")

    def _safe_error_text(self, message: str) -> str:
        secrets = [
            self.settings.telegram_bot_token.get_secret_value()
            if self.settings.telegram_bot_token is not None
            else None,
            self.settings.backup_encryption_key.get_secret_value()
            if self.settings.backup_encryption_key is not None
            else None,
        ]
        try:
            parsed = urlsplit(str(self.settings.database_url))
            if parsed.password is not None:
                secrets.extend([parsed.password, unquote(parsed.password)])
        except ValueError:
            pass
        safe = message
        for secret in secrets:
            if secret:
                safe = safe.replace(secret, "[REDACTED]")
        return safe[:2000]


class BackupScheduler:
    """Timezone-aware daily scheduler for the standalone process."""

    def __init__(self, settings: Settings | None = None, service: BackupService | None = None) -> None:
        self.settings = settings or get_settings()
        self.service = service or BackupService(self.settings)

    def next_run_at(self, now: datetime | None = None) -> datetime:
        timezone = ZoneInfo(self.settings.backup_timezone)
        current = (now or datetime.now(timezone)).astimezone(timezone)
        hour, minute = (int(value) for value in self.settings.backup_time.split(":"))
        candidate = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= current:
            candidate += timedelta(days=1)
        return candidate

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        """Wait for each configured local time and keep the process alive."""
        while not stop_event.is_set():
            run_at = self.next_run_at()
            delay = max(0.0, (run_at - datetime.now(run_at.tzinfo)).total_seconds())
            logger.info("Next backup scheduled at %s", run_at.isoformat())
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=delay)
            except asyncio.TimeoutError:
                try:
                    await self.service.run_backup(now=run_at)
                except BackupError:
                    pass
