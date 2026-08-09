"""Unit tests for nflverse_client.py's pure CSV-parsing/merge logic.

No network access - these test the parsing/join functions directly with
fixture rows, the same way tests/test_history.py tests grading logic without
hitting a database or the network.

Run with: python -m unittest discover -s tests
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nflverse_client as nv


class TestCoerceId(unittest.TestCase):
    def test_plain_int_string(self):
        self.assertEqual(nv._coerce_id("2577417"), 2577417)

    def test_float_serialized_id(self):
        # nflverse's crosswalk sometimes exports ids as "2577417.0" after
        # round-tripping through pandas/R upstream.
        self.assertEqual(nv._coerce_id("2577417.0"), 2577417)

    def test_none_and_blank_return_none(self):
        self.assertIsNone(nv._coerce_id(None))
        self.assertIsNone(nv._coerce_id(""))

    def test_na_string_returns_none(self):
        self.assertIsNone(nv._coerce_id("NA"))
        self.assertIsNone(nv._coerce_id("na"))

    def test_garbage_returns_none(self):
        self.assertIsNone(nv._coerce_id("not-a-number"))


class TestToFloat(unittest.TestCase):
    def test_plain_number(self):
        self.assertEqual(nv._to_float("0.42"), 0.42)

    def test_blank_and_na_return_none(self):
        self.assertIsNone(nv._to_float(""))
        self.assertIsNone(nv._to_float("NA"))
        self.assertIsNone(nv._to_float(None))

    def test_garbage_returns_none(self):
        self.assertIsNone(nv._to_float("oops"))


class TestParseCrosswalkRows(unittest.TestCase):
    def test_basic_parse(self):
        rows = [
            {"gsis_id": "00-0023459", "pfr_id": "RodgAa00", "espn_id": "8439"},
            {"gsis_id": "00-0038543", "pfr_id": "ChasJa00", "espn_id": "4362628"},
        ]
        result = nv.parse_crosswalk_rows(rows)
        self.assertEqual(result["by_gsis"]["00-0023459"], 8439)
        self.assertEqual(result["by_pfr"]["ChasJa00"], 4362628)

    def test_float_serialized_espn_id_coerced(self):
        rows = [{"gsis_id": "00-0023459", "pfr_id": "RodgAa00", "espn_id": "8439.0"}]
        result = nv.parse_crosswalk_rows(rows)
        self.assertEqual(result["by_gsis"]["00-0023459"], 8439)

    def test_missing_espn_id_row_is_skipped(self):
        rows = [
            {"gsis_id": "00-0023459", "pfr_id": "RodgAa00", "espn_id": ""},
            {"gsis_id": "00-0038543", "pfr_id": "ChasJa00", "espn_id": "4362628"},
        ]
        result = nv.parse_crosswalk_rows(rows)
        self.assertNotIn("00-0023459", result["by_gsis"])
        self.assertEqual(result["by_gsis"]["00-0038543"], 4362628)

    def test_missing_gsis_or_pfr_id_still_indexes_the_other(self):
        rows = [{"gsis_id": "", "pfr_id": "ChasJa00", "espn_id": "4362628"}]
        result = nv.parse_crosswalk_rows(rows)
        self.assertEqual(result["by_gsis"], {})
        self.assertEqual(result["by_pfr"]["ChasJa00"], 4362628)


class TestParsePlayerStatsRows(unittest.TestCase):
    def test_basic_parse(self):
        rows = [
            {
                "player_id": "00-0038543", "week": "1", "targets": "10",
                "target_share": "0.25", "air_yards_share": "0.3", "wopr": "0.55",
            },
        ]
        result = nv.parse_player_stats_rows(rows)
        self.assertEqual(result["00-0038543"][1]["targets"], 10.0)
        self.assertEqual(result["00-0038543"][1]["target_share"], 0.25)

    def test_na_numeric_fields_become_none(self):
        rows = [{"player_id": "00-0038543", "week": "1", "targets": "NA",
                  "target_share": "", "air_yards_share": "NA", "wopr": "NA"}]
        result = nv.parse_player_stats_rows(rows)
        self.assertIsNone(result["00-0038543"][1]["targets"])
        self.assertIsNone(result["00-0038543"][1]["target_share"])

    def test_missing_player_id_or_week_skipped(self):
        rows = [
            {"player_id": "", "week": "1", "targets": "5"},
            {"player_id": "00-0038543", "week": "", "targets": "5"},
        ]
        result = nv.parse_player_stats_rows(rows)
        self.assertEqual(result, {})

    def test_week_is_int_keyed(self):
        rows = [{"player_id": "00-0038543", "week": "3", "targets": "5"}]
        result = nv.parse_player_stats_rows(rows)
        self.assertIn(3, result["00-0038543"])
        self.assertNotIn("3", result["00-0038543"])


class TestParseSnapCountsRows(unittest.TestCase):
    def test_basic_parse(self):
        rows = [{"pfr_player_id": "ChasJa00", "week": "1", "offense_pct": "0.95",
                  "defense_pct": "0", "st_pct": "0.1"}]
        result = nv.parse_snap_counts_rows(rows)
        self.assertEqual(result["ChasJa00"][1]["offense_pct"], 0.95)

    def test_missing_id_skipped(self):
        rows = [{"pfr_player_id": "", "week": "1", "offense_pct": "0.95"}]
        result = nv.parse_snap_counts_rows(rows)
        self.assertEqual(result, {})


class TestMergeUsage(unittest.TestCase):
    def setUp(self):
        self.crosswalk = {
            "by_gsis": {"00-0038543": 4362628},
            "by_pfr": {"ChasJa00": 4362628, "UNMATCHED00": 999999999},
        }

    def test_player_stats_only(self):
        merged = nv.merge_usage(
            self.crosswalk,
            {"00-0038543": {1: {"targets": 10.0, "target_share": 0.3}}},
            {},
        )
        self.assertEqual(merged[4362628][1]["targets"], 10.0)
        self.assertEqual(merged[4362628][1]["match_method"], "gsis")

    def test_snap_counts_only(self):
        merged = nv.merge_usage(
            self.crosswalk,
            {},
            {"ChasJa00": {1: {"offense_pct": 0.95}}},
        )
        self.assertEqual(merged[4362628][1]["offense_pct"], 0.95)
        self.assertEqual(merged[4362628][1]["match_method"], "pfr_id")

    def test_both_sources_merge_into_one_record(self):
        merged = nv.merge_usage(
            self.crosswalk,
            {"00-0038543": {1: {"targets": 10.0, "target_share": 0.3}}},
            {"ChasJa00": {1: {"offense_pct": 0.95}}},
        )
        record = merged[4362628][1]
        self.assertEqual(record["targets"], 10.0)
        self.assertEqual(record["offense_pct"], 0.95)
        self.assertEqual(record["match_method"], "gsis+pfr_id")

    def test_gsis_id_not_in_crosswalk_is_dropped_not_guessed(self):
        merged = nv.merge_usage(
            self.crosswalk,
            {"00-0099999": {1: {"targets": 10.0}}},  # not in crosswalk
            {},
        )
        self.assertEqual(merged, {})

    def test_none_inputs_treated_as_empty(self):
        merged = nv.merge_usage(self.crosswalk, None, None)
        self.assertEqual(merged, {})


if __name__ == "__main__":
    unittest.main()
