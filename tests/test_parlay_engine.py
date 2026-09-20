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


class TestInjuryFilter(unittest.TestCase):
    def setUp(self):
        self.players = [make_qb(1, "Kansas QB", "KC"), make_qb(2, "Denver QB", "DEN")]

    def _teams(self, legs):
        return {l["team"] for l in legs}

    def test_players_ruled_out_are_dropped_entirely(self):
        for status in ("OUT", "IR", "SUSPENDED"):
            with self.subTest(status=status):
                legs = parlay_engine.build_legs(self.players, 1, injuries={1: {"status": status, "note": None}})
                self.assertEqual(self._teams(legs), {"DEN"})

    def test_a_ruled_out_player_loses_every_kind_of_leg(self):
        # Yardage legs AND the touchdown leg - not just whichever came first.
        legs = parlay_engine.build_legs(self.players, 1, injuries={1: {"status": "OUT", "note": None}})
        self.assertFalse([l for l in legs if l["player_id"] == 1])
        healthy = parlay_engine.build_legs(self.players, 1)
        self.assertGreater(len([l for l in healthy if l["player_id"] == 1]), 1)

    def test_doubtful_and_questionable_players_stay_but_are_flagged(self):
        for status in ("DOUBTFUL", "QUESTIONABLE"):
            with self.subTest(status=status):
                legs = parlay_engine.build_legs(
                    self.players, 1, injuries={1: {"status": status, "note": "back tightness"}})
                kansas = [l for l in legs if l["player_id"] == 1]
                self.assertTrue(kansas)
                self.assertTrue(all(l["injury_status"] == status for l in kansas))
                self.assertTrue(all(l["injury_note"] == "back tightness" for l in kansas))
                self.assertGreater(len({l["stat"] for l in kansas}), 1)  # flagged on every leg kind

    def test_healthy_players_carry_no_injury_fields(self):
        legs = parlay_engine.build_legs(self.players, 1, injuries={1: {"status": "OUT", "note": None}})
        self.assertTrue(all("injury_status" not in l and "injury_note" not in l for l in legs))

    def test_no_injuries_argument_keeps_prior_behavior(self):
        self.assertEqual(self._teams(parlay_engine.build_legs(self.players, 1)), {"KC", "DEN"})
        self.assertEqual(self._teams(parlay_engine.build_legs(self.players, 1, injuries={})), {"KC", "DEN"})

    def test_an_injury_entry_for_a_player_not_in_the_pool_is_harmless(self):
        legs = parlay_engine.build_legs(self.players, 1, injuries={999: {"status": "OUT", "note": None}})
        self.assertEqual(self._teams(legs), {"KC", "DEN"})

    def test_sportsbook_legs_get_the_same_filter_and_flag(self):
        events = {"e1": {"home": "KC", "away": "DEN", "commence_time": "x", "start_ts": FUTURE}}
        rows = [prop_row("player_pass_yds", "Kansas QB", 220.5, -113),
                prop_row("player_pass_yds", "Denver QB", 220.5, -113)]

        def legs(injuries):
            return parlay_engine.build_book_legs(self.players, 1, rows, events, now_ts=NOW, injuries=injuries)[0]

        self.assertEqual(self._teams(legs({1: {"status": "OUT", "note": None}})), {"DEN"})
        flagged = legs({1: {"status": "QUESTIONABLE", "note": "hamstring"}})
        self.assertEqual({l["team"] for l in flagged}, {"KC", "DEN"})
        kansas = next(l for l in flagged if l["team"] == "KC")
        self.assertEqual((kansas["injury_status"], kansas["injury_note"]), ("QUESTIONABLE", "hamstring"))
        self.assertNotIn("injury_status", next(l for l in flagged if l["team"] == "DEN"))


STATS = ["pass_yds", "rush_yds", "rec_yds", "receptions"]


def spread_fixture(num_games=6):
    """`num_games` games (teams T0/T1, T2/T3, ...), one leg per team with
    probabilities falling team by team - so the strongest legs, and therefore
    the plain top N, all sit in the first game or two."""
    teams = [f"T{i}" for i in range(num_games * 2)]
    week = {}
    for i in range(0, len(teams), 2):
        a, b = teams[i], teams[i + 1]
        week[a] = {"opponent": b, "is_home": True}
        week[b] = {"opponent": a, "is_home": False}
    legs = [make_leg(i, t, STATS[i % 4], 0.95 - 0.03 * i, -110) for i, t in enumerate(teams)]
    return legs, {"weeks": {"1": week}}


def games_used(parlays, schedule):
    from collections import Counter
    return Counter(g for p in parlays for g in {parlay_engine.leg_game_key(l, schedule) for l in p["legs"]})


def legs_used(parlays):
    from collections import Counter
    return Counter((l["player_id"], l["stat"]) for p in parlays for l in p["legs"])


class TestLegGameKey(unittest.TestCase):
    SCHEDULE = {"weeks": {"1": {"KC": {"opponent": "DEN"}, "DEN": {"opponent": "KC"}}}}

    def test_both_sides_of_a_game_share_a_key(self):
        kc = parlay_engine.leg_game_key({"team": "KC", "week": 1}, self.SCHEDULE)
        den = parlay_engine.leg_game_key({"team": "DEN", "week": 1}, self.SCHEDULE)
        self.assertEqual(kc, den)
        self.assertEqual(kc, frozenset({"KC", "DEN"}))

    def test_falls_back_to_the_team_when_the_schedule_lacks_the_game(self):
        self.assertEqual(parlay_engine.leg_game_key({"team": "BUF", "week": 1}, self.SCHEDULE), frozenset({"BUF"}))
        self.assertEqual(parlay_engine.leg_game_key({"team": "KC", "week": 9}, self.SCHEDULE), frozenset({"KC"}))
        self.assertEqual(parlay_engine.leg_game_key({"team": "KC", "week": 1}, None), frozenset({"KC"}))


class TestSpreadGames(unittest.TestCase):
    def setUp(self):
        self.legs, self.schedule = spread_fixture(6)

    def _parlays(self, **kw):
        return parlay_engine.build_parlays(self.legs, num_legs=2, schedule=self.schedule, **kw)

    def test_the_plain_top_n_really_is_clustered_and_spreading_fixes_it(self):
        plain = self._parlays(spread_games=False)
        spread = self._parlays()
        self.assertEqual(len(plain), 10)
        self.assertEqual(len(spread), 10)
        self.assertGreater(len(games_used(spread, self.schedule)), len(games_used(plain, self.schedule)))
        self.assertLess(max(legs_used(spread).values()), max(legs_used(plain).values()))
        self.assertLess(max(games_used(spread, self.schedule).values()),
                        max(games_used(plain, self.schedule).values()))

    def test_the_single_best_parlay_is_still_first(self):
        self.assertEqual(self._parlays()[0]["legs"], self._parlays(spread_games=False)[0]["legs"])

    def test_results_stay_ordered_by_the_chosen_sort(self):
        probs = [p["combined_probability"] for p in self._parlays()]
        self.assertEqual(probs, sorted(probs, reverse=True))
        payouts = [p["book_decimal_odds"] for p in self._parlays(sort_by="payout")]
        self.assertEqual(payouts, sorted(payouts, reverse=True))

    def test_spreading_off_gives_the_plain_top_n(self):
        plain = self._parlays(spread_games=False)
        probs = [p["combined_probability"] for p in plain]
        self.assertEqual(probs, sorted(probs, reverse=True))

    def test_no_parlay_is_invented_or_duplicated(self):
        spread = self._parlays()
        keys = [tuple(sorted((l["player_id"], l["stat"]) for l in p["legs"])) for p in spread]
        self.assertEqual(len(keys), len(set(keys)))

    def test_a_pool_with_only_two_games_still_fills_the_list_without_overusing_a_leg(self):
        # Sportsbook mode can have props for just a couple of games, so games
        # must be reused heavily - that must NOT drag single legs along with it.
        # The shape here is the real pool that exposed this: two games, and the
        # scarce prop types (1 anytime-TD leg, 2 passing legs) are also the
        # strongest, so the top-ranked parlays all lean on them. A first version
        # of the rule reused one leg in all 10 parlays here (8 distinct legs used
        # of 24) even though 16 could be shared.
        teams = ["A", "B", "C", "D"]  # games A/B and C/D
        schedule = {"weeks": {"1": {tm: {"opponent": teams[i ^ 1]} for i, tm in enumerate(teams)}}}
        spec = ([("pass_yds", 0.90), ("pass_yds", 0.89), ("anytime_td", 0.88)]
                + [(stat, 0.80 - 0.01 * i) for i, stat in enumerate(["rec_yds", "receptions"] * 8)]
                + [("rush_yds", 0.60 - 0.01 * i) for i in range(5)])
        legs = [make_leg(100 + i, teams[i % 4], stat, prob, -110) for i, (stat, prob) in enumerate(spec)]
        self.assertEqual(len({parlay_engine.leg_game_key(l, schedule) for l in legs}), 2)

        parlays = parlay_engine.build_parlays(legs, num_legs=3, schedule=schedule)
        self.assertEqual(len(parlays), 10)
        self.assertEqual(len(games_used(parlays, schedule)), 2)
        # A parlay with legs in both games uses EACH game once, so a game can
        # legitimately be in nearly every parlay here.
        self.assertLessEqual(max(legs_used(parlays).values()), 3)
        self.assertGreaterEqual(len(legs_used(parlays)), 14)

    def test_fewer_candidates_than_top_n_are_all_returned(self):
        legs, schedule = spread_fixture(2)  # 4 legs -> a handful of 2-leg combos
        parlays = parlay_engine.build_parlays(legs, num_legs=2, schedule=schedule)
        self.assertLess(len(parlays), 10)
        self.assertEqual(len(parlays), len(parlay_engine.build_parlays(legs, num_legs=2, schedule=schedule,
                                                                        spread_games=False)))

    def test_works_without_a_schedule_by_spreading_across_teams(self):
        legs, _ = spread_fixture(6)
        spread = parlay_engine.build_parlays(legs, num_legs=2)
        plain = parlay_engine.build_parlays(legs, num_legs=2, spread_games=False)
        self.assertEqual(len(spread), 10)
        self.assertLess(max(legs_used(spread).values()), max(legs_used(plain).values()))

    def test_a_game_counts_once_per_parlay_however_many_legs_it_has(self):
        # Both legs of this parlay are in the same game; that's one use of it.
        schedule = {"weeks": {"1": {"A": {"opponent": "B"}, "B": {"opponent": "A"}}}}
        legs = [make_leg(1, "A", "pass_yds", 0.9, -110), make_leg(2, "B", "rush_yds", 0.9, -110)]
        parlays = parlay_engine.build_parlays(legs, num_legs=2, schedule=schedule)
        self.assertEqual(sum(games_used(parlays, schedule).values()), len(parlays))


if __name__ == "__main__":
    unittest.main()
