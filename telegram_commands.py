from __future__ import annotations

import asyncio
import html
import logging
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from analysis_service import analyze_symbol
from entry_strategy import format_krw
from position_manager import estimate_invalidation, position_change_percent
from storage import ChartTeacherStore
from strategy_universe import TRADE_SYMBOLS
from telegram_client import BOT_TOKEN, CHAT_ID, send_telegram


log = logging.getLogger("chart-teacher-telegram-commands")
SYMBOLS = {
    "BTC": "BTCUSDT",
    "비트": "BTCUSDT",
    "비트코인": "BTCUSDT",
    "ETH": "ETHUSDT",
    "이더": "ETHUSDT",
    "이더리움": "ETHUSDT",
    "SOL": "SOLUSDT",
    "솔": "SOLUSDT",
    "솔라나": "SOLUSDT",
    "XRP": "XRPUSDT",
    "리플": "XRPUSDT",
}
POSITION_SYMBOL_PATTERN = r"BTC|ETH|SOL|XRP|비트코인|비트|이더리움|이더|솔라나|솔|리플"
TRADE_SYMBOL_PATTERN = r"ETH|SOL|이더리움|이더|솔라나|솔"


@dataclass(frozen=True)
class TelegramCommand:
    action: str
    symbol: str | None = None
    amount_krw: int | None = None
    price: float | None = None


def parse_telegram_command(text: str) -> TelegramCommand | None:
    cleaned = " ".join(str(text).strip().replace("/", "").split())
    if cleaned.lower() in {"help", "도움말", "사용법", "명령어"}:
        return TelegramCommand("help")
    if cleaned.lower() in {"status", "현황", "보유현황", "포지션"}:
        return TelegramCommand("status")

    status_match = re.fullmatch(
        rf"({POSITION_SYMBOL_PATTERN})\s+(현황|상태|포지션)",
        cleaned,
        re.IGNORECASE,
    )
    if status_match:
        return TelegramCommand("status", SYMBOLS[status_match.group(1).upper()])

    buy_match = re.fullmatch(
        rf"({TRADE_SYMBOL_PATTERN})\s+"
        r"(매수|추가매수)\s+([0-9,.]+)\s*(억원|억|천만원|만원|만|원)\s+"
        r"([0-9,.]+)\s*([KkMm]?)",
        cleaned,
        re.IGNORECASE,
    )
    if buy_match:
        return TelegramCommand(
            "buy",
            SYMBOLS[buy_match.group(1).upper()],
            _parse_amount(buy_match.group(3), buy_match.group(4)),
            _parse_price(buy_match.group(5), buy_match.group(6)),
        )

    close_match = re.fullmatch(
        rf"({POSITION_SYMBOL_PATTERN})\s+"
        r"(전량매도|매도완료|포지션종료)\s+([0-9,.]+)\s*([KkMm]?)",
        cleaned,
        re.IGNORECASE,
    )
    if close_match:
        return TelegramCommand(
            "close",
            SYMBOLS[close_match.group(1).upper()],
            price=_parse_price(close_match.group(3), close_match.group(4)),
        )
    undo_match = re.fullmatch(
        rf"({POSITION_SYMBOL_PATTERN})\s+"
        r"(매수취소|최근매수취소|기록취소)",
        cleaned,
        re.IGNORECASE,
    )
    if undo_match:
        return TelegramCommand("undo_buy", SYMBOLS[undo_match.group(1).upper()])
    return None


def _parse_amount(raw: str, unit: str) -> int:
    value = float(raw.replace(",", ""))
    multiplier = {
        "억원": 100_000_000,
        "억": 100_000_000,
        "천만원": 10_000_000,
        "만원": 10_000,
        "만": 10_000,
        "원": 1,
    }[unit]
    amount = int(round(value * multiplier))
    if amount <= 0:
        raise ValueError("Amount must be positive")
    return amount


def _parse_price(raw: str, suffix: str) -> float:
    value = float(raw.replace(",", ""))
    multiplier = {"": 1, "K": 1_000, "M": 1_000_000}[suffix.upper()]
    price = value * multiplier
    if price <= 0:
        raise ValueError("Price must be positive")
    return price


def _fmt_price(value: float) -> str:
    if value >= 1_000:
        compact = value / 1_000
        return f"{compact:.2f}".rstrip("0").rstrip(".") + "K"
    return f"{value:,.4f}".rstrip("0").rstrip(".")


async def handle_telegram_command(command: TelegramCommand) -> str:
    store = ChartTeacherStore()
    if command.action == "help":
        return _help_message()
    if command.action == "status":
        positions = (
            [store.load_open_position(command.symbol)]
            if command.symbol
            else store.load_open_positions()
        )
        positions = [item for item in positions if item is not None]
        if not positions:
            return "📭 <b>현재 기록된 보유 포지션이 없습니다.</b>"
        lines = ["📒 <b>현재 보유 기록</b>"]
        for position in positions:
            coin = position["symbol"].removesuffix("USDT")
            lines.append(
                f"• <b>{coin}</b> · {position['stage']}차 · "
                f"{format_krw(position['invested_krw'])} · "
                f"평균 {_fmt_price(position['average_entry_price'])}"
            )
        lines.append("\n원화 손익은 USDT/KRW와 실제 수량을 입력하지 않아 표시하지 않습니다.")
        return "\n".join(lines)

    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    if command.action == "buy":
        if command.symbol not in TRADE_SYMBOLS:
            return "⛔ 신규 매수 기록은 ETH·SOL만 지원합니다. BTC는 시장 관찰용이고 XRP는 운용 대상에서 제외됐습니다."
        analysis = await analyze_symbol(command.symbol)
        invalidation = estimate_invalidation(analysis, command.price)
        position = store.record_buy(
            command.symbol,
            amount_krw=command.amount_krw,
            price=command.price,
            now_ms=now_ms,
            invalidation_price=invalidation,
        )
        coin = command.symbol.removesuffix("USDT")
        return (
            f"✅ <b>{coin} {position['stage']}차 매수 기록 완료</b>\n"
            f"이번 기록: {format_krw(command.amount_krw)} · {_fmt_price(command.price)}\n"
            f"누적: {format_krw(position['invested_krw'])} · "
            f"평균 {_fmt_price(position['average_entry_price'])}\n"
            f"보호 기준: 약 {_fmt_price(position['invalidation_price'])}\n\n"
            "이제 5분 감시에서 추가진입·보유·위험축소·분할매도 조건을 확인합니다. "
            "보호 기준은 기술적 관찰선이며 거래소 주문은 자동 실행하지 않습니다."
        )

    if command.action == "close":
        closed = store.close_position(command.symbol, price=command.price, now_ms=now_ms)
        if closed is None:
            return "📭 해당 코인의 열린 포지션 기록이 없습니다."
        coin = command.symbol.removesuffix("USDT")
        change = position_change_percent(closed, command.price)
        return (
            f"✅ <b>{coin} 포지션 종료 기록</b>\n"
            f"평균 {_fmt_price(closed['average_entry_price'])} → "
            f"종료 {_fmt_price(command.price)} · 가격 변화 {change:+.2f}%\n"
            "원화 확정손익은 실제 수량·수수료·USDT/KRW가 없어 계산하지 않았습니다."
        )
    if command.action == "undo_buy":
        result = store.undo_last_buy(command.symbol, now_ms=now_ms)
        if result is None:
            return "📭 취소할 열린 매수 기록이 없습니다."
        coin = command.symbol.removesuffix("USDT")
        undone = result["undone"]
        position = result["position"]
        lines = [
            f"↩️ <b>{coin} 최근 매수 기록 취소</b>",
            f"취소: {format_krw(undone['amount_krw'])} · {_fmt_price(undone['price'])}",
        ]
        if position is None:
            lines.append("남은 매수 기록이 없어 일반 감시 모드로 돌아갑니다.")
        else:
            lines.append(
                f"남은 기록: {position['stage']}차 · {format_krw(position['invested_krw'])} · "
                f"평균 {_fmt_price(position['average_entry_price'])}"
            )
        return "\n".join(lines)
    raise ValueError("Unsupported command")


def _help_message() -> str:
    return (
        "📘 <b>Chart Teacher 보유관리 명령</b>\n\n"
        "매수 기록\n<code>ETH 매수 3300만원 3.2K</code>\n"
        "추가매수 기록\n<code>SOL 추가매수 3300만원 150</code>\n"
        "보유 확인\n<code>ETH 현황</code> 또는 <code>현황</code>\n"
        "최근 매수 오타 취소\n<code>ETH 매수취소</code>\n"
        "전량매도 기록\n<code>ETH 전량매도 4K</code>\n\n"
        "신규 매수 기록은 ETH·SOL만 지원합니다. BTC는 시장 관찰용이고 XRP 자동 알림은 꺼져 있습니다. "
        "메시지는 기록 기능이며 실제 주문을 실행하지 않습니다."
    )


async def telegram_command_loop() -> None:
    if not BOT_TOKEN or not CHAT_ID:
        log.warning("Telegram command polling disabled: credentials missing")
        return
    store = ChartTeacherStore()
    offset_raw = store.get_state("telegram_update_offset")
    offset = int(offset_raw) if offset_raw and offset_raw.isdigit() else None
    if offset is None:
        offset = await _skip_old_updates(store)

    while True:
        try:
            updates = await _get_updates(offset, timeout=25)
            for update in updates:
                update_id = int(update.get("update_id", 0))
                offset = max(offset, update_id + 1)
                # Save before handling so a restart cannot duplicate a buy record.
                store.set_state("telegram_update_offset", str(offset))
                message = update.get("message") or {}
                chat = message.get("chat") or {}
                if str(chat.get("id", "")) != str(CHAT_ID):
                    continue
                text = message.get("text")
                if not isinstance(text, str):
                    continue
                try:
                    command = parse_telegram_command(text)
                except (TypeError, ValueError):
                    await send_telegram("형식을 읽지 못했습니다. <code>도움말</code>을 보내 확인하세요.")
                    continue
                if command is None:
                    continue
                await send_telegram(await handle_telegram_command(command))
        except asyncio.CancelledError:
            raise
        except Exception:
            # Never log the request URL because it contains the bot token.
            log.exception("Telegram command polling failed")
            await asyncio.sleep(5)


async def _skip_old_updates(store: ChartTeacherStore) -> int:
    updates = await _get_updates(-1, timeout=0, limit=1)
    offset = max((int(item.get("update_id", 0)) + 1 for item in updates), default=0)
    store.set_state("telegram_update_offset", str(offset))
    return offset


async def _get_updates(offset: int, *, timeout: int, limit: int = 50) -> list[dict]:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    params = {
        "offset": offset,
        "timeout": timeout,
        "limit": limit,
        "allowed_updates": '["message"]',
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout + 10.0)) as client:
            response = await client.get(url, params=params)
    except httpx.HTTPError:
        raise RuntimeError("Telegram getUpdates request failed") from None
    if response.status_code >= 400:
        raise RuntimeError(f"Telegram getUpdates returned HTTP {response.status_code}")
    try:
        body = response.json()
    except ValueError:
        raise RuntimeError("Telegram getUpdates returned invalid JSON") from None
    if not body.get("ok") or not isinstance(body.get("result"), list):
        raise RuntimeError("Telegram getUpdates was rejected")
    return body["result"]
