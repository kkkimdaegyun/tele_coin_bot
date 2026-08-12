import argparse
import asyncio

from analysis_service import SUPPORTED_SYMBOLS, backfill_symbol
from storage import ChartTeacherStore


async def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill public Binance OHLCV history into SQLite.")
    parser.add_argument("--years", type=float, default=3.0)
    parser.add_argument("--symbols", nargs="+", choices=SUPPORTED_SYMBOLS, default=list(SUPPORTED_SYMBOLS))
    args = parser.parse_args()

    if not 0 < args.years <= 10:
        parser.error("--years must be between 0 and 10")
    for symbol in args.symbols:
        counts = await backfill_symbol(symbol, years=args.years)
        print(symbol, " ".join(f"{interval}={count}" for interval, count in counts.items()))
    print("TOTAL", ChartTeacherStore().counts())


if __name__ == "__main__":
    asyncio.run(main())
