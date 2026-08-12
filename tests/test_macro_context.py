import unittest

from macro_context import _impact


class MacroContextTests(unittest.TestCase):
    def test_risk_asset_impact_rules(self):
        self.assertEqual(_impact("yield", 4.5, 0.10), -1)
        self.assertEqual(_impact("yield", 4.5, -0.10), 1)
        self.assertEqual(_impact("risk_inverse", 120, 0.50), -1)
        self.assertEqual(_impact("risk_positive", 20_000, 1.50), 1)
        self.assertEqual(_impact("vix", 28, 2.0), -1)


if __name__ == "__main__":
    unittest.main()
