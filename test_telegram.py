import asyncio
from dotenv import load_dotenv
load_dotenv()

from formatter import build_message
from telegram_client import send_telegram

payload = {
    "event": "downtrend_breakout",
    "symbol": "ETHUSDT",
    "exchange": "BINANCE",
    "timeframe": "240",
    "price": 3200.0,
    "trendline_price": 3150.0,
}

async def main():
    message = build_message(payload)
    print(message)
    await send_telegram(message)
    print("\n텔레그램 테스트 전송 완료")

if __name__ == "__main__":
    asyncio.run(main())
