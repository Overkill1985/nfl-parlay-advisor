"""Unit tests for history.py's grading logic - this is what determines whether
model_snapshots calibration data and tracked-pick ROI numbers are trustworthy,
so it's worth covering thoroughly even though the app itself has no test
suite yet.

Run with: python -m unittest discover -s tests
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import history
import storage


class TestGradeOverUnder(unittest.TestCase):
    def test_over_hit(self):
        self.assertEqual(history._grade_over_under("Over", 50.0, 60.0), history.GRADE_HIT)

    def test_over_miss(self):
        self.assertEqual(history._grade_over_under("Over", 50.0, 40.0), history.GRADE_MISS)

    def test_under_hit(self):
        self.assertEqual(history._grade_over_under("Under", 50.0, 40.0), history.GRADE_HIT)

    def test_under_miss(self):
        self.assertEqual(history._grade_over_under("Under", 50.0, 60.0), history.GRADE_MISS)

    def test_push_regardless_of_direction(self):
        self.assertEqual(history._grade_over_under("Over", 50.0, 50.0), history.GRADE_PUSH)
        self.assertEqual(history._grade_over_under("Under", 50.0, 50.0), history.GRADE_PUSH)

    def test_none_line_or_actual_returns_none(self):
        self.assertIsNone(history._grade_over_under("Over", None, 60.0))
        self.assertIsNone(history._grade_over_under("Over", 50.0, None))


class TestGradeAnytime(unittest.TestCase):
    def test_none_is_none(self):
        self.assertIsNone(history._grade_anytime(None))

    def test_zero_is_miss(self):
        self.assertEqual(history._grade_anytime(0), history.GRADE_MISS)

    def test_positive_is_hit(self):
        self.assertEqual(history._grade_anytime(1), history.GRADE_HIT)
        self.assertEqual(history._grade_anytime(2), history.GRADE_HIT)


class TestActualTdCount(unittest.TestCase):
    def test_anytime_td_qb_uses_pass_td(self):
        stats = {"pass_td": 3, "rush_td": 1, "rec_td": 0}
        self.assertEqual(history._actual_td_count("anytime_td", "QB", stats), 3)

    def test_anytime_td_skill_position_sums_rush_and_rec(self):
        stats = {"pass_td": 0, "rush_td": 1, "rec_td": 2}
        self.assertEqual(history._actual_td_count("anytime_td", "RB", stats), 3)
        self.assertEqual(history._actual_td_count("anytime_td", "WR", stats), 3)
        self.assertEqual(history._actual_td_count("anytime_td", "TE", stats), 3)

    def test_non_td_stat_passes_through(self):
        stats = {"rush_yds": 87.0}
        self.assertEqual(history._actual_td_count("rush_yds", "RB", stats), 87.0)


class TestGradeMoneyline(unittest.TestCase):
    def test_win_is_hit(self):
        self.assertEqual(history._grade_moneyline(picked_won=True, tied=False), history.GRADE_HIT)

    def test_loss_is_miss(self):
        self.assertEqual(history._grade_moneyline(picked_won=False, tied=False), history.GRADE_MISS)

    def test_tie_is_push_regardless_of_picked_won(self):
        self.assertEqual(history._grade_moneyline(picked_won=True, tied=True), history.GRADE_PUSH)
        self.assertEqual(history._grade_moneyline(picked_won=False, tied=True), history.GRADE_PUSH)


def _game(team_score, opponent_score, winner, completed=True):
    return {"team_score": team_score, "opponent_score": opponent_score, "winner": winner, "completed": completed}


class TestGradeGameMarketPick(unittest.TestCase):
    def test_moneyline_win(self):
        pick = {"market_type": "moneyline", "team": "KC"}
        scores = {"KC": _game(27, 20, winner=True)}
        actual, grade = history.grade_game_market_pick(pick, scores)
        self.assertEqual(actual, 7)
        self.assertEqual(grade, history.GRADE_HIT)

    def test_moneyline_loss(self):
        pick = {"market_type": "moneyline", "team": "KC"}
        scores = {"KC": _game(20, 27, winner=False)}
        actual, grade = history.grade_game_market_pick(pick, scores)
        self.assertEqual(grade, history.GRADE_MISS)

    def test_moneyline_tie_is_push(self):
        pick = {"market_type": "moneyline", "team": "KC"}
        scores = {"KC": _game(20, 20, winner=False)}
        _actual, grade = history.grade_game_market_pick(pick, scores)
        self.assertEqual(grade, history.GRADE_PUSH)

    def test_spread_favorite_covers(self):
        # KC favored by 3.5 (line_entered=-3.5), wins by 7 -> covers.
        pick = {"market_type": "spread", "team": "KC", "line_entered": -3.5}
        scores = {"KC": _game(27, 20, winner=True)}
        actual, grade = history.grade_game_market_pick(pick, scores)
        self.assertEqual(actual, 7)
        self.assertEqual(grade, history.GRADE_HIT)

    def test_spread_favorite_fails_to_cover(self):
        # KC favored by 10.5, wins by only 7 -> does not cover.
        pick = {"market_type": "spread", "team": "KC", "line_entered": -10.5}
        scores = {"KC": _game(27, 20, winner=True)}
        _actual, grade = history.grade_game_market_pick(pick, scores)
        self.assertEqual(grade, history.GRADE_MISS)

    def test_spread_underdog_covers_on_loss_within_margin(self):
        # DEN getting +10.5, loses by 7 -> still covers.
        pick = {"market_type": "spread", "team": "DEN", "line_entered": 10.5}
        scores = {"DEN": _game(20, 27, winner=False)}
        _actual, grade = history.grade_game_market_pick(pick, scores)
        self.assertEqual(grade, history.GRADE_HIT)

    def test_spread_push(self):
        pick = {"market_type": "spread", "team": "KC", "line_entered": -7.0}
        scores = {"KC": _game(27, 20, winner=True)}
        _actual, grade = history.grade_game_market_pick(pick, scores)
        self.assertEqual(grade, history.GRADE_PUSH)

    def test_total_over_hits(self):
        pick = {"market_type": "total", "team": "KC", "direction": "Over", "line_entered": 44.5}
        scores = {"KC": _game(27, 20, winner=True)}
        actual, grade = history.grade_game_market_pick(pick, scores)
        self.assertEqual(actual, 47)
        self.assertEqual(grade, history.GRADE_HIT)

    def test_total_under_hits(self):
        pick = {"market_type": "total", "team": "KC", "direction": "Under", "line_entered": 50.5}
        scores = {"KC": _game(27, 20, winner=True)}
        _actual, grade = history.grade_game_market_pick(pick, scores)
        self.assertEqual(grade, history.GRADE_HIT)

    def test_game_not_completed_is_not_gradeable(self):
        pick = {"market_type": "moneyline", "team": "KC"}
        scores = {"KC": _game(27, 20, winner=True, completed=False)}
        actual, grade = history.grade_game_market_pick(pick, scores)
        self.assertEqual((actual, grade), (None, None))

    def test_team_not_in_scores_is_not_gradeable(self):
        pick = {"market_type": "moneyline", "team": "KC"}
        actual, grade = history.grade_game_market_pick(pick, {})
        self.assertEqual((actual, grade), (None, None))

    def test_player_prop_market_type_is_not_gradeable_here(self):
        pick = {"market_type": "player_prop", "team": "KC"}
        scores = {"KC": _game(27, 20, winner=True)}
        actual, grade = history.grade_game_market_pick(pick, scores)
        self.assertEqual((actual, grade), (None, None))


class TestGradePickAgainstActualGameMarkets(unittest.TestCase):
    """grade_pick_against_actual's dispatch to the moneyline/spread/total
    path - the pure-grading-logic details are covered above in
    TestGradeGameMarketPick, this just checks the routing/gating."""

    def test_game_market_without_scores_by_team_is_not_gradeable(self):
        pick = {"market_type": "moneyline", "team": "KC"}
        result = history.grade_pick_against_actual(pick, players_by_id={}, scores_by_team=None)
        self.assertEqual(result, (None, None))

    def test_game_market_with_scores_by_team_grades(self):
        pick = {"market_type": "moneyline", "team": "KC"}
        scores = {"KC": _game(27, 20, winner=True)}
        actual, grade = history.grade_pick_against_actual(pick, players_by_id={}, scores_by_team=scores)
        self.assertEqual(grade, history.GRADE_HIT)


def _player(player_id, position, weekly_actuals):
    return {"id": player_id, "position": position, "weekly_actuals": weekly_actuals}


class TestGradePickAgainstActual(unittest.TestCase):
    def setUp(self):
        self.players_by_id = {
            1: _player(1, "RB", {1: {"stats": {"rush_yds": 87.0, "rush_td": 1, "rec_td": 0}, "applied_total": 15.0}}),
            2: _player(2, "QB", {1: {"stats": {"pass_td": 2}, "applied_total": 20.0}}),
        }

    def test_non_player_prop_market_is_not_gradeable(self):
        pick = {"market_type": "moneyline", "player_id": 1, "stat": "rush_yds"}
        self.assertEqual(history.grade_pick_against_actual(pick, self.players_by_id), (None, None))

    def test_missing_player_id_or_stat_is_not_gradeable(self):
        pick = {"market_type": "player_prop", "player_id": None, "stat": "rush_yds"}
        self.assertEqual(history.grade_pick_against_actual(pick, self.players_by_id), (None, None))

    def test_unknown_player_is_not_gradeable(self):
        pick = {"market_type": "player_prop", "player_id": 999, "stat": "rush_yds", "week": 1}
        self.assertEqual(history.grade_pick_against_actual(pick, self.players_by_id), (None, None))

    def test_no_actual_for_week_is_not_gradeable(self):
        pick = {"market_type": "player_prop", "player_id": 1, "stat": "rush_yds", "week": 2}
        self.assertEqual(history.grade_pick_against_actual(pick, self.players_by_id), (None, None))

    def test_yardage_over_under_grades_correctly(self):
        pick = {
            "market_type": "player_prop", "player_id": 1, "stat": "rush_yds", "week": 1,
            "direction": "Over", "line_entered": 75.5,
        }
        actual_value, grade = history.grade_pick_against_actual(pick, self.players_by_id)
        self.assertEqual(actual_value, 87.0)
        self.assertEqual(grade, history.GRADE_HIT)

    def test_anytime_td_uses_position_specific_count(self):
        pick = {"market_type": "player_prop", "player_id": 2, "stat": "anytime_td", "week": 1}
        actual_value, grade = history.grade_pick_against_actual(pick, self.players_by_id)
        self.assertEqual(actual_value, 2)
        self.assertEqual(grade, history.GRADE_HIT)


class TestAutoGradeIntegration(unittest.TestCase):
    """Exercises auto_grade_pending_picks / auto_grade_pending_snapshots end to
    end against a throwaway sqlite db, so the storage<->history wiring itself
    is covered, not just the pure grading helpers above."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_db_dir = storage.DB_DIR
        self._orig_db_path = storage.DB_PATH
        storage.DB_DIR = self._tmpdir.name
        storage.DB_PATH = os.path.join(self._tmpdir.name, "test.db")
        storage.init_db()

        self.players = [
            {
                "id": 1, "position": "RB",
                "weekly_actuals": {1: {"stats": {"rush_yds": 87.0, "rush_td": 1, "rec_td": 0}, "applied_total": 15.0}},
            },
        ]

    def tearDown(self):
        storage.DB_DIR = self._orig_db_dir
        storage.DB_PATH = self._orig_db_path
        self._tmpdir.cleanup()

    def test_auto_grade_pending_picks_updates_status_and_actual_value(self):
        pick_id = storage.create_pick(
            status="placed", market_type="player_prop", player_id=1, week=1,
            stat="rush_yds", direction="Over", line_entered=75.5,
        )

        graded = history.auto_grade_pending_picks(self.players)

        self.assertEqual(graded, 1)
        pick = storage.get_pick(pick_id)
        self.assertEqual(pick["status"], "graded_win")
        self.assertEqual(pick["actual_value"], 87.0)
        self.assertIsNotNone(pick["graded_at"])

    def test_auto_grade_pending_picks_is_idempotent(self):
        storage.create_pick(
            status="placed", market_type="player_prop", player_id=1, week=1,
            stat="rush_yds", direction="Over", line_entered=75.5,
        )
        history.auto_grade_pending_picks(self.players)
        # Second pass: the pick is now graded_win, not "placed", so it should
        # no longer be picked up.
        graded_again = history.auto_grade_pending_picks(self.players)
        self.assertEqual(graded_again, 0)

    def test_auto_grade_pending_picks_leaves_ungradeable_picks_placed(self):
        # No actual data for week 2 yet - should stay "placed", not be
        # silently marked as some grade.
        pick_id = storage.create_pick(
            status="placed", market_type="player_prop", player_id=1, week=2,
            stat="rush_yds", direction="Over", line_entered=75.5,
        )
        graded = history.auto_grade_pending_picks(self.players)
        self.assertEqual(graded, 0)
        self.assertEqual(storage.get_pick(pick_id)["status"], "placed")

    def test_auto_grade_pending_snapshots_grades_and_is_idempotent(self):
        storage.upsert_model_snapshot(
            season=2026, week=1, player_id=1, player_name="Test Back", team="KC",
            position="RB", stat="rush_yds", label="Rushing Yards", direction="Over",
            line=75.5, model_probability=0.7, projected_avg=80.0,
            source="weekly_projection", risk_tier="balanced",
        )

        graded = history.auto_grade_pending_snapshots(self.players, season=2026, week=1)
        self.assertEqual(graded, 1)
        self.assertEqual(storage.list_ungraded_snapshots(2026, 1), [])

        graded_again = history.auto_grade_pending_snapshots(self.players, season=2026, week=1)
        self.assertEqual(graded_again, 0)


if __name__ == "__main__":
    unittest.main()
