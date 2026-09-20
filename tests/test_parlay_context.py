"""Tests for the server-side helpers that feed injury and weather context into
parlays: building the injury map, listing who was left out, and attaching
weather warnings to the legs that made it into parlays. The network-touching
calls (ESPN's injury feed, Open-Meteo) are mocked."""
import unittest
from unittest import mock

import server


def player(pid, name, team, injury_status="ACTIVE", position="WR"):
    return {"id": pid, "name": name, "team": team, "position": position, "injury_status": injury_status}


def live_feed(**by_id):
    return {"available": True, "players": {int(k[1:]): v for k, v in by_id.items()}}


def live(status, comment=None):
    return {"status": status, "comment": comment}


class TestInjuryStatusByPlayer(unittest.TestCase):
    def _run(self, players, feed):
        with mock.patch.object(server.espn_client, "get_injuries", return_value=feed):
            return server._injury_status_by_player(players)

    def test_live_feed_status_and_note_are_used(self):
        result = self._run([player(1, "A", "KC")], live_feed(p1=live("OUT", "Achilles, out for the season")))
        self.assertEqual(result, {1: {"status": "OUT", "note": "Achilles, out for the season"}})

    def test_fantasy_status_is_the_fallback_for_players_missing_from_the_feed(self):
        result = self._run([player(1, "A", "KC", "DOUBTFUL")], live_feed())
        self.assertEqual(result, {1: {"status": "DOUBTFUL", "note": None}})

    def test_live_feed_wins_a_disagreement_even_when_it_says_healthy(self):
        # Fantasy data is cached up to 6h; a player who's since been cleared
        # must not stay hidden because of the stale flag.
        result = self._run([player(1, "A", "KC", "OUT")], live_feed(p1=live("ACTIVE")))
        self.assertEqual(result, {})

    def test_healthy_statuses_are_not_included(self):
        players = [player(1, "A", "KC", "ACTIVE"), player(2, "B", "KC", "PROBABLE"), player(3, "C", "KC", "UNKNOWN")]
        self.assertEqual(self._run(players, live_feed()), {})

    def test_unavailable_feed_falls_back_to_fantasy_statuses(self):
        result = self._run([player(1, "A", "KC", "OUT")], {"available": False, "reason": "fetch_error"})
        self.assertEqual(result, {1: {"status": "OUT", "note": None}})

    def test_very_long_notes_are_truncated_and_empty_notes_become_none(self):
        result = self._run(
            [player(1, "A", "KC"), player(2, "B", "KC")],
            live_feed(p1=live("QUESTIONABLE", "x" * 900), p2=live("QUESTIONABLE", "")))
        self.assertEqual(len(result[1]["note"]), 300)
        self.assertIsNone(result[2]["note"])


SCHEDULE = {"weeks": {"2": {
    "KC": {"opponent": "GB", "is_home": True, "game_date": "2026-09-20T17:00:00Z"},
    "GB": {"opponent": "KC", "is_home": False, "game_date": "2026-09-20T17:00:00Z"},
    "DEN": {"opponent": "LV", "is_home": True, "game_date": "2026-09-20T20:05:00Z"},
    "LV": {"opponent": "DEN", "is_home": False, "game_date": "2026-09-20T20:05:00Z"},
    # BUF has no entry: on a bye
}}}
SCOREBOARD = {"available": True, "teams": {
    "KC": {"completed": True}, "GB": {"completed": True},
    "DEN": {"completed": False}, "LV": {"completed": False},
}}


class TestRuledOutPlayers(unittest.TestCase):
    def test_lists_only_ruled_out_players_whose_game_is_still_to_play(self):
        players = [
            player(1, "Denver Out", "DEN"), player(2, "Denver Questionable", "DEN"),
            player(3, "Kansas Out", "KC"),      # game already finished
            player(4, "Bills Out", "BUF"),      # bye week
            player(5, "Vegas IR", "LV"),
        ]
        injuries = {1: {"status": "OUT"}, 2: {"status": "QUESTIONABLE"}, 3: {"status": "OUT"},
                    4: {"status": "OUT"}, 5: {"status": "IR"}}
        result = server._ruled_out_players(players, injuries, SCHEDULE, SCOREBOARD, 2)
        self.assertEqual([(r["name"], r["status"]) for r in result],
                         [("Denver Out", "OUT"), ("Vegas IR", "IR")])

    def test_without_a_scoreboard_only_byes_are_skipped(self):
        players = [player(1, "Kansas Out", "KC"), player(4, "Bills Out", "BUF")]
        injuries = {1: {"status": "OUT"}, 4: {"status": "OUT"}}
        result = server._ruled_out_players(players, injuries, SCHEDULE, {"available": False}, 2)
        self.assertEqual([r["name"] for r in result], ["Kansas Out"])


def parlay_with(*legs):
    return {"legs": [dict(l) for l in legs]}


def windy():
    return {"conditions": "forecast", "forecast_reliable": True, "wind_mph": 24.0, "precip_in": 0.0, "temp_f": 60.0}


class TestAttachWeatherWarnings(unittest.TestCase):
    def test_warns_the_affected_stat_and_not_the_others(self):
        parlays = [parlay_with({"team": "KC", "stat": "pass_yds"}, {"team": "KC", "stat": "rush_yds"})]
        with mock.patch.object(server.weather, "get_forecast", return_value=windy()):
            server._attach_weather_warnings(parlays, SCHEDULE, 2)
        passing, rushing = parlays[0]["legs"]
        self.assertEqual(len(passing["weather_warnings"]), 1)
        self.assertEqual(rushing["weather_warnings"], [])

    def test_an_away_teams_leg_uses_the_home_stadiums_forecast(self):
        parlays = [parlay_with({"team": "GB", "stat": "pass_yds"})]  # GB is away at KC
        with mock.patch.object(server.weather, "get_forecast", return_value=windy()) as get_forecast:
            server._attach_weather_warnings(parlays, SCHEDULE, 2)
        get_forecast.assert_called_once_with("KC", "2026-09-20T17:00:00Z")

    def test_one_lookup_per_game_however_many_legs_and_parlays_share_it(self):
        parlays = [parlay_with({"team": "KC", "stat": "pass_yds"}, {"team": "GB", "stat": "rec_yds"}),
                   parlay_with({"team": "KC", "stat": "receptions"})]
        with mock.patch.object(server.weather, "get_forecast", return_value=windy()) as get_forecast:
            server._attach_weather_warnings(parlays, SCHEDULE, 2)
        self.assertEqual(get_forecast.call_count, 1)

    def test_a_team_with_no_game_that_week_gets_no_warnings_and_no_lookup(self):
        parlays = [parlay_with({"team": "BUF", "stat": "pass_yds"})]
        with mock.patch.object(server.weather, "get_forecast", return_value=windy()) as get_forecast:
            server._attach_weather_warnings(parlays, SCHEDULE, 2)
        self.assertEqual(parlays[0]["legs"][0]["weather_warnings"], [])
        get_forecast.assert_not_called()

    def test_a_failing_forecast_never_breaks_parlay_generation(self):
        parlays = [parlay_with({"team": "KC", "stat": "pass_yds"}, {"team": "DEN", "stat": "pass_yds"})]
        with mock.patch.object(server.weather, "get_forecast", side_effect=RuntimeError("boom")), \
                mock.patch.object(server.traceback, "print_exc"):
            server._attach_weather_warnings(parlays, SCHEDULE, 2)
        self.assertTrue(all(l["weather_warnings"] == [] for l in parlays[0]["legs"]))

    def test_every_leg_gets_the_field_even_when_calm(self):
        calm = {"conditions": "forecast", "forecast_reliable": True, "wind_mph": 3.0, "precip_in": 0.0, "temp_f": 70.0}
        parlays = [parlay_with({"team": "KC", "stat": "pass_yds"})]
        with mock.patch.object(server.weather, "get_forecast", return_value=calm):
            server._attach_weather_warnings(parlays, SCHEDULE, 2)
        self.assertEqual(parlays[0]["legs"][0]["weather_warnings"], [])


if __name__ == "__main__":
    unittest.main()
