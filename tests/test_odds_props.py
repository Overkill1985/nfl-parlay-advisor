"""Tests for the per-sportsbook player-prop side of odds_client: name
normalization, choosing which games are worth spending credits on, parsing one
game's per-event payload, and the credit-guarded refresh. Fixture-driven; the
network is mocked and the cache lives in a temp dir, so nothing here spends
Odds API credits or touches the real cache/."""
import datetime
import json
import os
import tempfile
import unittest
from unittest import mock

import odds_client as oc

NOW = 1_800_000_000  # arbitrary fixed "now"
HOUR = 3600


def iso(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def raw_event(event_id, home, away, starts_in_hours):
    return {
        "id": event_id, "home_team": home, "away_team": away,
        "commence_time": iso(NOW + starts_in_hours * HOUR),
    }


def outcome(name, player, price, point=None):
    o = {"name": name, "description": player, "price": price}
    if point is not None:
        o["point"] = point
    return o


def bookmaker(key, title, markets):
    return {"key": key, "title": title,
            "markets": [{"key": mk, "outcomes": outs} for mk, outs in markets.items()]}


class TestNormalizePlayerName(unittest.TestCase):
    def test_strips_generational_suffixes(self):
        self.assertEqual(oc.normalize_player_name("Kenneth Walker III"), "kenneth walker")
        self.assertEqual(oc.normalize_player_name("Marvin Harrison Jr."), "marvin harrison")

    def test_ignores_punctuation_and_case(self):
        self.assertEqual(oc.normalize_player_name("D'Andre Swift"), "dandre swift")
        self.assertEqual(oc.normalize_player_name("Amon-Ra St. Brown"), "amon ra st brown")

    def test_strips_accents(self):
        self.assertEqual(oc.normalize_player_name("José Ramírez"), "jose ramirez")

    def test_same_player_written_two_ways_compares_equal(self):
        self.assertEqual(oc.normalize_player_name("Kenneth Walker III"),
                         oc.normalize_player_name("kenneth walker"))

    def test_a_name_that_is_only_a_suffix_is_kept(self):
        self.assertEqual(oc.normalize_player_name("V"), "v")

    def test_empty(self):
        self.assertEqual(oc.normalize_player_name(""), "")
        self.assertEqual(oc.normalize_player_name(None), "")


class TestSelectPropEvents(unittest.TestCase):
    EVENTS = [
        raw_event("later", "Kansas City Chiefs", "Indianapolis Colts", 30),
        raw_event("soon", "Green Bay Packers", "Atlanta Falcons", 5),
        raw_event("started", "Denver Broncos", "Jacksonville Jaguars", -2),
        raw_event("too_far", "Dallas Cowboys", "New York Giants", 200),
        raw_event("mystery_team", "Springfield Isotopes", "Kansas City Chiefs", 10),
    ]

    def test_only_upcoming_games_inside_the_horizon(self):
        ids = [e["id"] for e in oc.select_prop_events(self.EVENTS, NOW)]
        self.assertEqual(ids, ["soon", "later"])

    def test_soonest_first(self):
        picked = oc.select_prop_events(self.EVENTS, NOW)
        self.assertLess(picked[0]["start_ts"], picked[1]["start_ts"])

    def test_teams_are_normalized_to_espn_abbreviations(self):
        soon = oc.select_prop_events(self.EVENTS, NOW)[0]
        self.assertEqual((soon["home"], soon["away"]), ("GB", "ATL"))

    def test_capped_at_max_events(self):
        self.assertEqual(len(oc.select_prop_events(self.EVENTS, NOW, max_events=1)), 1)

    def test_game_with_unrecognized_team_is_skipped_not_guessed(self):
        ids = [e["id"] for e in oc.select_prop_events(self.EVENTS, NOW)]
        self.assertNotIn("mystery_team", ids)


class TestCredits(unittest.TestCase):
    def test_cost_is_one_credit_per_market_per_game(self):
        self.assertEqual(oc.estimate_props_cost(16), 16 * len(oc.PROP_MARKETS))

    def test_affordable_count_leaves_the_reserve_untouched(self):
        per_game = len(oc.PROP_MARKETS)
        self.assertEqual(oc.affordable_event_count(oc.CREDIT_RESERVE + 3 * per_game), 3)
        self.assertEqual(oc.affordable_event_count(oc.CREDIT_RESERVE), 0)
        self.assertEqual(oc.affordable_event_count(5), 0)

    def test_unknown_balance_is_unconstrained(self):
        self.assertIsNone(oc.affordable_event_count(None))


class TestParseEventProps(unittest.TestCase):
    def test_keeps_only_over_and_yes_sides(self):
        payload = {"bookmakers": [bookmaker("fanduel", "FanDuel", {
            "player_pass_yds": [outcome("Over", "Patrick Mahomes", -113, 221.5),
                                outcome("Under", "Patrick Mahomes", -113, 221.5)],
            "player_anytime_td": [outcome("Yes", "Travis Kelce", 175)],
        })]}
        rows = oc.parse_event_props(payload)["fanduel"]["rows"]
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["price"] in (-113, 175) for r in rows))

    def test_anytime_td_has_no_line(self):
        payload = {"bookmakers": [bookmaker("fanduel", "FanDuel", {
            "player_anytime_td": [outcome("Yes", "Travis Kelce", 175)]})]}
        row = oc.parse_event_props(payload)["fanduel"]["rows"][0]
        self.assertIsNone(row["point"])
        self.assertEqual(row["norm"], "travis kelce")

    def test_alternate_line_ladder_collapses_to_the_line_nearest_even_money(self):
        # Bovada lists a whole ladder under the standard market key.
        ladder = [outcome("Over", "Patrick Mahomes", price, point) for point, price in
                  [(189.5, -250), (209.5, -145), (219.5, -115), (249.5, 170)]]
        payload = {"bookmakers": [bookmaker("bovada", "Bovada", {"player_pass_yds": ladder})]}
        rows = oc.parse_event_props(payload)["bovada"]["rows"]
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["point"], rows[0]["price"]), (219.5, -115))

    def test_each_book_is_parsed_independently(self):
        payload = {"bookmakers": [
            bookmaker("fanduel", "FanDuel", {"player_pass_yds": [outcome("Over", "Patrick Mahomes", -113, 221.5)]}),
            bookmaker("betmgm", "BetMGM", {"player_pass_yds": [outcome("Over", "Patrick Mahomes", -115, 224.5)]}),
        ]}
        parsed = oc.parse_event_props(payload)
        self.assertEqual(parsed["fanduel"]["rows"][0]["point"], 221.5)
        self.assertEqual(parsed["betmgm"]["rows"][0]["point"], 224.5)
        self.assertEqual(parsed["betmgm"]["title"], "BetMGM")

    def test_yardage_row_without_a_line_is_dropped(self):
        payload = {"bookmakers": [bookmaker("fanduel", "FanDuel", {
            "player_pass_yds": [outcome("Over", "Patrick Mahomes", -113)]})]}
        self.assertEqual(oc.parse_event_props(payload), {})

    def test_markets_we_did_not_ask_for_are_ignored(self):
        payload = {"bookmakers": [bookmaker("fanduel", "FanDuel", {
            "player_pass_tds": [outcome("Over", "Patrick Mahomes", -150, 1.5)]})]}
        self.assertEqual(oc.parse_event_props(payload), {})

    def test_empty_payload(self):
        self.assertEqual(oc.parse_event_props({}), {})


class TestRefreshProps(unittest.TestCase):
    """refresh_props spends real credits in production, so this is where the
    guards are pinned down. Everything runs against a temp cache dir with the
    network mocked."""

    API_KEY = "test-key-should-never-be-persisted"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patches = [
            mock.patch.object(oc, "PROPS_CACHE_PATH", os.path.join(self.tmp.name, "props.json")),
            mock.patch.object(oc, "QUOTA_PATH", os.path.join(self.tmp.name, "quota.json")),
            mock.patch.dict(os.environ, {"ODDS_API_KEY": self.API_KEY}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.calls = []

    def _fake_api(self, events, credits_remaining=400, fail_on_event=None):
        def fake_api_get(url, params, api_key):
            self.calls.append((url, params))
            oc._record_quota({"x-requests-remaining": str(credits_remaining), "x-requests-used": "100"})
            if url == oc.EVENTS_URL:
                return events
            if fail_on_event and fail_on_event in url:
                raise OSError("connection reset")
            return {"bookmakers": [bookmaker("fanduel", "FanDuel", {
                "player_pass_yds": [outcome("Over", "Patrick Mahomes", -113, 221.5)]})]}
        return mock.patch.object(oc, "_api_get", side_effect=fake_api_get)

    def _prop_calls(self):
        return [c for c in self.calls if c[0] != oc.EVENTS_URL]

    def test_no_api_key_makes_no_network_call(self):
        with mock.patch.dict(os.environ, {}, clear=True), self._fake_api([]):
            result = oc.refresh_props(NOW)
        self.assertEqual((result["ok"], result["reason"]), (False, "no_api_key"))
        self.assertEqual(self.calls, [])

    def test_success_caches_rows_tagged_with_their_game(self):
        events = [raw_event("e1", "Kansas City Chiefs", "Indianapolis Colts", 5)]
        with self._fake_api(events):
            result = oc.refresh_props(NOW)
        self.assertTrue(result["ok"])
        cached = oc.get_props()
        self.assertEqual(cached["events"]["e1"]["home"], "KC")
        row = cached["books"]["fanduel"]["rows"][0]
        self.assertEqual((row["event_id"], row["point"]), ("e1", 221.5))

    def test_one_request_per_game_asks_for_every_market_at_once(self):
        events = [raw_event("e1", "Kansas City Chiefs", "Indianapolis Colts", 5),
                  raw_event("e2", "Green Bay Packers", "Atlanta Falcons", 6)]
        with self._fake_api(events):
            oc.refresh_props(NOW)
        self.assertEqual(len(self._prop_calls()), 2)
        for _, params in self._prop_calls():
            self.assertEqual(set(params["markets"].split(",")), set(oc.PROP_MARKETS))

    def test_trims_to_what_the_credit_reserve_allows(self):
        events = [raw_event(f"e{i}", "Kansas City Chiefs", "Indianapolis Colts", 5 + i) for i in range(8)]
        balance = oc.CREDIT_RESERVE + 2 * len(oc.PROP_MARKETS)  # affords exactly 2 games
        with self._fake_api(events, credits_remaining=balance):
            result = oc.refresh_props(NOW)
        self.assertTrue(result["ok"])
        self.assertTrue(result["trimmed"])
        self.assertEqual(result["events_fetched"], 2)
        self.assertEqual(len(self._prop_calls()), 2)

    def test_refuses_when_only_the_reserve_is_left(self):
        events = [raw_event("e1", "Kansas City Chiefs", "Indianapolis Colts", 5)]
        with self._fake_api(events, credits_remaining=oc.CREDIT_RESERVE):
            result = oc.refresh_props(NOW)
        self.assertEqual((result["ok"], result["reason"]), (False, "insufficient_credits"))
        self.assertEqual(self._prop_calls(), [])  # nothing was spent

    def test_cooldown_blocks_a_second_refresh_and_spends_nothing(self):
        events = [raw_event("e1", "Kansas City Chiefs", "Indianapolis Colts", 5)]
        with self._fake_api(events):
            oc.refresh_props(NOW)
            self.calls.clear()
            result = oc.refresh_props(NOW + 60)
        self.assertEqual((result["ok"], result["reason"]), (False, "cooldown"))
        self.assertEqual(self.calls, [])

    def test_allowed_again_after_the_cooldown(self):
        events = [raw_event("e1", "Kansas City Chiefs", "Indianapolis Colts", 5000)]
        with self._fake_api(events):
            oc.refresh_props(NOW)
            later = NOW + oc.PROPS_MIN_REFRESH_SECONDS + 1
            events[0]["commence_time"] = iso(later + 5 * HOUR)
            self.assertTrue(oc.refresh_props(later)["ok"])

    def test_failure_partway_keeps_what_was_already_fetched(self):
        events = [raw_event("good", "Kansas City Chiefs", "Indianapolis Colts", 5),
                  raw_event("bad", "Green Bay Packers", "Atlanta Falcons", 6)]
        with self._fake_api(events, fail_on_event="bad"):
            result = oc.refresh_props(NOW)
        self.assertTrue(result["ok"])
        self.assertTrue(result["partial"])
        self.assertIn("good", oc.get_props()["events"])
        self.assertNotIn("bad", oc.get_props()["events"])

    def test_total_failure_reports_an_error_and_caches_nothing(self):
        events = [raw_event("bad", "Kansas City Chiefs", "Indianapolis Colts", 5)]
        with self._fake_api(events, fail_on_event="bad"):
            result = oc.refresh_props(NOW)
        self.assertEqual((result["ok"], result["reason"]), (False, "fetch_error"))
        self.assertFalse(oc.get_props()["available"])

    def test_no_games_in_the_window(self):
        with self._fake_api([raw_event("far", "Kansas City Chiefs", "Indianapolis Colts", 500)]):
            result = oc.refresh_props(NOW)
        self.assertEqual(result["reason"], "no_upcoming_games")

    def test_the_api_key_is_never_written_to_disk(self):
        events = [raw_event("e1", "Kansas City Chiefs", "Indianapolis Colts", 5)]
        with self._fake_api(events):
            oc.refresh_props(NOW)
        for name in os.listdir(self.tmp.name):
            with open(os.path.join(self.tmp.name, name), encoding="utf-8") as f:
                self.assertNotIn(self.API_KEY, f.read())


class TestPropsStatus(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for p in (
            mock.patch.object(oc, "PROPS_CACHE_PATH", os.path.join(self.tmp.name, "props.json")),
            mock.patch.object(oc, "QUOTA_PATH", os.path.join(self.tmp.name, "quota.json")),
        ):
            p.start()
            self.addCleanup(p.stop)

    def test_nothing_fetched_yet(self):
        status = oc.props_status(NOW)
        self.assertFalse(status["available"])
        self.assertEqual(status["books"], [])
        self.assertIsNone(status["credits_remaining"])

    def test_lists_books_that_have_props_with_credit_balance_and_freshness(self):
        with open(oc.PROPS_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump({
                "available": True, "fetched_at": NOW - 2 * HOUR, "partial": False,
                "events": {"e1": {"home": "KC", "away": "IND", "start_ts": NOW + HOUR},
                           "e0": {"home": "GB", "away": "ATL", "start_ts": NOW - HOUR}},
                "books": {
                    "fanduel": {"title": "FanDuel", "rows": [{"x": 1}, {"x": 2}]},
                    "betmgm": {"title": "BetMGM", "rows": [{"x": 1}]},
                    "empty": {"title": "Empty", "rows": []},
                },
            }, f)
        with open(oc.QUOTA_PATH, "w", encoding="utf-8") as f:
            json.dump({"remaining": 321, "used": 179, "updated_at": NOW}, f)

        status = oc.props_status(NOW)
        self.assertEqual([b["key"] for b in status["books"]], ["betmgm", "fanduel"])  # alphabetical, no empties
        self.assertEqual(status["books"][1]["props"], 2)
        self.assertEqual(status["age_hours"], 2.0)
        self.assertEqual(status["upcoming_events"], 1)  # the started game doesn't count
        self.assertEqual(status["credits_remaining"], 321)


if __name__ == "__main__":
    unittest.main()
