"""Tests for parlay_engine.build_legs, focused on the schedule/scoreboard
filtering added to stop parlays including games that have already finished
or a team's bye week. Fixture-driven, no network - matches the rest of this
suite's style.
"""
import unittest

import parlay_engine


def make_qb(player_id, name, team, games=1, pass_yds_season=250):
    """A minimal QB whose season-pace stats clear build_legs' thresholds for
    both a yardage prop (pass_yds) and the anytime-TD prop, so a fixture
    reliably produces at least one leg unless filtered out."""
    return {
        "id": player_id, "name": name, "team": team, "position": "QB",
        "games": games,
        "stats_season": {
            "pass_att": 30 * games, "pass_cmp": 20 * games,
            "pass_yds": pass_yds_season * games, "pass_td": 2 * games,
            "pass_int": 1 * games, "rush_att": 3 * games, "rush_yds": 10 * games,
            "rush_td": 0, "rec_yds": 0, "rec_td": 0, "receptions": 0,
        },
        "projected_points_per_game": 18.0,
        "weekly_projections": {},
    }


SCHEDULE = {
    "weeks": {
        "1": {
            "KC": {"opponent": "DEN", "is_home": True, "game_date": "2026-09-15T00:20:00Z", "game_id": 1},
            "DEN": {"opponent": "KC", "is_home": False, "game_date": "2026-09-15T00:20:00Z", "game_id": 1},
            # BUF has no entry for week 1 in this fixture - simulates a bye.
        }
    },
    "bye_weeks": {"BUF": 1},
}

SCOREBOARD_IN_PROGRESS = {
    "available": True,
    "all_completed": False,
    "teams": {
        "KC": {"opponent": "DEN", "team_score": 0, "opponent_score": 0, "winner": False, "completed": False},
        "DEN": {"opponent": "KC", "team_score": 0, "opponent_score": 0, "winner": False, "completed": False},
    },
}

SCOREBOARD_KC_DONE = {
    "available": True,
    "all_completed": False,
    "teams": {
        "KC": {"opponent": "DEN", "team_score": 24, "opponent_score": 20, "winner": True, "completed": True},
        "DEN": {"opponent": "KC", "team_score": 20, "opponent_score": 24, "winner": False, "completed": False},
    },
}

SCOREBOARD_UNAVAILABLE = {"available": False, "reason": "fetch_error: timeout"}


class TestBuildLegsGameFiltering(unittest.TestCase):
    def test_no_schedule_or_scoreboard_keeps_prior_behavior(self):
        players = [make_qb(1, "Kansas QB", "KC")]
        legs = parlay_engine.build_legs(players, week=1)
        self.assertTrue(legs, "a QB with no filters applied should still produce legs")

    def test_completed_game_is_excluded(self):
        players = [make_qb(1, "Kansas QB", "KC"), make_qb(2, "Denver QB", "DEN")]
        legs = parlay_engine.build_legs(players, week=1, schedule=SCHEDULE, scoreboard=SCOREBOARD_KC_DONE)
        teams_present = {l["team"] for l in legs}
        self.assertNotIn("KC", teams_present)
        self.assertIn("DEN", teams_present)

    def test_in_progress_game_is_not_excluded(self):
        # completed=False means the game hasn't finished (whether it's not
        # yet started or currently live) - only a finished game is filtered.
        players = [make_qb(1, "Kansas QB", "KC"), make_qb(2, "Denver QB", "DEN")]
        legs = parlay_engine.build_legs(players, week=1, schedule=SCHEDULE, scoreboard=SCOREBOARD_IN_PROGRESS)
        teams_present = {l["team"] for l in legs}
        self.assertEqual(teams_present, {"KC", "DEN"})

    def test_bye_week_team_is_excluded(self):
        players = [make_qb(1, "Bills QB", "BUF"), make_qb(2, "Kansas QB", "KC")]
        legs = parlay_engine.build_legs(players, week=1, schedule=SCHEDULE, scoreboard=SCOREBOARD_IN_PROGRESS)
        teams_present = {l["team"] for l in legs}
        self.assertNotIn("BUF", teams_present)
        self.assertIn("KC", teams_present)

    def test_scoreboard_fetch_failure_does_not_hide_every_leg(self):
        # A transient ESPN scoreboard hiccup must fail open on completion
        # filtering, not silently empty the entire parlay pool.
        players = [make_qb(1, "Kansas QB", "KC")]
        legs = parlay_engine.build_legs(players, week=1, schedule=SCHEDULE, scoreboard=SCOREBOARD_UNAVAILABLE)
        self.assertTrue(legs)

    def test_schedule_without_scoreboard_still_filters_byes(self):
        players = [make_qb(1, "Bills QB", "BUF"), make_qb(2, "Kansas QB", "KC")]
        legs = parlay_engine.build_legs(players, week=1, schedule=SCHEDULE)
        teams_present = {l["team"] for l in legs}
        self.assertNotIn("BUF", teams_present)
        self.assertIn("KC", teams_present)

    def test_scoreboard_without_schedule_still_filters_completed(self):
        players = [make_qb(1, "Kansas QB", "KC"), make_qb(2, "Denver QB", "DEN")]
        legs = parlay_engine.build_legs(players, week=1, scoreboard=SCOREBOARD_KC_DONE)
        teams_present = {l["team"] for l in legs}
        self.assertNotIn("KC", teams_present)
        self.assertIn("DEN", teams_present)

    def test_all_games_completed_yields_empty_pool(self):
        players = [make_qb(1, "Kansas QB", "KC"), make_qb(2, "Denver QB", "DEN")]
        all_done = {
            "available": True, "all_completed": True,
            "teams": {
                "KC": {**SCOREBOARD_KC_DONE["teams"]["KC"], "completed": True},
                "DEN": {**SCOREBOARD_KC_DONE["teams"]["DEN"], "completed": True},
            },
        }
        legs = parlay_engine.build_legs(players, week=1, schedule=SCHEDULE, scoreboard=all_done)
        self.assertEqual(legs, [])


if __name__ == "__main__":
    unittest.main()
