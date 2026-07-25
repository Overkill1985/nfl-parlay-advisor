"""Unit tests for odds_math.py.

Pure functions, no I/O - run with: python -m unittest discover -s tests
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import odds_math


class TestAmericanToDecimal(unittest.TestCase):
    def test_none(self):
        self.assertIsNone(odds_math.american_to_decimal(None))

    def test_underdog_positive_odds(self):
        self.assertAlmostEqual(odds_math.american_to_decimal(150), 2.5)

    def test_favorite_negative_odds(self):
        self.assertAlmostEqual(odds_math.american_to_decimal(-150), 1 + 100 / 150)

    def test_even_money_boundaries(self):
        # +100 and -100 are both "even money" and should land on the same
        # decimal odds (2.0), even though they take different branches.
        self.assertAlmostEqual(odds_math.american_to_decimal(100), 2.0)
        self.assertAlmostEqual(odds_math.american_to_decimal(-100), 2.0)


class TestDecimalToAmerican(unittest.TestCase):
    def test_none(self):
        self.assertIsNone(odds_math.decimal_to_american(None))

    def test_at_or_below_one_is_invalid(self):
        self.assertIsNone(odds_math.decimal_to_american(1.0))
        self.assertIsNone(odds_math.decimal_to_american(0.5))

    def test_favorite_decimal_below_two(self):
        # 1.6667 decimal ~= -150 american
        self.assertEqual(odds_math.decimal_to_american(1 + 100 / 150), -150)

    def test_underdog_decimal_at_or_above_two(self):
        self.assertEqual(odds_math.decimal_to_american(2.5), 150)

    def test_boundary_at_exactly_two(self):
        # decimal_odds == 2 must take the >=2 branch (+100), not the other one.
        self.assertEqual(odds_math.decimal_to_american(2.0), 100)

    def test_round_trip_with_american_to_decimal(self):
        for american in (-500, -220, -110, -101, 105, 120, 250, 900):
            decimal = odds_math.american_to_decimal(american)
            self.assertAlmostEqual(
                odds_math.decimal_to_american(decimal), american, delta=1,
                msg=f"round-trip failed for {american}",
            )


class TestImpliedProbability(unittest.TestCase):
    def test_none(self):
        self.assertIsNone(odds_math.implied_probability(None))

    def test_underdog(self):
        self.assertAlmostEqual(odds_math.implied_probability(150), 100 / 250)

    def test_favorite(self):
        self.assertAlmostEqual(odds_math.implied_probability(-150), 150 / 250)

    def test_even_money_boundaries_agree(self):
        self.assertAlmostEqual(odds_math.implied_probability(100), 0.5)
        self.assertAlmostEqual(odds_math.implied_probability(-100), 0.5)

    def test_probability_in_unit_interval(self):
        for american in (-1000, -300, -110, 100, 110, 300, 1000):
            p = odds_math.implied_probability(american)
            self.assertGreater(p, 0)
            self.assertLess(p, 1)


class TestFairDecimalOdds(unittest.TestCase):
    def test_half_probability(self):
        self.assertAlmostEqual(odds_math.fair_decimal_odds(0.5), 2.0)

    def test_none_probability(self):
        self.assertIsNone(odds_math.fair_decimal_odds(None))

    def test_zero_probability(self):
        self.assertIsNone(odds_math.fair_decimal_odds(0))

    def test_negative_probability(self):
        self.assertIsNone(odds_math.fair_decimal_odds(-0.1))


class TestCombinedDecimalOdds(unittest.TestCase):
    def test_empty_list(self):
        self.assertEqual(odds_math.combined_decimal_odds([]), 1.0)

    def test_multiplies_legs(self):
        # -110 -> ~1.909..., two legs should multiply, not add.
        leg = odds_math.american_to_decimal(-110)
        combined = odds_math.combined_decimal_odds([-110, -110])
        self.assertAlmostEqual(combined, leg * leg)

    def test_skips_none_entries(self):
        leg = odds_math.american_to_decimal(150)
        combined = odds_math.combined_decimal_odds([150, None])
        self.assertAlmostEqual(combined, leg)


class TestPayoutProfit(unittest.TestCase):
    def test_payout(self):
        self.assertAlmostEqual(odds_math.payout(100, 2.5), 250)

    def test_profit(self):
        self.assertAlmostEqual(odds_math.profit(100, 2.5), 150)

    def test_profit_at_decimal_one_is_zero(self):
        self.assertAlmostEqual(odds_math.profit(100, 1.0), 0)


class TestExpectedValue(unittest.TestCase):
    def test_none_inputs_return_none(self):
        self.assertIsNone(odds_math.expected_value(None, 2.0, 100))
        self.assertIsNone(odds_math.expected_value(0.5, None, 100))
        self.assertIsNone(odds_math.expected_value(0.5, 2.0, None))

    def test_positive_edge(self):
        # True 60% shot priced at fair 50% (decimal 2.0) has positive EV.
        ev = odds_math.expected_value(0.6, 2.0, 100)
        self.assertAlmostEqual(ev, 0.6 * 100 - 0.4 * 100)
        self.assertGreater(ev, 0)

    def test_zero_edge_is_zero_ev(self):
        # A probability exactly matching the fair odds implied probability
        # should be breakeven.
        prob = 0.4
        decimal_odds = odds_math.fair_decimal_odds(prob)
        ev = odds_math.expected_value(prob, decimal_odds, 100)
        self.assertAlmostEqual(ev, 0, places=6)

    def test_negative_edge(self):
        ev = odds_math.expected_value(0.4, 2.0, 100)
        self.assertLess(ev, 0)


class TestRiskScore(unittest.TestCase):
    def test_empty_is_zero(self):
        self.assertEqual(odds_math.risk_score([]), 0)

    def test_bounded_between_zero_and_hundred(self):
        cases = [[0.99], [0.5, 0.5, 0.5, 0.5], [0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01]]
        for probs in cases:
            score = odds_math.risk_score(probs)
            self.assertGreaterEqual(score, 0)
            self.assertLessEqual(score, 100)

    def test_more_legs_increases_risk_all_else_equal(self):
        two_legs = odds_math.risk_score([0.8, 0.8])
        four_legs = odds_math.risk_score([0.8, 0.8, 0.8, 0.8])
        self.assertGreater(four_legs, two_legs)

    def test_weaker_leg_increases_risk(self):
        strong = odds_math.risk_score([0.9, 0.9])
        weak = odds_math.risk_score([0.9, 0.55])
        self.assertGreater(weak, strong)


if __name__ == "__main__":
    unittest.main()
