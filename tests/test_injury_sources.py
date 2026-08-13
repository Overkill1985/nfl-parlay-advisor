"""Tests for the injury-feed parsers in espn_client and nflverse_client.
Fixture-driven and network-free, matching the rest of this suite."""
import unittest

import espn_client
import nflverse_client


def espn_link(player_id):
    return {"href": f"https://www.espn.com/nfl/player/_/id/{player_id}/some-name"}


class TestParseInjuriesPayload(unittest.TestCase):
    PAYLOAD = {
        "timestamp": "2026-10-15T18:00Z",
        "injuries": [
            {
                "id": "25",  # SF in PRO_TEAM_ABBR
                "displayName": "San Francisco 49ers",
                "injuries": [
                    {
                        "status": "Out",
                        "date": "2026-10-15T12:00Z",
                        "shortComment": "Ruled out for Sunday.",
                        "longComment": "A much longer note.",
                        "type": {"description": "out"},
                        "athlete": {"displayName": "George Kittle",
                                    "links": [espn_link(15048)]},
                    },
                    {
                        "status": "Injured Reserve",
                        "type": {"description": "injured-reserve"},
                        "athlete": {"displayName": "Someone Else",
                                    "links": [espn_link(999)]},
                    },
                ],
            }
        ],
    }

    def test_extracts_exact_espn_id_from_link(self):
        players = espn_client.parse_injuries_payload(self.PAYLOAD)
        self.assertIn(15048, players)
        self.assertEqual(players[15048]["name"], "George Kittle")

    def test_normalizes_status_vocabulary(self):
        players = espn_client.parse_injuries_payload(self.PAYLOAD)
        self.assertEqual(players[15048]["status"], "OUT")
        # "Injured Reserve" must fold into the same IR token the fantasy-API
        # path already uses, despite the different spelling.
        self.assertEqual(players[999]["status"], "IR")

    def test_normalizes_team_id_to_espn_abbreviation(self):
        players = espn_client.parse_injuries_payload(self.PAYLOAD)
        self.assertEqual(players[15048]["team"], "SF")
        self.assertEqual(players[15048]["team_name"], "San Francisco 49ers")

    def test_prefers_short_comment(self):
        players = espn_client.parse_injuries_payload(self.PAYLOAD)
        self.assertEqual(players[15048]["comment"], "Ruled out for Sunday.")

    def test_records_without_an_extractable_id_are_skipped(self):
        payload = {"injuries": [{"id": "25", "displayName": "X", "injuries": [
            {"status": "Out", "athlete": {"displayName": "No Links", "links": []}},
            {"status": "Out", "athlete": {"displayName": "Bad Href",
                                          "links": [{"href": "https://espn.com/nfl/team"}]}},
        ]}]}
        self.assertEqual(espn_client.parse_injuries_payload(payload), {})

    def test_unknown_status_passes_through_uppercased(self):
        payload = {"injuries": [{"id": "25", "displayName": "X", "injuries": [
            {"status": "Some New Status", "athlete": {"displayName": "P",
                                                      "links": [espn_link(7)]}},
        ]}]}
        players = espn_client.parse_injuries_payload(payload)
        self.assertEqual(players[7]["status"], "SOME NEW STATUS")
        self.assertEqual(players[7]["raw_status"], "Some New Status")

    def test_unmapped_team_id_does_not_raise(self):
        payload = {"injuries": [{"id": "9999", "displayName": "Unknown", "injuries": [
            {"status": "Out", "athlete": {"displayName": "P", "links": [espn_link(7)]}},
        ]}]}
        players = espn_client.parse_injuries_payload(payload)
        self.assertIsNone(players[7]["team"])

    def test_empty_payload(self):
        self.assertEqual(espn_client.parse_injuries_payload({}), {})


class TestParseInjuryRows(unittest.TestCase):
    ROWS = [
        {"gsis_id": "00-0031234", "week": "3", "team": "SF", "position": "TE",
         "full_name": "George Kittle", "report_status": "Questionable",
         "report_primary_injury": "Hamstring", "report_secondary_injury": "",
         "practice_status": "Limited Participation in Practice",
         "practice_primary_injury": "Hamstring", "practice_secondary_injury": "NA"},
        {"gsis_id": "00-0031234", "week": "4", "team": "SF", "position": "TE",
         "full_name": "George Kittle", "report_status": "", "report_primary_injury": "",
         "report_secondary_injury": "", "practice_status": "Full Participation in Practice",
         "practice_primary_injury": "Hamstring", "practice_secondary_injury": ""},
        {"gsis_id": "", "week": "3", "report_status": "Out"},          # no id
        {"gsis_id": "00-0009999", "week": "", "report_status": "Out"},  # no week
        {"gsis_id": "00-0008888", "week": "bye", "report_status": "Out"},  # unparseable week
    ]

    def test_groups_by_player_and_week(self):
        parsed = nflverse_client.parse_injury_rows(self.ROWS)
        self.assertEqual(sorted(parsed["00-0031234"]), [3, 4])

    def test_blank_and_na_cells_become_none(self):
        parsed = nflverse_client.parse_injury_rows(self.ROWS)
        wk3 = parsed["00-0031234"][3]
        self.assertIsNone(wk3["report_secondary_injury"])
        self.assertIsNone(wk3["practice_secondary_injury"])  # "NA"
        self.assertIsNone(parsed["00-0031234"][4]["report_status"])

    def test_keeps_practice_participation_detail(self):
        parsed = nflverse_client.parse_injury_rows(self.ROWS)
        self.assertEqual(parsed["00-0031234"][3]["practice_status"],
                         "Limited Participation in Practice")
        self.assertEqual(parsed["00-0031234"][3]["report_primary_injury"], "Hamstring")

    def test_unusable_rows_are_skipped_not_fatal(self):
        parsed = nflverse_client.parse_injury_rows(self.ROWS)
        self.assertEqual(list(parsed), ["00-0031234"])

    def test_empty_input(self):
        self.assertEqual(nflverse_client.parse_injury_rows([]), {})


class TestMergeInjuries(unittest.TestCase):
    CROSSWALK = {"by_gsis": {"00-0031234": 15048, "00-0044444": 20001}, "by_pfr": {}}

    def test_rekeys_gsis_to_espn_id(self):
        by_gsis = {"00-0031234": {3: {"report_status": "Out"}}}
        merged = nflverse_client.merge_injuries(self.CROSSWALK, by_gsis)
        self.assertEqual(merged, {15048: {3: {"report_status": "Out"}}})

    def test_players_without_an_espn_id_are_dropped_not_name_matched(self):
        by_gsis = {"00-0077777": {3: {"report_status": "Out"}}}
        self.assertEqual(nflverse_client.merge_injuries(self.CROSSWALK, by_gsis), {})

    def test_multiple_players_and_weeks(self):
        by_gsis = {
            "00-0031234": {3: {"report_status": "Out"}, 4: {"report_status": "Questionable"}},
            "00-0044444": {3: {"report_status": "Doubtful"}},
        }
        merged = nflverse_client.merge_injuries(self.CROSSWALK, by_gsis)
        self.assertEqual(sorted(merged), [15048, 20001])
        self.assertEqual(sorted(merged[15048]), [3, 4])

    def test_empty_input(self):
        self.assertEqual(nflverse_client.merge_injuries(self.CROSSWALK, {}), {})
        self.assertEqual(nflverse_client.merge_injuries(self.CROSSWALK, None), {})


if __name__ == "__main__":
    unittest.main()
