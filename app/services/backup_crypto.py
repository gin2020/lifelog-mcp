"""Authenticated encryption format used by PostgreSQL backups."""

import base64
import binascii
import gzip
import secrets
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import SecretStr


MAGIC = b"LIFELOG-BACKUP"
VERSION = 1
FLAG_GZIP = 1
NONCE_SIZE = 12
TAG_SIZE = 16
HEADER_SIZE = len(MAGIC) + 2 + NONCE_SIZE


class BackupEncryptionError(ValueError):
    """Raised when a backup key or encrypted payload is invalid."""


def decode_encryption_key(key: SecretStr | str | None) -> bytes:
    """Decode the documented URL-safe base64 AES-256 key without logging it."""
    if key is None:
        raise BackupEncryptionError("BACKUP_ENCRYPTION_KEY is not configured")
    value = key.get_secret_value() if isinstance(key, SecretStr) else key
    try:
        decoded = base64.urlsafe_b64decode(value.strip() + "=" * (-len(value.strip()) % 4))
    except (ValueError, binascii.Error) as error:
        raise BackupEncryptionError("BACKUP_ENCRYPTION_KEY is not valid URL-safe base64") from error
    if len(decoded) != 32:
        raise BackupEncryptionError("BACKUP_ENCRYPTION_KEY must decode to exactly 32 bytes")
    return decoded


def encrypt_bytes(plaintext: bytes, key: SecretStr | str, *, compressed: bool = False) -> bytes:
    """Encrypt bytes using AES-256-GCM and a versioned, authenticated header."""
    raw_key = decode_encryption_key(key)
    nonce = secrets.token_bytes(NONCE_SIZE)
    flags = FLAG_GZIP if compressed else 0
    header = MAGIC + bytes((VERSION, flags)) + nonce
    ciphertext = AESGCM(raw_key).encrypt(nonce, plaintext, header)
    return header + ciphertext


def decrypt_bytes(encrypted: bytes, key: SecretStr | str) -> bytes:
    """Validate and decrypt a backup, transparently reversing optional gzip."""
    if len(encrypted) < HEADER_SIZE + TAG_SIZE:
        raise BackupEncryptionError("Encrypted backup is truncated")
    if encrypted[: len(MAGIC)] != MAGIC:
        raise BackupEncryptionError("Encrypted backup has an unsupported format")
    version = encrypted[len(MAGIC)]
    flags = encrypted[len(MAGIC) + 1]
    if version != VERSION or flags & ~FLAG_GZIP:
        raise BackupEncryptionError("Encrypted backup has an unsupported version")
    nonce_start = len(MAGIC) + 2
    nonce = encrypted[nonce_start : nonce_start + NONCE_SIZE]
    header = encrypted[:HEADER_SIZE]
    try:
        plaintext = AESGCM(decode_encryption_key(key)).decrypt(
            nonce,
            encrypted[HEADER_SIZE:],
            header,
        )
    except Exception as error:
        raise BackupEncryptionError("Encrypted backup authentication failed") from error
    if flags & FLAG_GZIP:
        try:
            plaintext = gzip.decompress(plaintext)
        except (OSError, EOFError) as error:
            raise BackupEncryptionError("Encrypted backup compression is invalid") from error
    return plaintext


def encrypt_file(
    input_path: Path,
    output_path: Path,
    key: SecretStr | str,
    *,
    compressed: bool = False,
) -> None:
    """Encrypt a temporary backup file into the final temporary artifact."""
    output_path.write_bytes(encrypt_bytes(input_path.read_bytes(), key, compressed=compressed))


def decrypt_file(input_path: Path, output_path: Path, key: SecretStr | str) -> None:
    """Decrypt an encrypted backup and restore the original pg_dump bytes."""
    output_path.write_bytes(decrypt_bytes(input_path.read_bytes(), key))
