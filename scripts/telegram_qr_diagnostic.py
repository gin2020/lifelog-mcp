"""Run Telethon's official QRLogin flow outside MCP.

The script deliberately does not print the tg:// URL or token. It is useful
for proving that the client is executing QRLogin.wait(), while the MCP tool
is the safe place to return the URL to the authenticated requesting user.
"""

import argparse
import asyncio
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession

from app.config.settings import get_settings


async def run(timeout: float) -> dict[str, object]:
    settings = get_settings()
    if settings.telegram_api_id is None or settings.telegram_api_hash is None:
        raise RuntimeError("TELEGRAM_API_ID and TELEGRAM_API_HASH are required")
    client = TelegramClient(
        StringSession(),
        settings.telegram_api_id,
        settings.telegram_api_hash.get_secret_value(),
    )
    try:
        await client.connect()
        qr_login = await client.qr_login()
        qr_url = qr_login.url
        parsed = urlsplit(qr_url)
        result = {
            "client_class": client.__class__.__name__,
            "qr_login_class": qr_login.__class__.__name__,
            "qr_url_present": bool(qr_url),
            "qr_url_scheme": parsed.scheme,
            "qr_url_length": len(qr_url),
            "qr_expires_at": qr_login.expires.isoformat(),
            "wait_started": True,
        }
        try:
            user = await asyncio.wait_for(qr_login.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            result["status"] = "pending_timeout"
        except SessionPasswordNeededError:
            result["status"] = "password_required"
        else:
            result["status"] = "connected"
            result["user_object_class"] = user.__class__.__name__
            result["session_saved"] = bool(client.session.save())
        return result
    finally:
        await client.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.timeout)), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
