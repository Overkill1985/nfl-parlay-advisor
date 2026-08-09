"""Unit tests for odds_client.py's pure parsing/consensus-math logic.

No network access. Run with: python -m unittest discover -s tests
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import odds_client


class TestImpliedProbability(unittest.TestCase):
    def test_favorite_and_underdog_are_consistent(self):
        fav = odds_client._implied_probability(-300)
        dog = odds_client._implied_probability(240)
        self.assertGreater(fav, dog)

    def test_none_is_none(self):
        self.assertIsNone(odds_client._implied_probability(None))


class TestConsensusPrice(unittest.TestCase):
    def test_empty_list_is_none(self):
        self.assertIsNone(odds_client.consensus_price([]))

    def test_all_none_is_none(self):
        self.assertIsNone(odds_client.consensus_price([None, None]))

    def test_single_price_round_trips_closely(self):
        # Not an exact round-trip since we go price -> probability -> price,
        # but should land within a few points of the original.
        result = odds_client.consensus_price([-150])
        self.assertAlmostEqual(result, -150, delta=2)

    def test_averages_in_probability_space_not_raw_odds(self):
        # -303 and -290 are both favorites of similar size; consensus should
        # land between them, not wildly off.
        result = odds_client.consensus_price([-303, -290])
        self.assertLess(result, -280)
        self.assertGreater(result, -310)


class TestParseOddsEvents(unittest.TestCase):
    def _sample_event(self):
        return {
            "home_team": "Tampa Bay Buccaneers", "away_team": "Dallas Cowboys",
            "commence_time": "2025-09-10T00:20:00Z",
            "bookmakers": [
                {"key": "fanduel", "markets": [
                    {"key": "h2h", "outcomes": [
                        {"name": "Dallas Cowboys", "price": 240},
                        {"name": "Tampa Bay Buccaneers", "price": -303},
                    ]},
                    {"key": "spreads", "outcomes": [
                        {"name": "Dallas Cowboys", "point": 6.5},
                        {"name": "Tampa Bay Buccaneers", "point": -6.5},
                    ]},
                    {"key": "totals", "outcomes": [
                        {"name": "Over", "point": 47.5, "price": -110},
                        {"name": "Under", "point": 47.5, "price": -110},
                    ]},
                ]},
            ],
        }

    def test_basic_game_parses_correctly(self):
        games = odds_client.parse_odds_events([self._sample_event()])
        key = frozenset(("TB", "DAL"))
        self.assertIn(key, games)
        game = games[key]
        self.assertEqual(game["home_team"], "TB")
        self.assertEqual(game["away_team"], "DAL")
        self.assertEqual(game["favorite"], "TB")
        self.assertEqual(game["spread"]["TB"], -6.5)
        self.assertEqual(game["total"]["point"], 47.5)

    def test_unrecognized_team_name_is_dropped_not_guessed(self):
        event = self._sample_event()
        event["home_team"] = "Some Fictional Team"
        games = odds_client.parse_odds_events([event])
        self.assertEqual(games, {})

    def test_no_totals_market_leaves_total_none(self):
        event = self._sample_event()
        event["bookmakers"][0]["markets"] = [
            m for m in event["bookmakers"][0]["markets"] if m["key"] != "totals"
        ]
        games = odds_client.parse_odds_events([event])
        key = frozenset(("TB", "DAL"))
        self.assertIsNone(games[key]["total"])

    def test_favorite_is_most_negative_moneyline(self):
        event = self._sample_event()
        # Flip so Dallas is now the heavy favorite.
        event["bookmakers"][0]["markets"][0]["outcomes"] = [
            {"name": "Dallas Cowboys", "price": -400},
            {"name": "Tampa Bay Buccaneers", "price": 320},
        ]
        games = odds_client.parse_odds_events([event])
        key = frozenset(("TB", "DAL"))
        self.assertEqual(games[key]["favorite"], "DAL")


class TestGetOddsNoApiKey(unittest.TestCase):
    def test_returns_unavailable_without_network_call_when_key_unset(self):
        os.environ.pop("ODDS_API_KEY", None)
        result = odds_client.get_odds(force_refresh=True)
        self.assertEqual(result, {"available": False, "reason": "no_api_key"})


if __name__ == "__main__":
    unittest.main()
