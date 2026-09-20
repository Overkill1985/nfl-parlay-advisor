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


import odds_client
import odds_math

NOW = 1_800_000_000
FUTURE = NOW + 3600
PAST = NOW - 3600


def make_player(pid, name, team, position, games=1, **per_game):
    """A player with the given per-game stat line (season totals are derived,
    since get_week_stats divides them back down by `games`)."""
    stats = dict(pass_att=0, pass_cmp=0, pass_yds=0, pass_td=0, pass_int=0, rush_att=0,
                 rush_yds=0, rush_td=0, rec_yds=0, rec_td=0, receptions=0)
    stats.update({k: v * games for k, v in per_game.items()})
    return {
        "id": pid, "name": name, "team": team, "position": position, "games": games,
        "stats_season": stats, "projected_points_per_game": 12.0, "weekly_projections": {},
    }


def prop_row(market, name, point, price, event_id="e1"):
    return {"market": market, "name": name, "norm": odds_client.normalize_player_name(name),
            "point": point, "price": price, "event_id": event_id}


EVENTS = {"e1": {"home": "KC", "away": "DEN", "commence_time": "x", "start_ts": FUTURE}}


class TestMatchPropRows(unittest.TestCase):
    def test_matches_on_exact_normalized_name_within_the_game(self):
        players = [make_player(1, "Kansas QB", "KC", "QB", pass_yds=250)]
        rows = [prop_row("player_pass_yds", "Kansas QB", 220.5, -113)]
        matched, stats = parlay_engine.match_prop_rows(rows, EVENTS, players)
        self.assertEqual([p["id"] for _, p, _ in matched], [1])
        self.assertEqual(stats, {"unmatched": 0, "ambiguous": 0})

    def test_a_same_named_player_on_another_team_is_not_matched(self):
        players = [make_player(1, "Sam Smith", "BUF", "WR", receptions=5)]
        rows = [prop_row("player_receptions", "Sam Smith", 4.5, -120)]
        matched, stats = parlay_engine.match_prop_rows(rows, EVENTS, players)
        self.assertEqual(matched, [])
        self.assertEqual(stats["unmatched"], 1)

    def test_generational_suffix_difference_still_matches(self):
        players = [make_player(1, "Kenneth Walker", "KC", "RB", rush_yds=70)]
        rows = [prop_row("player_rush_yds", "Kenneth Walker III", 60.5, -115)]
        matched, _ = parlay_engine.match_prop_rows(rows, EVENTS, players)
        self.assertEqual(len(matched), 1)

    def test_two_players_with_the_same_name_on_a_team_is_dropped_not_guessed(self):
        players = [make_player(1, "Sam Smith", "KC", "WR", receptions=5),
                   make_player(2, "Sam Smith", "KC", "TE", receptions=3)]
        rows = [prop_row("player_receptions", "Sam Smith", 3.5, -120)]
        matched, stats = parlay_engine.match_prop_rows(rows, EVENTS, players)
        self.assertEqual(matched, [])
        self.assertEqual(stats["ambiguous"], 1)

    def test_game_must_be_the_one_the_schedule_has_that_team_playing_this_week(self):
        # The prop is from KC-vs-DEN, but this week's schedule has KC on LV.
        players = [make_player(1, "Kansas QB", "KC", "QB", pass_yds=250)]
        rows = [prop_row("player_pass_yds", "Kansas QB", 220.5, -113)]
        matched, stats = parlay_engine.match_prop_rows(rows, EVENTS, players, matchups={"KC": "LV", "LV": "KC"})
        self.assertEqual(matched, [])
        self.assertEqual(stats["unmatched"], 1)

    def test_row_for_an_unknown_game_is_unmatched(self):
        players = [make_player(1, "Kansas QB", "KC", "QB", pass_yds=250)]
        rows = [prop_row("player_pass_yds", "Kansas QB", 220.5, -113, event_id="nope")]
        _, stats = parlay_engine.match_prop_rows(rows, EVENTS, players)
        self.assertEqual(stats["unmatched"], 1)


class TestBuildBookLegs(unittest.TestCase):
    def setUp(self):
        self.qb = make_player(1, "Kansas QB", "KC", "QB", pass_yds=250, rush_td=0)
        self.rows = [prop_row("player_pass_yds", "Kansas QB", 220.5, -113)]

    def _legs(self, players=None, rows=None, events=None, **kw):
        legs, stats = parlay_engine.build_book_legs(
            players or [self.qb], 1, rows or self.rows, events or EVENTS, now_ts=NOW, **kw)
        return legs, stats

    def test_uses_the_books_line_and_price_not_the_models(self):
        legs, _ = self._legs()
        self.assertEqual(len(legs), 1)
        leg = legs[0]
        self.assertEqual((leg["line"], leg["book_price"]), (220.5, -113))
        self.assertEqual(leg["direction"], "Over")

    def test_probability_is_the_models_chance_of_clearing_the_books_line(self):
        leg = self._legs()[0][0]
        expected = parlay_engine._prob_over(250, 0.28 * 250, 220.5)
        self.assertAlmostEqual(leg["probability"], expected, places=3)

    def test_edge_is_model_probability_minus_the_prices_implied_probability(self):
        leg = self._legs()[0][0]
        model = parlay_engine._prob_over(250, 0.28 * 250, 220.5)
        implied = odds_math.implied_probability(-113)
        self.assertAlmostEqual(leg["implied_probability"], implied, places=3)
        self.assertAlmostEqual(leg["edge"], model - implied, places=3)
        self.assertAlmostEqual(leg["book_decimal"], odds_math.american_to_decimal(-113), places=3)

    def test_a_line_the_model_thinks_is_unlikely_to_clear_is_left_out(self):
        rows = [prop_row("player_pass_yds", "Kansas QB", 320.5, -110)]  # mean is 250
        self.assertEqual(self._legs(rows=rows)[0], [])

    def test_game_that_has_already_kicked_off_is_excluded(self):
        started = {"e1": {**EVENTS["e1"], "start_ts": PAST}}
        self.assertEqual(self._legs(events=started)[0], [])

    def test_completed_game_is_excluded(self):
        scoreboard = {"available": True, "all_completed": False,
                      "teams": {"KC": {"completed": True}, "DEN": {"completed": True}}}
        self.assertEqual(self._legs(scoreboard=scoreboard)[0], [])

    def test_team_on_bye_this_week_is_excluded(self):
        schedule = {"weeks": {"1": {"GB": {"opponent": "ATL"}, "ATL": {"opponent": "GB"}}}}
        self.assertEqual(self._legs(schedule=schedule)[0], [])

    def test_position_filter_applies(self):
        self.assertEqual(self._legs(position_filter="RB")[0], [])
        self.assertEqual(len(self._legs(position_filter="QB")[0]), 1)

    def test_anytime_td_prices_rushing_and_receiving_tds_not_passing_tds(self):
        # The book's market is "scores a touchdown". The model-only QB leg
        # uses pass TDs; pricing a QB's anytime-TD off those would be wrong.
        qb = make_player(1, "Kansas QB", "KC", "QB", pass_yds=250, pass_td=3.0, rush_td=0.9)
        rows = [prop_row("player_anytime_td", "Kansas QB", None, 150)]
        leg = self._legs(players=[qb], rows=rows)[0][0]
        self.assertEqual(leg["stat"], "anytime_td")
        self.assertEqual(leg["projected_avg"], 0.9)
        self.assertIsNone(leg["line"])
        self.assertEqual(leg["direction"], "Yes")

    def test_a_player_with_no_touchdown_rate_gets_no_anytime_td_leg(self):
        qb = make_player(1, "Kansas QB", "KC", "QB", pass_yds=250, pass_td=3.0, rush_td=0.0)
        rows = [prop_row("player_anytime_td", "Kansas QB", None, 150)]
        self.assertEqual(self._legs(players=[qb], rows=rows)[0], [])

    def test_reports_how_many_props_could_not_be_matched(self):
        rows = self.rows + [prop_row("player_pass_yds", "Some Backup", 180.5, -110)]
        _, stats = self._legs(rows=rows)
        self.assertEqual(stats["unmatched"], 1)


def make_leg(pid, team, stat, probability, price):
    return {
        "player_id": pid, "player": f"P{pid}", "team": team, "position": "WR", "stat": stat,
        "label": stat, "unit": "", "line": 50.5, "direction": "Over", "projected_avg": 60.0,
        "probability": probability, "source": "season_pace_estimate", "week": 1,
        "book_price": price, "book_decimal": odds_math.american_to_decimal(price),
        "implied_probability": odds_math.implied_probability(price),
        "edge": 0.0,
    }


class TestBuildParlaysBookMode(unittest.TestCase):
    def test_book_payout_is_every_legs_decimal_odds_multiplied(self):
        legs = [make_leg(1, "BUF", "pass_yds", 0.7, -110),
                make_leg(2, "GB", "rush_yds", 0.7, -110),
                make_leg(3, "SEA", "rec_yds", 0.7, 150)]
        parlay = parlay_engine.build_parlays(legs, num_legs=3)[0]
        expected = round(odds_math.combined_decimal_odds([-110, -110, 150]), 2)
        self.assertEqual(parlay["book_decimal_odds"], expected)
        self.assertEqual(parlay["book_american_odds"], odds_math.decimal_to_american(expected))

    def test_the_models_own_fair_price_is_still_reported_for_comparison(self):
        legs = [make_leg(1, "BUF", "pass_yds", 0.7, -110), make_leg(2, "GB", "rush_yds", 0.7, -110)]
        parlay = parlay_engine.build_parlays(legs, num_legs=2)[0]
        self.assertEqual(parlay["estimated_decimal_odds"], round(1 / 0.49, 2))

    def test_sorting_by_payout_and_by_probability_can_pick_different_parlays(self):
        legs = [make_leg(1, "BUF", "pass_yds", 0.90, -300),
                make_leg(2, "GB", "rush_yds", 0.85, -250),
                make_leg(3, "SEA", "rec_yds", 0.80, -200),
                make_leg(4, "MIA", "receptions", 0.55, 200)]
        by_prob = parlay_engine.build_parlays(legs, num_legs=3, sort_by="probability")[0]
        by_payout = parlay_engine.build_parlays(legs, num_legs=3, sort_by="payout")[0]
        self.assertEqual({l["player_id"] for l in by_prob["legs"]}, {1, 2, 3})
        self.assertIn(4, {l["player_id"] for l in by_payout["legs"]})
        self.assertGreater(by_payout["book_decimal_odds"], by_prob["book_decimal_odds"])

    def test_market_sort_trusts_the_books_prices_over_the_models_probabilities(self):
        # Leg 4 is the model's favourite (90%) but the book prices it as a
        # long shot (+400 -> 20%). Ranked by the model it leads; ranked by the
        # market it must not.
        legs = [make_leg(1, "BUF", "pass_yds", 0.60, -200),
                make_leg(2, "GB", "rush_yds", 0.60, -200),
                make_leg(3, "SEA", "rec_yds", 0.60, -200),
                make_leg(4, "MIA", "receptions", 0.90, 400)]
        by_model = parlay_engine.build_parlays(legs, num_legs=3, sort_by="probability")[0]
        by_market = parlay_engine.build_parlays(legs, num_legs=3, sort_by="market")[0]
        self.assertIn(4, {l["player_id"] for l in by_model["legs"]})
        self.assertNotIn(4, {l["player_id"] for l in by_market["legs"]})

    def test_book_implied_probability_is_the_product_of_the_legs_implied_probabilities(self):
        legs = [make_leg(1, "BUF", "pass_yds", 0.7, -200), make_leg(2, "GB", "rush_yds", 0.7, 100)]
        parlay = parlay_engine.build_parlays(legs, num_legs=2)[0]
        expected = odds_math.implied_probability(-200) * odds_math.implied_probability(100)
        self.assertAlmostEqual(parlay["book_implied_probability"], expected, places=3)

    def test_market_sort_without_book_prices_falls_back_to_the_models_probability(self):
        players = [make_qb(1, "Kansas QB", "KC", pass_yds_season=250), make_qb(2, "Denver QB", "DEN", pass_yds_season=180)]
        legs = parlay_engine.build_legs(players, week=1)
        parlays = parlay_engine.build_parlays(legs, num_legs=2, sort_by="market", require_distinct_categories=False)
        probs = [p["combined_probability"] for p in parlays]
        self.assertEqual(probs, sorted(probs, reverse=True))

    def test_same_game_legs_are_flagged_because_books_reprice_them(self):
        schedule = {"weeks": {"1": {"KC": {"opponent": "DEN", "is_home": True},
                                    "DEN": {"opponent": "KC", "is_home": False}}}}
        same_game = [make_leg(1, "KC", "pass_yds", 0.7, -110),
                     make_leg(2, "DEN", "rush_yds", 0.7, -110),
                     make_leg(3, "BUF", "rec_yds", 0.7, -110)]
        self.assertTrue(parlay_engine.build_parlays(same_game, num_legs=3, schedule=schedule)[0]["same_game"])

        different_games = [make_leg(1, "KC", "pass_yds", 0.7, -110),
                           make_leg(2, "GB", "rush_yds", 0.7, -110),
                           make_leg(3, "BUF", "rec_yds", 0.7, -110)]
        self.assertFalse(parlay_engine.build_parlays(different_games, num_legs=3, schedule=schedule)[0]["same_game"])

    def test_model_only_legs_get_no_sportsbook_fields(self):
        players = [make_qb(1, "Kansas QB", "KC"), make_qb(2, "Denver QB", "DEN")]
        legs = parlay_engine.build_legs(players, week=1)
        parlay = parlay_engine.build_parlays(legs, num_legs=2, require_distinct_categories=False)[0]
        self.assertNotIn("book_decimal_odds", parlay)
        self.assertNotIn("same_game", parlay)

    def test_payout_sort_without_book_prices_falls_back_to_the_models_fair_odds(self):
        legs = [{**make_leg(1, "BUF", "pass_yds", 0.9, -110)}, {**make_leg(2, "GB", "rush_yds", 0.6, -110)},
                {**make_leg(3, "SEA", "rec_yds", 0.5, -110)}]
        for leg in legs:
            del leg["book_price"]
        parlays = parlay_engine.build_parlays(legs, num_legs=2, sort_by="payout")
        payouts = [p["estimated_decimal_odds"] for p in parlays]
        self.assertEqual(payouts, sorted(payouts, reverse=True))


if __name__ == "__main__":
    unittest.main()
