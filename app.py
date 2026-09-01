import asyncio
import hmac
import json
import logging
import os
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name(".env"))

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from analysis_service import build_live_report
from market_monitor import monitor_loop
from telegram_client import send_telegram
from telegram_commands import telegram_command_loop
from strategy_universe import TRADE_SYMBOLS

log = logging.getLogger("chart-teacher-bot")

WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()


@asynccontextmanager
async def lifespan(application: FastAPI):
    monitor_enabled = os.getenv("AUTO_MONITOR_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
    commands_enabled = os.getenv("TELEGRAM_COMMANDS_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
    monitor_task = asyncio.create_task(monitor_loop()) if monitor_enabled else None
    command_task = asyncio.create_task(telegram_command_loop()) if commands_enabled else None
    try:
        yield
    finally:
        for task in (monitor_task, command_task):
            if task:
                task.cancel()
        for task in (monitor_task, command_task):
            if task:
                with suppress(asyncio.CancelledError):
                    await task


app = FastAPI(title="Chart Teacher Bot", version="0.3.0", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"ok": True, "service": "chart-teacher-bot"}


async def _shutdown_server(server) -> None:
    # Let the HTTP response finish before asking Uvicorn to exit gracefully.
    await asyncio.sleep(0.2)
    server.should_exit = True


@app.post("/internal/shutdown", include_in_schema=False)
async def shutdown(request: Request, background_tasks: BackgroundTasks):
    expected = getattr(app.state, "control_token", "")
    supplied = request.headers.get("x-chart-teacher-control", "")
    if not expected or not supplied or not hmac.compare_digest(expected, supplied):
        raise HTTPException(status_code=403, detail="invalid control token")

    server = getattr(app.state, "uvicorn_server", None)
    if server is None:
        raise HTTPException(status_code=503, detail="server control unavailable")

    app.state.controlled_shutdown = True
    background_tasks.add_task(_shutdown_server, server)
    return {"ok": True}


async def _deliver(payload: dict):
    try:
        symbol = str(payload.get("symbol", "")).upper().replace("BINANCE:", "")
        event = str(payload.get("event", "TradingView Webhook"))
        if symbol not in TRADE_SYMBOLS:
            log.info("Webhook ignored for non-trading symbol: symbol=%s", symbol)
            return
        _, message = await build_live_report(symbol, trigger=event)
        await send_telegram(message)
        log.info("Telegram sent: event=%s symbol=%s tf=%s",
                 payload.get("event"), payload.get("symbol"), payload.get("timeframe"))
    except Exception:
        log.exception("Telegram delivery failed")

@app.post("/webhook/{secret}")
async def tradingview_webhook(secret: str, request: Request, background_tasks: BackgroundTasks):
    if not WEBHOOK_SECRET or secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="invalid secret")

    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise ValueError("payload must be object")
    except Exception:
        raw = (await request.body()).decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {
                "event": "generic",
                "symbol": "UNKNOWN",
                "timeframe": "-",
                "price": "-",
                "raw": raw[:1000],
            }

    # TradingView는 빠른 응답이 중요하므로 Telegram 전송은 background task로 넘깁니다.
    background_tasks.add_task(_deliver, payload)
    return {"ok": True}
