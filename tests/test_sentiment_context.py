import unittest

from sentiment_context import fear_greed_guidance, snapshot_from_payload


class FearGreedContextTests(unittest.TestCase):
    def test_snapshot_includes_daily_and_weekly_change(self):
        payload = {
            "data": [
                {
                    "value": str(value),
                    "value_classification": "Fear" if index < 7 else "Neutral",
                    "timestamp": str(1_800_000_000 - index * 86_400),
                }
                for index, value in enumerate((27, 29, 30, 28, 26, 25, 31, 47))
            ],
            "metadata": {"error": None},
        }
        snapshot = snapshot_from_payload(payload)
        self.assertEqual(snapshot.value, 27)
        self.assertEqual(snapshot.label, "공포")
        self.assertEqual(snapshot.change_1d, -2)
        self.assertEqual(snapshot.change_7d, -20)
        self.assertEqual(snapshot.source, "Alternative.me")

    def test_extreme_fear_is_not_automatic_buy_signal(self):
        guidance = fear_greed_guidance(12, -8)
        self.assertIn("가격 구조 확인 전 진입 근거 아님", guidance)


if __name__ == "__main__":
    unittest.main()
