"""Direct Telethon auth diagnostic; does not use MCP or LifeLog persistence."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.settings import get_settings
from app.services.telegram_user_api import TelegramUserApi, translate_telegram_error


async def diagnose(phone: str) -> None:
    api = TelegramUserApi(get_settings())
    client = api.client()
    try:
        await client.connect()
        sent_code = await client.send_code_request(phone)
        metadata = api.sent_code_metadata(sent_code)
        print(json.dumps({
            "sent_code_class": sent_code.__class__.__name__,
            "sent_code_type": metadata["telegram_sent_code_type"],
            "sent_code_type_repr": repr(sent_code.type),
            "next_type": metadata["telegram_next_type"],
            "next_type_repr": repr(sent_code.next_type) if sent_code.next_type is not None else None,
            "timeout": metadata["telegram_timeout"],
            "phone_code_hash_present": metadata["phone_code_hash_present"],
        }, ensure_ascii=False))
    except Exception as error:
        translated = translate_telegram_error(error, "requesting the login code")
        print(json.dumps({
            "rpc_error_class": error.__class__.__name__,
            "error_code": translated.code,
            "retry_after": translated.retry_after,
        }, ensure_ascii=False))
    finally:
        await client.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("phone", help="Phone in international format")
    args = parser.parse_args()
    asyncio.run(diagnose(args.phone))


if __name__ == "__main__":
    main()
