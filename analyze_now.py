import argparse
import asyncio
import json

from analysis_service import build_live_report
from telegram_client import send_telegram


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run a multi-timeframe Chart Teacher analysis.")
    parser.add_argument("symbol", choices=("BTCUSDT", "ETHUSDT", "SOLUSDT"))
    parser.add_argument("--telegram", action="store_true", help="Send the report to Telegram.")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of the formatted report.")
    args = parser.parse_args()

    analysis, message = await build_live_report(args.symbol, trigger="수동 종합 분석")
    print(json.dumps(analysis.to_dict(), ensure_ascii=False, indent=2) if args.json else message)
    if args.telegram:
        await send_telegram(message)
        print("\nTelegram 종합 분석 전송 완료")


if __name__ == "__main__":
    asyncio.run(main())
