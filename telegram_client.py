import asyncio
import os
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name(".env"))

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
_SEND_LOCK = asyncio.Lock()
_LAST_SEND_AT = 0.0


async def _wait_for_free_send_slot() -> None:
    global _LAST_SEND_AT
    wait_seconds = 1.05 - (time.monotonic() - _LAST_SEND_AT)
    if wait_seconds > 0:
        await asyncio.sleep(wait_seconds)


def _mark_sent() -> None:
    global _LAST_SEND_AT
    _LAST_SEND_AT = time.monotonic()

async def send_telegram(text: str) -> None:
    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID가 설정되지 않았습니다.")

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    async with _SEND_LOCK:
        await _wait_for_free_send_slot()
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                response = await client.post(url, json=payload)
        except httpx.HTTPError:
            # httpx exceptions can contain the request URL, which contains the bot token.
            raise RuntimeError("Telegram API request failed") from None
        _mark_sent()

    if response.status_code >= 400:
        raise RuntimeError(f"Telegram API returned HTTP {response.status_code}")

    try:
        body = response.json()
    except ValueError:
        raise RuntimeError("Telegram API returned an invalid response") from None

    if not body.get("ok"):
        raise RuntimeError("Telegram API rejected the message")


async def send_telegram_photo(image: bytes, caption: str, filename: str = "chart.png") -> None:
    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID가 설정되지 않았습니다.")
    if not image:
        raise ValueError("Telegram photo is empty")
    if len(caption) > 1024:
        raise ValueError("Telegram photo caption is too long")

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    data = {"chat_id": CHAT_ID, "caption": caption, "parse_mode": "HTML"}
    files = {"photo": (filename, image, "image/png")}
    async with _SEND_LOCK:
        await _wait_for_free_send_slot()
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(url, data=data, files=files)
        except httpx.HTTPError:
            raise RuntimeError("Telegram photo request failed") from None
        _mark_sent()
    if response.status_code >= 400:
        raise RuntimeError(f"Telegram photo API returned HTTP {response.status_code}")
    try:
        body = response.json()
    except ValueError:
        raise RuntimeError("Telegram photo API returned an invalid response") from None
    if not body.get("ok"):
        raise RuntimeError("Telegram photo API rejected the image")
