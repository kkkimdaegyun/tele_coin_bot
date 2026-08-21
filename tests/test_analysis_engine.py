import math
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from analysis_engine import _consolidation_patterns, analyze_market, analyze_timeframe, find_historical_analogs
from analysis_report import (
    build_analysis_message,
    build_compact_analysis_caption,
    build_ema_signal_message,
    build_hourly_summary_message,
    build_usdt_dominance_message,
)
from dominance_context import dominance_regime, snapshot_from_payloads
from ema_chart import render_ema_chart_png
from ema_signals import alignment_state, detect_ema_signals
from entry_strategy import (
    capital_action_line,
    capital_plan_line,
    entry_headline,
    evaluate_entry,
)
from investor_guidance import build_bottom_decision
from sentiment_context import FearGreedSnapshot
from market_data import Candle
from market_monitor import (
    DEFAULT_MONITOR_SYMBOLS,
    _RECENT_ALERT_IMAGES,
    _mark_recent_alert_image,
    _recent_alert_image_active,
    _significant_signal,
    quiet_hours_active,
)
from pattern_education import pattern_one_line
from storage import ChartTeacherStore
from wave_context import wave_summary


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
                taker_buy_volume=(1000 + index * 2) * 0.55,
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
        self.assertIn("핵심 3종", message)

    def test_double_bottom_has_beginner_one_line(self):
        dataset = candles()
        analysis = analyze_market("BTCUSDT", {key: dataset for key in ("1d", "4h", "1h", "15m")})
        analysis.important_patterns = [{
            "timeframe": "1d",
            "name": "쌍바닥",
            "direction": "bullish",
            "confidence": 82,
            "status": "confirmed",
            "evidence": "두 저점과 넥라인이 확인됐습니다.",
        }]
        message = build_analysis_message(analysis)
        self.assertIn("1D 쌍바닥", message)
        self.assertIn("한줄 설명:", message)
        self.assertIn("두 번 저점", message)
        caption = build_compact_analysis_caption(
            analysis,
            {"event": "pattern:쌍바닥", "timeframe": "1d", "description": "1d 쌍바닥 확인"},
        )
        self.assertTrue(caption.startswith("<b>🔴 BTC · 지금 신규진입 0원 · 추격 금지</b>"))
        self.assertIn("<b>감지 계기</b> · 1D 쌍바닥", caption)
        self.assertLessEqual(len(caption), 1024)
        self.assertIn("넥라인", pattern_one_line("쌍바닥", "confirmed"))

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

    def test_usdt_dominance_snapshot_and_local_storage(self):
        snapshot = snapshot_from_payloads(
            {
                "data": {
                    "market_cap_percentage": {"usdt": 7.20},
                    "total_market_cap": {"usd": 2_500_000_000_000},
                    "market_cap_change_percentage_24h_usd": 3.0,
                    "volume_change_percentage_24h_usd": 16.0,
                    "updated_at": 1_800_000_000,
                }
            },
            [
                {
                    "market_cap": 180_000_000_000,
                    "market_cap_change_percentage_24h": 0.0,
                }
            ],
        )
        self.assertEqual(snapshot.regime, "risk_on")
        self.assertLess(snapshot.change_24h_pp, 0)
        self.assertIn("단독 매수 신호는 아닙니다", build_usdt_dominance_message(snapshot))

        with tempfile.TemporaryDirectory() as directory:
            store = ChartTeacherStore(Path(directory) / "dominance.db")
            store.record_dominance_snapshot(
                observed_at=snapshot.timestamp,
                usdt_dominance=snapshot.value,
                usdt_market_cap_usd=snapshot.usdt_market_cap_usd,
                total_market_cap_usd=snapshot.total_market_cap_usd,
                dominance_change_24h_pp=snapshot.change_24h_pp,
                total_market_change_24h=snapshot.total_market_change_24h,
                volume_change_24h=snapshot.volume_change_24h,
            )
            stored = store.load_dominance_before(snapshot.timestamp)
            self.assertIsNotNone(stored)
            self.assertAlmostEqual(stored["usdt_dominance"], 7.2)

    def test_usdt_dominance_regime_directions(self):
        self.assertEqual(dominance_regime(7.2, -0.2, 3.0)[0], "risk_on")
        self.assertEqual(dominance_regime(7.5, 0.2, -3.0)[0], "risk_off")

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
        for analysis in analyses:
            analysis.dominance_context = {
                "value": 7.2,
                "change_24h_pp": -0.2,
                "change_1h_pp": None,
                "change_4h_pp": None,
                "regime": "risk_on",
                "label": "위험선호 우호",
                "guidance": "과열 추격 근거는 아님",
            }
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
        self.assertIn("🔴 BTC · 지금 신규진입 0원 · 추격 금지", message)
        self.assertIn("현재가: 64K", message)
        self.assertIn("현재가: 3.2K", message)
        self.assertIn("현재가: 150", message)
        self.assertIn("한줄평:", message)
        self.assertIn("투자자 체크:", message)
        self.assertIn("시장심리(BTC 중심)", message)
        self.assertIn("출처: Alternative.me", message)
        self.assertIn("USDT.D 시장 필터", message)

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
            taker_buy_volume=previous.taker_buy_volume,
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
            taker_buy_volume=650,
        )
        signals = detect_ema_signals(
            {interval: boosted for interval in ("15m", "1h", "4h", "1d")},
            {interval: live for interval in ("15m", "1h", "4h", "1d")},
        )
        self.assertEqual(alignment_state(3, 2, 1), "bullish")
        self.assertEqual(alignment_state(1, 2, 3), "bearish")
        self.assertTrue(any(item.event.startswith("ema_20_touch_") for item in signals))
        self.assertFalse(any(item.interval == "15m" for item in signals))
        self.assertTrue(
            all(item.reaction in {"closed_above", "closed_below"} for item in signals if item.kind == "touch")
        )
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

    def test_taker_buy_pressure_is_reported(self):
        dataset = candles()
        analysis = analyze_market(
            "BTCUSDT",
            {key: dataset for key in ("1d", "4h", "1h", "15m")},
        )
        detail = analysis.timeframes["4h"]
        self.assertAlmostEqual(detail.taker_buy_ratio, 0.55, places=2)
        self.assertAlmostEqual(detail.taker_buy_ratio_5, 0.55, places=2)
        message = build_analysis_message(analysis)
        self.assertIn("체결 매수 비중", message)
        self.assertIn("최근 5봉", message)

    def test_signal_cooldown(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ChartTeacherStore(Path(directory) / "cooldown.db")
            self.assertTrue(store.claim_cooldown("btc:1h:ema20", 10_000, 4_000))
            self.assertFalse(store.claim_cooldown("btc:1h:ema20", 12_000, 4_000))
            self.assertTrue(store.claim_cooldown("btc:1h:ema20", 14_000, 4_000))

    def test_quiet_hours_use_kst_boundary(self):
        kst = timezone(timedelta(hours=9))
        with patch.dict(
            os.environ,
            {"QUIET_HOURS_START_KST": "0", "QUIET_HOURS_END_KST": "7"},
        ):
            self.assertTrue(quiet_hours_active(datetime(2026, 8, 13, 6, 59, tzinfo=kst)))
            self.assertFalse(quiet_hours_active(datetime(2026, 8, 13, 7, 0, tzinfo=kst)))

    def test_sol_is_enabled_by_default(self):
        self.assertEqual(DEFAULT_MONITOR_SYMBOLS, ("BTCUSDT", "ETHUSDT", "SOLUSDT"))

    def test_rsi_threshold_crossing_is_an_important_signal(self):
        dataset = candles()
        analysis = analyze_market(
            "BTCUSDT",
            {key: dataset for key in ("1d", "4h", "1h", "15m")},
        )
        analysis.important_patterns = []
        analysis.direction_score = 50
        analysis.signal_strength = 0
        analysis.bottom_score = 0
        for detail in analysis.timeframes.values():
            detail.rsi_previous = detail.rsi
            detail.volume_ratio = 1.0
            detail.taker_buy_ratio = 0.5
            detail.change_24h = 0
            detail.ema20_distance_percent = 0
            detail.breakout_20 = False
            detail.ema20_touched = False
        analysis.timeframes["4h"].rsi_previous = 69.0
        analysis.timeframes["4h"].rsi = 71.0
        signal = _significant_signal(analysis)
        self.assertIsNotNone(signal)
        self.assertEqual(signal["event"], "rsi_overbought_entry")
        self.assertEqual(signal["timeframe"], "4h")

    def test_recent_alert_image_suppresses_hourly_duplicate_for_ten_minutes(self):
        _RECENT_ALERT_IMAGES.clear()
        with patch.dict(os.environ, {"HOURLY_DUPLICATE_WINDOW_MINUTES": "10"}):
            _mark_recent_alert_image("BTCUSDT", 1_000)
            self.assertTrue(_recent_alert_image_active("BTCUSDT", 600_999))
            self.assertFalse(_recent_alert_image_active("BTCUSDT", 601_000))

    def test_entry_engine_allows_confirmed_breakout_but_blocks_fomo_chase(self):
        dataset = candles()
        analysis = analyze_market(
            "BTCUSDT",
            {key: dataset for key in ("1d", "4h", "1h", "15m")},
        )
        for detail in analysis.timeframes.values():
            detail.direction_score = 62
            detail.close = 110
            detail.ema20 = 105
            detail.ema50 = 100
            detail.ema200 = 95
            detail.rsi = 60
            detail.rsi_previous = 58
            detail.change_24h = 2
            detail.ema20_distance_percent = 0.5
            detail.atr_percent = 0.8
            detail.volume_ratio = 1.1
            detail.taker_buy_ratio = 0.52
            detail.breakout_20 = False
            detail.ema20_touched = False
        trigger = analysis.timeframes["15m"]
        trigger.breakout_20 = True
        trigger.volume_ratio = 1.8
        trigger.taker_buy_ratio = 0.64
        trigger.rsi = 61
        decision = evaluate_entry(analysis)
        self.assertEqual(decision.action, "first_entry_review")
        self.assertGreaterEqual(decision.score, 65)
        self.assertIsNotNone(decision.invalidation)
        self.assertIn("1차 분할 진입 검토", entry_headline("BTCUSDT", decision))
        analysis.important_patterns = [
            {
                "name": "하락 추세선 돌파",
                "direction": "bullish",
                "confidence": 90,
                "status": "confirmed",
                "evidence": "test",
                "timeframe": "4h",
            }
        ]
        priority_signal = _significant_signal(analysis)
        self.assertEqual(priority_signal["event"], "entry:first_entry_review")

        trigger.change_24h = 10
        analysis.timeframes["1h"].rsi = 82
        analysis.timeframes["1h"].ema20_distance_percent = 4.5
        chase = evaluate_entry(analysis)
        self.assertEqual(chase.action, "wait_pullback")
        self.assertIn("추격", chase.action_label)

        trigger.change_24h = 2
        analysis.timeframes["1h"].rsi = 60
        analysis.timeframes["1h"].ema20_distance_percent = 0.5
        trigger.rsi = 88
        trigger.ema20_distance_percent = 3.0
        parabolic = evaluate_entry(analysis)
        self.assertEqual(parabolic.action, "wait_pullback")
        self.assertTrue(any("15m RSI" in item for item in parabolic.blockers))

        trigger.rsi = 61
        trigger.ema20_distance_percent = 0.5
        analysis.dominance_context = {
            "regime": "risk_off",
            "change_24h_pp": 0.2,
        }
        risk_off = evaluate_entry(analysis)
        self.assertEqual(risk_off.action, "wait_pullback")
        self.assertTrue(any("USDT.D" in item for item in risk_off.blockers))

    def test_capital_plan_uses_seventy_million_and_first_ten_percent(self):
        dataset = candles()
        analysis = analyze_market(
            "BTCUSDT",
            {key: dataset for key in ("1d", "4h", "1h", "15m")},
        )
        with patch.dict(
            os.environ,
            {
                "PLANNED_CAPITAL_KRW_BTCUSDT": "70000000",
                "ENTRY_FIRST_TRANCHE_PERCENT": "10",
                "ENTRY_SECOND_TRANCHE_PERCENT": "15",
                "ENTRY_THIRD_TRANCHE_PERCENT": "25",
            },
        ):
            decision = evaluate_entry(analysis)
        self.assertEqual(decision.capital_plan.planned_krw, 70_000_000)
        self.assertEqual(decision.capital_plan.first_krw, 7_000_000)
        self.assertEqual(decision.capital_plan.reserve_krw, 35_000_000)
        self.assertIn("700만원", capital_action_line(decision))
        self.assertIn("예비 3,500만원", capital_plan_line(decision))

    def test_wave_summary_labels_structure_score_as_not_probability(self):
        summary = wave_summary(
            {
                "direction": "bullish",
                "phase": "3파 진행 후보",
                "current_extension": 1.2,
                "next_level_label": "1.618",
                "next_level": 123.45,
                "structure_score": 70,
            }
        )
        self.assertIn("1.618", summary)
        self.assertIn("확률 아님", summary)


if __name__ == "__main__":
    unittest.main()
