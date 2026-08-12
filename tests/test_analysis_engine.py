import math
import tempfile
import unittest
from pathlib import Path

from analysis_engine import _consolidation_patterns, analyze_market, analyze_timeframe, find_historical_analogs
from analysis_report import build_analysis_message, build_ema_signal_message, build_hourly_summary_message
from ema_chart import render_ema_chart_png
from ema_signals import alignment_state, detect_ema_signals
from investor_guidance import build_bottom_decision
from sentiment_context import FearGreedSnapshot
from market_data import Candle
from storage import ChartTeacherStore


def candles(count: int = 400, step: float = 0.5) -> list[Candle]:
    result = []
    price = 100.0
    for index in range(count):
        opened = price
        closed = 100 + index * step + math.sin(index / 4) * 5
        result.append(
            Candle(
                open_time=index * 60_000,
                open=opened,
                high=max(opened, closed) + 1.0,
                low=min(opened, closed) - 1.0,
                close=closed,
                volume=1000 + index * 2,
                close_time=(index + 1) * 60_000 - 1,
            )
        )
        price = closed
    return result


class AnalysisEngineTests(unittest.TestCase):
    def test_uptrend_indicators_and_score(self):
        result = analyze_timeframe("4h", candles())
        self.assertIsNotNone(result.ema200)
        self.assertGreater(result.direction_score, 50)
        self.assertEqual(result.structure, "bullish")

    def test_market_report_contains_teacher_sections(self):
        dataset = candles()
        analysis = analyze_market("BTCUSDT", {key: dataset for key in ("1d", "4h", "1h", "15m")})
        message = build_analysis_message(analysis)
        self.assertIn("핵심 지표", message)
        self.assertIn("바닥 단서 · 확률 아님", message)
        self.assertIn("과거 유사 구간", message)
        self.assertIn("투자자가 알아야 할 점", message)

    def test_historical_analogs_have_forward_returns(self):
        analogs = find_historical_analogs(candles(500))
        self.assertEqual(len(analogs), 3)
        self.assertIsNotNone(analogs[0].return_long)

    def test_bottom_score_is_rule_not_probability(self):
        dataset = candles()
        analysis = analyze_market("BTCUSDT", {key: dataset for key in ("1d", "4h", "1h", "15m")})
        analysis.bottom_score = 27
        early = build_bottom_decision(analysis)
        self.assertEqual(early.stage, "초기 단서")
        self.assertIn("신규 진입 보류", early.action)
        self.assertFalse(early.confirmation_ready)

        analysis.bottom_score = 75
        strong = build_bottom_decision(analysis)
        self.assertEqual(strong.stage, "강한 후보·확인 동반")
        self.assertIn("검토 가능", strong.action)
        self.assertTrue(strong.confirmation_ready)

    def test_sqlite_upsert_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ChartTeacherStore(Path(directory) / "test.db")
            sample = candles(50)
            store.upsert_candles("BTCUSDT", "4h", sample)
            store.upsert_candles("BTCUSDT", "4h", sample)
            self.assertEqual(len(store.load_candles("BTCUSDT", "4h")), 50)

    def test_bullish_pennant_candidate(self):
        sample = candles(70, step=1.0)
        highs = [(45, 150.0), (53, 145.0), (61, 140.0)]
        lows = [(47, 120.0), (55, 125.0), (63, 130.0)]
        patterns = _consolidation_patterns(sample, highs, lows)
        self.assertTrue(any(pattern.name == "상승 페넌트" for pattern in patterns))

    def test_hourly_summary_contains_live_prices(self):
        dataset = candles()
        analyses = [
            analyze_market(symbol, {key: dataset for key in ("1d", "4h", "1h", "15m")})
            for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT")
        ]
        analyses[0].current_price = 64_000
        analyses[1].current_price = 3_200
        analyses[2].current_price = 150
        sentiment = FearGreedSnapshot(
            value=27,
            label="공포",
            yesterday_value=29,
            week_value=27,
            change_1d=-2,
            change_7d=0,
            timestamp=1_800_000_000,
            guidance="공포 구간 · 단독 매수 신호 아님",
        )
        message = build_hourly_summary_message(analyses, sentiment=sentiment)
        self.assertIn("1시간 정기 차트 브리핑", message)
        self.assertIn("BTC · 64K", message)
        self.assertIn("ETH · 3.2K", message)
        self.assertIn("SOL · 150", message)
        self.assertIn("한줄평:", message)
        self.assertIn("투자자 체크:", message)
        self.assertIn("시장심리(BTC 중심)", message)
        self.assertIn("출처: Alternative.me", message)

    def test_ema_touch_and_alignment_transition(self):
        sample = candles(260, step=0.2)
        # Force the last confirmed bar to complete a bullish 20 > 50 > 200 alignment.
        boosted = list(sample)
        previous = boosted[-1]
        boosted[-1] = Candle(
            open_time=previous.open_time,
            open=previous.open,
            high=previous.high + 100,
            low=previous.low,
            close=previous.close + 100,
            volume=previous.volume,
            close_time=previous.close_time,
        )
        closes = [item.close for item in boosted]
        from analysis_engine import ema
        ema20 = ema(closes, 20)[-1]
        live = Candle(
            open_time=boosted[-1].open_time + 60_000,
            open=ema20,
            high=ema20 * 1.001,
            low=ema20 * 0.999,
            close=ema20,
            volume=1000,
            close_time=boosted[-1].close_time + 60_000,
        )
        signals = detect_ema_signals(
            {interval: boosted for interval in ("15m", "1h", "4h", "1d")},
            {interval: live for interval in ("15m", "1h", "4h", "1d")},
        )
        self.assertEqual(alignment_state(3, 2, 1), "bullish")
        self.assertEqual(alignment_state(1, 2, 3), "bearish")
        self.assertTrue(any(item.event == "ema_20_touch" for item in signals))
        analysis = analyze_market(
            "BTCUSDT",
            {key: boosted for key in ("1d", "4h", "1h", "15m")},
        )
        message = build_ema_signal_message(analysis, signals, tolerance_percent=0.05)
        self.assertIn("한줄 결론", message)
        self.assertIn("투자자가 알아야 할 점", message)
        self.assertTrue("하락" in message or "상승" in message)
        one_hour_signals = [item for item in signals if item.interval == "1h"]
        image = render_ema_chart_png(
            "BTCUSDT",
            {"1h": boosted},
            {"1h": live},
            one_hour_signals,
        )
        self.assertTrue(image.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertGreater(len(image), 10_000)
        overview = render_ema_chart_png(
            "BTCUSDT",
            {"1h": boosted, "4h": boosted},
            {"1h": live},
            one_hour_signals,
            analysis=analysis,
            intervals_override=["1h"],
        )
        self.assertTrue(overview.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertGreater(len(overview), len(image))


if __name__ == "__main__":
    unittest.main()
