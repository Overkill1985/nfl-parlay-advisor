"""Tests for building a parlay by hand: the prop options offered per player,
scoring a leg at a user-chosen line, the shared score_parlay, and the
server's request validation. The engine parts are pure; the server helper is
tested with a synthetic context (no network, no credits)."""
import unittest
from unittest import mock

import odds_math
import parlay_engine
import server
from tests.test_parlay_engine import make_player, make_qb, prop_row


def qb(pid=1, team="KC", pass_yds=250, pass_td=1.6, rush_td=0.3):
    return make_player(pid, f"QB{pid}", team, "QB", pass_yds=pass_yds, pass_td=pass_td, rush_td=rush_td)


def wr(pid=2, team="DEN", rec_yds=70, receptions=5, rec_td=0.4):
    return make_player(pid, f"WR{pid}", team, "WR", rec_yds=rec_yds, receptions=receptions, rec_td=rec_td)


class TestPropOptions(unittest.TestCase):
    def test_qb_gets_passing_and_td_pass_not_receiving(self):
        options, source = parlay_engine.prop_options(qb(), 1)
        stats = [o["stat"] for o in options]
        self.assertIn("pass_yds", stats)
        self.assertIn("anytime_td", stats)
        self.assertNotIn("rec_yds", stats)
        self.assertEqual(next(o for o in options if o["stat"] == "anytime_td")["label"], "Throws a TD Pass")
        self.assertEqual(source, "season_pace_estimate")

    def test_receiver_gets_receiving_props_and_anytime_td(self):
        stats = {o["stat"] for o in parlay_engine.prop_options(wr(), 1)[0]}
        self.assertEqual(stats, {"rec_yds", "receptions", "anytime_td"})

    def test_a_stat_under_the_minimum_is_not_offered(self):
        # rush_yds applies to a QB but 10 yds/game is below the 25 minimum.
        stats = {o["stat"] for o in parlay_engine.prop_options(qb(), 1)[0]}
        self.assertNotIn("rush_yds", stats)

    def test_model_line_follows_the_risk_tier(self):
        safe = next(o for o in parlay_engine.prop_options(qb(), 1, "safe")[0] if o["stat"] == "pass_yds")
        boom = next(o for o in parlay_engine.prop_options(qb(), 1, "boom")[0] if o["stat"] == "pass_yds")
        self.assertLess(safe["model_line"], boom["model_line"])
        self.assertEqual(safe["projected_avg"], 250.0)

    def test_no_touchdown_prop_for_a_player_who_never_scores(self):
        stats = {o["stat"] for o in parlay_engine.prop_options(wr(rec_td=0.0), 1)[0]}
        self.assertNotIn("anytime_td", stats)


class TestBuildManualLeg(unittest.TestCase):
    def test_scores_the_users_line_with_the_same_model_as_the_auto_pool(self):
        leg = parlay_engine.build_manual_leg(qb(), 1, "pass_yds", line=220.5)
        expected = parlay_engine._prob_over(250, 0.28 * 250, 220.5)
        self.assertAlmostEqual(leg["probability"], expected, places=3)
        self.assertEqual((leg["line"], leg["direction"], leg["label"]), (220.5, "Over", "Passing Yards"))

    def test_a_long_shot_line_is_reported_not_refused(self):
        # The auto pool drops anything under 50%; a hand-picked leg keeps its
        # honest (low) probability instead.
        leg = parlay_engine.build_manual_leg(qb(), 1, "pass_yds", line=400)
        self.assertLess(leg["probability"], 0.05)

    def test_anytime_td_for_a_qb_means_td_pass_in_model_mode(self):
        leg = parlay_engine.build_manual_leg(qb(pass_td=1.6, rush_td=0.3), 1, "anytime_td")
        self.assertEqual(leg["label"], "Throws a TD Pass")
        self.assertAlmostEqual(leg["probability"], parlay_engine._prob_anytime_td(1.6), places=3)
        self.assertIsNone(leg["line"])

    def test_anytime_td_at_a_sportsbook_means_rushing_plus_receiving_even_for_a_qb(self):
        row = prop_row("player_anytime_td", "QB1", None, 400)
        leg = parlay_engine.build_manual_leg(qb(pass_td=1.6, rush_td=0.3), 1, "anytime_td", book_row=row)
        self.assertEqual(leg["label"], "Anytime TD")
        self.assertAlmostEqual(leg["probability"], parlay_engine._prob_anytime_td(0.3), places=3)
        self.assertEqual(leg["book_price"], 400)

    def test_a_sportsbook_row_sets_the_line_and_price(self):
        row = prop_row("player_pass_yds", "QB1", 238.5, -114)
        leg = parlay_engine.build_manual_leg(qb(), 1, "pass_yds", line=999, book_row=row)
        self.assertEqual(leg["line"], 238.5)  # the book's, not the caller's
        self.assertEqual(leg["book_price"], -114)
        self.assertAlmostEqual(leg["implied_probability"], odds_math.implied_probability(-114), places=3)
        self.assertAlmostEqual(leg["edge"], leg["probability"] - leg["implied_probability"], places=2)

    def test_rejects_a_stat_that_does_not_fit_the_position(self):
        with self.assertRaises(ValueError):
            parlay_engine.build_manual_leg(wr(), 1, "pass_yds", line=200)

    def test_rejects_a_missing_or_nonpositive_line(self):
        with self.assertRaises(ValueError):
            parlay_engine.build_manual_leg(qb(), 1, "pass_yds")
        with self.assertRaises(ValueError):
            parlay_engine.build_manual_leg(qb(), 1, "pass_yds", line=0)

    def test_rejects_an_unknown_stat(self):
        with self.assertRaises(ValueError):
            parlay_engine.build_manual_leg(qb(), 1, "sacks", line=1.5)

    def test_injury_flag_is_carried(self):
        leg = parlay_engine.build_manual_leg(qb(), 1, "pass_yds", line=220.5,
                                             injury={"status": "QUESTIONABLE", "note": "ankle"})
        self.assertEqual((leg["injury_status"], leg["injury_note"]), ("QUESTIONABLE", "ankle"))
        self.assertNotIn("injury_status", parlay_engine.build_manual_leg(qb(), 1, "pass_yds", line=220.5))


class TestScoreParlay(unittest.TestCase):
    def test_hand_built_and_generated_parlays_are_scored_identically(self):
        players = [make_qb(1, "Kansas QB", "KC"), make_qb(2, "Denver QB", "DEN")]
        generated = parlay_engine.build_parlays(parlay_engine.build_legs(players, 1), num_legs=2,
                                                require_distinct_categories=False)[0]
        rescored = parlay_engine.score_parlay(generated["legs"])
        for key in ("combined_probability", "estimated_decimal_odds", "estimated_american_odds", "risk_score"):
            self.assertEqual(rescored[key], generated[key], key)
        self.assertEqual(rescored["correlation_warnings"], generated["correlation_warnings"])

    def test_book_fields_only_when_every_leg_is_priced(self):
        row = prop_row("player_pass_yds", "QB1", 238.5, -114)
        priced = parlay_engine.build_manual_leg(qb(), 1, "pass_yds", book_row=row)
        unpriced = parlay_engine.build_manual_leg(wr(), 1, "rec_yds", line=60.5)
        self.assertNotIn("book_decimal_odds", parlay_engine.score_parlay([priced, unpriced]))
        priced2 = parlay_engine.build_manual_leg(wr(), 1, "rec_yds", book_row=prop_row("player_reception_yds", "WR2", 60.5, -110))
        scored = parlay_engine.score_parlay([priced, priced2])
        self.assertEqual(scored["book_decimal_odds"], round(odds_math.combined_decimal_odds([-114, -110]), 2))


SCHEDULE = {"weeks": {"2": {
    "KC": {"opponent": "DEN", "is_home": True, "game_date": "2026-09-20T17:00:00Z"},
    "DEN": {"opponent": "KC", "is_home": False, "game_date": "2026-09-20T17:00:00Z"},
    "SF": {"opponent": "LAR", "is_home": True, "game_date": "2026-09-20T20:25:00Z"},
    "LAR": {"opponent": "SF", "is_home": False, "game_date": "2026-09-20T20:25:00Z"},
}}}


def fake_context(players, injuries=None, finished=(), props=None):
    return {
        "data": {"players": players, "current_week": 2},
        "players_by_id": {p["id"]: p for p in players},
        "schedule": SCHEDULE,
        "week_map": SCHEDULE["weeks"]["2"],
        "scoreboard": {"available": True, "teams": {}},
        "finished_teams": set(finished),
        "injuries": injuries or {},
        "usage": {},
        "props": props or {"available": False},
    }


class TestManualLegsRequestValidation(unittest.TestCase):
    """server.Handler._build_manual_legs with a synthetic context: every
    rejection reads as a plain sentence naming the leg, and nothing about a
    ruled-out or finished player slips through."""

    def setUp(self):
        self.players = [qb(1, "KC"), wr(2, "DEN"), qb(3, "SF"), make_player(4, "Bye Guy", "BUF", "WR", rec_yds=80, receptions=6)]
        self.ctx = fake_context(self.players)
        patcher = mock.patch.object(server, "_parlay_context", return_value=self.ctx)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _build(self, body):
        return server.Handler._build_manual_legs(None, body)

    def _error(self, body):
        with self.assertRaises(server.ApiError) as cm:
            self._build(body)
        return cm.exception.message

    def test_valid_slip_returns_scored_legs(self):
        week, legs, ctx, book_title = self._build({"week": 2, "legs": [
            {"player_id": 1, "stat": "pass_yds", "line": 220.5},
            {"player_id": 2, "stat": "receptions", "line": 4.5},
        ]})
        self.assertEqual((week, len(legs), book_title), (2, 2, None))
        self.assertEqual([l["player"] for l in legs], ["QB1", "WR2"])

    def test_leg_count_bounds(self):
        self.assertIn("between 2 and 8", self._error({"week": 2, "legs": [{"player_id": 1, "stat": "pass_yds", "line": 200}]}))
        too_many = [{"player_id": 1, "stat": "pass_yds", "line": 200 + i} for i in range(9)]
        self.assertIn("between 2 and 8", self._error({"week": 2, "legs": too_many}))

    def test_duplicate_leg_is_named(self):
        msg = self._error({"week": 2, "legs": [{"player_id": 1, "stat": "pass_yds", "line": 200}] * 2})
        self.assertIn("Leg 2: QB1 pass_yds is already in this parlay", msg)

    def test_same_player_different_stats_is_allowed(self):
        _, legs, _, _ = self._build({"week": 2, "legs": [
            {"player_id": 1, "stat": "pass_yds", "line": 200}, {"player_id": 1, "stat": "anytime_td"}]})
        self.assertEqual(len(legs), 2)

    def test_bye_week_finished_game_and_ruled_out_players_are_refused(self):
        self.assertIn("on a bye", self._error({"week": 2, "legs": [
            {"player_id": 4, "stat": "rec_yds", "line": 50}, {"player_id": 1, "stat": "pass_yds", "line": 200}]}))
        self.ctx["finished_teams"] = {"SF"}
        self.assertIn("already finished", self._error({"week": 2, "legs": [
            {"player_id": 3, "stat": "pass_yds", "line": 200}, {"player_id": 1, "stat": "pass_yds", "line": 200}]}))
        self.ctx["injuries"] = {2: {"status": "OUT", "note": None}}
        self.assertIn("listed as OUT", self._error({"week": 2, "legs": [
            {"player_id": 2, "stat": "receptions", "line": 4.5}, {"player_id": 1, "stat": "pass_yds", "line": 200}]}))

    def test_questionable_player_is_allowed_but_flagged(self):
        self.ctx["injuries"] = {2: {"status": "QUESTIONABLE", "note": "hamstring"}}
        _, legs, _, _ = self._build({"week": 2, "legs": [
            {"player_id": 2, "stat": "receptions", "line": 4.5}, {"player_id": 1, "stat": "pass_yds", "line": 200}]})
        self.assertEqual(legs[0]["injury_status"], "QUESTIONABLE")

    def test_bad_week_unknown_player_bad_line_and_malformed_leg(self):
        good = {"player_id": 1, "stat": "pass_yds", "line": 200}
        self.assertIn("'week'", self._error({"week": "2", "legs": [good, good]}))
        self.assertIn("unknown player", self._error({"week": 2, "legs": [{"player_id": 99, "stat": "pass_yds", "line": 200}, good]}))
        self.assertIn("'line' must be a number", self._error({"week": 2, "legs": [{"player_id": 1, "stat": "pass_yds", "line": "200"}, good]}))
        self.assertIn("malformed", self._error({"week": 2, "legs": ["nope", good]}))
        self.assertIn("doesn't apply to a WR", self._error({"week": 2, "legs": [{"player_id": 2, "stat": "pass_yds", "line": 200}, good]}))

    def test_sportsbook_mode_requires_the_book_to_post_the_prop(self):
        self.ctx["props"] = {"available": True, "fetched_at": 0, "partial": False,
                             "events": {"e1": {"home": "KC", "away": "DEN", "start_ts": 4e9}},
                             "books": {"fanduel": {"title": "FanDuel", "rows": [prop_row("player_pass_yds", "QB1", 238.5, -114)]}}}
        good_qb = {"player_id": 1, "stat": "pass_yds"}
        msg = self._error({"week": 2, "book": "fanduel", "legs": [good_qb, {"player_id": 2, "stat": "receptions"}]})
        self.assertIn("FanDuel doesn't post a receptions prop for WR2", msg)
        _, legs, _, title = self._build({"week": 2, "book": "fanduel", "legs": [good_qb, {"player_id": 1, "stat": "anytime_td"}]}) \
            if False else (None, None, None, None)
        # A priced leg + an unpriced one from the same book is refused, so
        # add the second row and check the priced result instead.
        self.ctx["props"]["books"]["fanduel"]["rows"].append(prop_row("player_anytime_td", "QB1", None, 300))
        _, legs, _, title = self._build({"week": 2, "book": "fanduel", "legs": [good_qb, {"player_id": 1, "stat": "anytime_td"}]})
        self.assertEqual(title, "FanDuel")
        self.assertEqual([l["book_price"] for l in legs], [-114, 300])
        self.assertEqual(legs[0]["line"], 238.5)

    def test_no_props_loaded_or_unknown_book(self):
        self.assertIn("fetch them first", self._error({"week": 2, "book": "fanduel", "legs": [
            {"player_id": 1, "stat": "pass_yds"}, {"player_id": 2, "stat": "receptions"}]}))
        self.ctx["props"] = {"available": True, "fetched_at": 0, "events": {}, "books": {}}
        self.assertEqual("That sportsbook has no prop odds loaded.", self._error({"week": 2, "book": "<b>x</b>", "legs": [
            {"player_id": 1, "stat": "pass_yds"}, {"player_id": 2, "stat": "receptions"}]}))


if __name__ == "__main__":
    unittest.main()
