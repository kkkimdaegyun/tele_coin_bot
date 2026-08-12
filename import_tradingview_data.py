import argparse
from pathlib import Path

from market_data import import_tradingview_csv
from storage import ChartTeacherStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Import a TradingView chart CSV into Chart Teacher.")
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("symbol")
    parser.add_argument("interval", choices=("15m", "1h", "4h", "1d"))
    args = parser.parse_args()

    candles = import_tradingview_csv(args.csv_path, args.interval)
    count = ChartTeacherStore().upsert_candles(args.symbol.upper(), args.interval, candles)
    print(f"IMPORTED {args.symbol.upper()} {args.interval}: {count} candles")


if __name__ == "__main__":
    main()
