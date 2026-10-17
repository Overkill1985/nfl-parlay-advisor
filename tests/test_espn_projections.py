"""Tests for the ESPN projections request filter and the per-week parsing.
Fixture-driven and network-free, matching the rest of this suite."""
import unittest

import espn_client


class TestPlayersFilter(unittest.TestCase):
    def codes(self, filt):
        return filt["players"]["filterStatsForTopScoringPeriodIds"]["additionalValue"]

    def test_requests_weekly_projection_codes_for_the_given_weeks(self):
        # ESPN returns only the stat sets named here: without "11<season><week>"
        # no weekly projection ever comes back (with_weekly stayed 0 all of
        # 2026 until this was added).
        codes = self.codes(espn_client.players_filter(2026, weeks=(6, 7)))
        self.assertEqual(codes, ["002026", "102026", "002025", "1120266", "1120267"])

    def test_without_weeks_only_the_season_sets(self):
        codes = self.codes(espn_client.players_filter(2026))
        self.assertEqual(codes, ["002026", "102026", "002025"])

    def test_weeks_outside_the_season_are_ignored(self):
        # current_week + 1 after the final week must not request a week 19.
        codes = self.codes(espn_client.players_filter(2026, weeks=(18, 19, 0)))
        self.assertEqual(codes[3:], ["11202618"])


class TestExtractPlayerWeeklyProjection(unittest.TestCase):
    # Shaped like the live kona_player_info response (2026-10-17, week 6).
    ENTRY = {
        "player": {
            "id": 4241478,
            "fullName": "Some Receiver",
            "defaultPositionId": 3,  # WR
            "proTeamId": 25,
            "stats": [
                {"seasonId": 2026, "scoringPeriodId": 0, "statSourceId": 1,
                 "statSplitTypeId": 0, "id": "102026", "appliedTotal": 210.0,
                 "appliedAverage": 12.35, "stats": {}},
                {"seasonId": 2026, "scoringPeriodId": 6, "statSourceId": 1,
                 "statSplitTypeId": 1, "id": "1120266", "appliedTotal": 12.08555414,
                 "stats": {}},
                {"seasonId": 2026, "scoringPeriodId": 5, "statSourceId": 0,
                 "statSplitTypeId": 1, "id": "0120265", "appliedTotal": 9.4,
                 "stats": {}},
            ],
        }
    }

    def test_weekly_projection_and_actual_are_kept_apart(self):
        player = espn_client._extract_player(self.ENTRY, 2026)
        self.assertEqual(list(player["weekly_projections"]), [6])
        self.assertAlmostEqual(player["weekly_projections"][6]["applied_total"], 12.08555414)
        self.assertEqual(list(player["weekly_actuals"]), [5])
        self.assertEqual(player["projected_points_season"], 210.0)


if __name__ == "__main__":
    unittest.main()
