"""Tests for weather.warnings_for_leg - the per-leg weather cautions shown on
parlays. Pure function, no network."""
import unittest

import weather


def forecast(wind=5.0, precip=0.0, temp=65.0, conditions="forecast", reliable=True):
    return {"conditions": conditions, "forecast_reliable": reliable,
            "wind_mph": wind, "precip_in": precip, "temp_f": temp}


class TestWarningsForLeg(unittest.TestCase):
    def test_calm_mild_dry_game_has_no_warnings(self):
        for stat in ("pass_yds", "rec_yds", "receptions", "rush_yds", "anytime_td"):
            self.assertEqual(weather.warnings_for_leg(stat, forecast()), [], stat)

    def test_wind_warns_passing_and_receiving_yardage_only(self):
        windy = forecast(wind=22)
        self.assertEqual(len(weather.warnings_for_leg("pass_yds", windy)), 1)
        self.assertEqual(len(weather.warnings_for_leg("rec_yds", windy)), 1)
        self.assertIn("22 mph", weather.warnings_for_leg("pass_yds", windy)[0])
        # Not receptions, rushing or TDs: no evidence of a direction, so silent.
        for stat in ("receptions", "rush_yds", "anytime_td"):
            self.assertEqual(weather.warnings_for_leg(stat, windy), [], stat)

    def test_precipitation_also_warns_catches(self):
        wet = forecast(precip=0.3)
        for stat in ("pass_yds", "rec_yds", "receptions"):
            self.assertEqual(len(weather.warnings_for_leg(stat, wet)), 1, stat)
        for stat in ("rush_yds", "anytime_td"):
            self.assertEqual(weather.warnings_for_leg(stat, wet), [], stat)

    def test_cold_warns_passing_and_receiving_yardage(self):
        cold = forecast(temp=18)
        self.assertEqual(len(weather.warnings_for_leg("pass_yds", cold)), 1)
        self.assertIn("18", weather.warnings_for_leg("pass_yds", cold)[0])
        self.assertEqual(weather.warnings_for_leg("receptions", cold), [])

    def test_thresholds_are_inclusive(self):
        self.assertTrue(weather.warnings_for_leg("pass_yds", forecast(wind=weather.WIND_MPH_WARNING)))
        self.assertFalse(weather.warnings_for_leg("pass_yds", forecast(wind=weather.WIND_MPH_WARNING - 0.1)))
        self.assertTrue(weather.warnings_for_leg("receptions", forecast(precip=weather.PRECIP_IN_WARNING)))
        self.assertFalse(weather.warnings_for_leg("receptions", forecast(precip=weather.PRECIP_IN_WARNING - 0.01)))
        self.assertTrue(weather.warnings_for_leg("pass_yds", forecast(temp=weather.COLD_F_WARNING)))
        self.assertFalse(weather.warnings_for_leg("pass_yds", forecast(temp=weather.COLD_F_WARNING + 1)))

    def test_several_conditions_give_several_warnings(self):
        awful = forecast(wind=25, precip=0.5, temp=15)
        self.assertEqual(len(weather.warnings_for_leg("pass_yds", awful)), 3)

    def test_dome_or_retractable_roof_never_warns(self):
        dome = {"conditions": "controlled", "forecast_reliable": True,
                "wind_mph": None, "precip_in": None, "temp_f": None}
        self.assertEqual(weather.warnings_for_leg("pass_yds", dome), [])

    def test_a_forecast_too_far_out_to_trust_says_nothing(self):
        far = forecast(wind=40, precip=1.0, temp=0, reliable=False)
        self.assertEqual(weather.warnings_for_leg("pass_yds", far), [])

    def test_an_unknown_or_failed_forecast_is_not_a_warning(self):
        self.assertEqual(weather.warnings_for_leg("pass_yds", forecast(conditions="fetch_failed")), [])
        self.assertEqual(weather.warnings_for_leg("pass_yds", forecast(conditions="unknown")), [])
        self.assertEqual(weather.warnings_for_leg("pass_yds", None), [])
        self.assertEqual(weather.warnings_for_leg("pass_yds", {}), [])

    def test_missing_individual_readings_are_skipped_not_crashed_on(self):
        partial = forecast(wind=None, precip=None, temp=10)
        self.assertEqual(len(weather.warnings_for_leg("pass_yds", partial)), 1)  # cold only

    def test_wording_says_may_never_will(self):
        # The effect sizes aren't known, so the copy must not overclaim.
        for note in weather.warnings_for_leg("pass_yds", forecast(wind=25, precip=0.5, temp=15)):
            self.assertNotIn("will ", note.lower())


if __name__ == "__main__":
    unittest.main()
