"""Unit tests for correlation.py - the first test file for this module.

No network access - pure logic over plain leg dicts.
Run with: python -m unittest discover -s tests
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import correlation


def _model_leg(player, team, stat, direction="Over", line=100.0, week=1, player_id=1, label=None):
    return {
        "player_id": player_id, "player": player, "team": team, "stat": stat,
        "label": label or stat, "direction": direction, "line": line, "week": week,
    }


class TestFromModelLeg(unittest.TestCase):
    def test_basic_fields_carried_over(self):
        leg = _model_leg("Josh Allen", "BUF", "pass_yds", line=250.0)
        normalized = correlation.from_model_leg(leg)
        self.assertEqual(normalized["team"], "BUF")
        self.assertEqual(normalized["market_type"], "player_prop")
        self.assertIn("Josh Allen", normalized["description"])
        self.assertIn("250.0", normalized["description"])

    def test_anytime_td_description_has_no_line(self):
        leg = _model_leg("James Cook", "BUF", "anytime_td", direction="Yes", line=None, label="Anytime TD")
        normalized = correlation.from_model_leg(leg)
        self.assertNotIn("None", normalized["description"])


class TestFromPick(unittest.TestCase):
    def test_player_prop_pick(self):
        pick = {"id": 1, "player_name": "Josh Allen", "team": "BUF", "direction": "Over",
                "line_entered": 250.0, "label": "Passing Yards", "market_type": "player_prop"}
        normalized = correlation.from_pick(pick)
        self.assertIn("Josh Allen", normalized["description"])

    def test_game_market_pick_without_player_name(self):
        pick = {"id": 2, "player_name": None, "team": "BUF", "market_type": "moneyline",
                "direction": None, "line_entered": None, "odds_american": -180}
        normalized = correlation.from_pick(pick)
        self.assertIn("BUF", normalized["description"])
        self.assertEqual(normalized["market_type"], "moneyline")


class TestAttachOpponents(unittest.TestCase):
    def test_fills_in_opponent_from_schedule(self):
        legs = [{"team": "BUF", "week": 1, "opponent": None}]
        schedule = {"weeks": {"1": {"BUF": {"opponent": "HOU"}}}}
        result = correlation.attach_opponents(legs, schedule)
        self.assertEqual(result[0]["opponent"], "HOU")

    def test_leaves_existing_opponent_alone(self):
        legs = [{"team": "BUF", "week": 1, "opponent": "MIA"}]
        schedule = {"weeks": {"1": {"BUF": {"opponent": "HOU"}}}}
        result = correlation.attach_opponents(legs, schedule)
        self.assertEqual(result[0]["opponent"], "MIA")

    def test_missing_team_or_week_is_skipped(self):
        legs = [{"team": None, "week": 1, "opponent": None}]
        result = correlation.attach_opponents(legs, {"weeks": {}})
        self.assertIsNone(result[0]["opponent"])


class TestAnalyzeCorrelations(unittest.TestCase):
    def test_qb_and_pass_catcher_same_team_is_positive_stack(self):
        legs = [
            correlation.from_model_leg(_model_leg("Josh Allen", "BUF", "pass_yds", player_id=1)),
            correlation.from_model_leg(_model_leg("Khalil Shakir", "BUF", "rec_yds", player_id=2)),
        ]
        warnings = correlation.analyze_correlations(legs)
        self.assertTrue(any(w["type"] == "positive_stack" for w in warnings))

    def test_two_same_team_td_legs_is_competing_td(self):
        legs = [
            correlation.from_model_leg(_model_leg("James Cook", "BUF", "anytime_td", direction="Yes", player_id=1)),
            correlation.from_model_leg(_model_leg("Dalton Kincaid", "BUF", "anytime_td", direction="Yes", player_id=2)),
        ]
        warnings = correlation.analyze_correlations(legs)
        self.assertTrue(any(w["type"] == "competing_td" for w in warnings))

    def test_favored_moneyline_plus_own_rush_leg_is_positive(self):
        legs = [
            correlation.from_model_leg(_model_leg("James Cook", "BUF", "rush_yds", player_id=1)),
            correlation.from_pick({"id": 2, "team": "BUF", "market_type": "moneyline", "odds_american": -180}),
        ]
        warnings = correlation.analyze_correlations(legs)
        self.assertTrue(any(w["type"] == "favored_rush_stack" for w in warnings))

    def test_total_under_plus_passing_leg_is_conflict(self):
        legs = [
            correlation.from_model_leg(_model_leg("Josh Allen", "BUF", "pass_yds", player_id=1)),
            correlation.from_pick({"id": 2, "team": "BUF", "market_type": "total", "direction": "Under", "line_entered": 38.5}),
        ]
        warnings = correlation.analyze_correlations(legs)
        self.assertTrue(any(w["type"] == "total_conflict" for w in warnings))

    def test_three_legs_same_game_is_concentration(self):
        legs = [
            correlation.from_model_leg(_model_leg("Josh Allen", "BUF", "pass_yds", player_id=1)),
            correlation.from_model_leg(_model_leg("James Cook", "BUF", "rush_yds", player_id=2)),
            correlation.from_model_leg(_model_leg("Tua Tagovailoa", "MIA", "pass_yds", player_id=3)),
        ]
        for leg in legs:
            leg["opponent"] = "MIA" if leg["team"] == "BUF" else "BUF"
        warnings = correlation.analyze_correlations(legs)
        self.assertTrue(any(w["type"] == "game_concentration" for w in warnings))

    def test_unrelated_legs_produce_no_warnings(self):
        legs = [
            correlation.from_model_leg(_model_leg("Josh Allen", "BUF", "pass_yds", player_id=1)),
            correlation.from_model_leg(_model_leg("Justin Jefferson", "MIN", "rec_yds", player_id=2)),
        ]
        warnings = correlation.analyze_correlations(legs)
        self.assertEqual(warnings, [])


class TestGenerateGameScriptSummary(unittest.TestCase):
    def _bills_dolphins_legs(self):
        return [correlation.from_model_leg(_model_leg("Josh Allen", "BUF", "pass_yds", player_id=1))]

    def test_no_data_at_all_is_neutral(self):
        legs = self._bills_dolphins_legs()
        legs[0]["opponent"] = "MIA"
        summaries = correlation.generate_game_script_summary(legs)
        self.assertEqual(summaries[0]["basis"], "none")

    def test_roster_strength_fallback_when_only_team_strength_given(self):
        legs = self._bills_dolphins_legs()
        legs[0]["opponent"] = "MIA"
        summaries = correlation.generate_game_script_summary(legs, team_strength={"BUF": 200.0, "MIA": 150.0})
        self.assertEqual(summaries[0]["basis"], "roster_strength_speculative")
        self.assertIn("BUF", summaries[0]["summary"])

    def test_manual_pick_takes_priority_as_market_manual(self):
        legs = self._bills_dolphins_legs()
        legs[0]["opponent"] = "MIA"
        legs.append(correlation.from_pick({"id": 2, "team": "BUF", "opponent": "MIA", "market_type": "moneyline", "odds_american": -180}))
        market_odds = {frozenset(("BUF", "MIA")): {"favorite": "MIA", "total": {"point": 44.5}}}
        summaries = correlation.generate_game_script_summary(legs, market_odds_by_game=market_odds)
        self.assertEqual(summaries[0]["basis"], "market_manual")

    def test_auto_market_used_when_no_manual_pick(self):
        legs = self._bills_dolphins_legs()
        legs[0]["opponent"] = "MIA"
        market_odds = {frozenset(("BUF", "MIA")): {"favorite": "BUF", "total": {"point": 44.5}}}
        summaries = correlation.generate_game_script_summary(legs, market_odds_by_game=market_odds)
        self.assertEqual(summaries[0]["basis"], "market_auto")
        self.assertIn("BUF", summaries[0]["summary"])
        self.assertIn("44.5", summaries[0]["summary"])

    def test_manual_and_auto_agree_no_caveat(self):
        legs = self._bills_dolphins_legs()
        legs[0]["opponent"] = "MIA"
        legs.append(correlation.from_pick({"id": 2, "team": "BUF", "opponent": "MIA", "market_type": "moneyline", "odds_american": -180}))
        market_odds = {frozenset(("BUF", "MIA")): {"favorite": "BUF"}}
        summaries = correlation.generate_game_script_summary(legs, market_odds_by_game=market_odds)
        self.assertEqual(summaries[0]["basis"], "market_manual")
        self.assertNotIn("Note:", summaries[0]["summary"])

    def test_manual_and_auto_disagree_appends_caveat(self):
        legs = self._bills_dolphins_legs()
        legs[0]["opponent"] = "MIA"
        legs.append(correlation.from_pick({"id": 2, "team": "BUF", "opponent": "MIA", "market_type": "moneyline", "odds_american": -180}))
        market_odds = {frozenset(("BUF", "MIA")): {"favorite": "MIA"}}
        summaries = correlation.generate_game_script_summary(legs, market_odds_by_game=market_odds)
        self.assertEqual(summaries[0]["basis"], "market_manual")
        self.assertIn("Note:", summaries[0]["summary"])
        self.assertIn("MIA", summaries[0]["summary"])

    def test_auto_market_beats_roster_strength(self):
        legs = self._bills_dolphins_legs()
        legs[0]["opponent"] = "MIA"
        market_odds = {frozenset(("BUF", "MIA")): {"favorite": "MIA"}}
        summaries = correlation.generate_game_script_summary(
            legs, team_strength={"BUF": 200.0, "MIA": 150.0}, market_odds_by_game=market_odds,
        )
        self.assertEqual(summaries[0]["basis"], "market_auto")


if __name__ == "__main__":
    unittest.main()
