"""Tests for injury_report.py - the Eastern-time weekly schedule and the
two-source merge. All pure; no network, no clock dependency (every test
passes an explicit `now`)."""
import calendar
import datetime
import unittest

import injury_report as ir


def et(year, month, day, hour, minute=0):
    return datetime.datetime(year, month, day, hour, minute, tzinfo=ir.US_EASTERN)


class TestUSEasternDST(unittest.TestCase):
    """The hand-rolled tzinfo exists because Windows ships no tz database and
    pip is off-limits here - so its DST rule deserves direct testing rather
    than being taken on faith."""

    def _second_sunday_march(self, year):
        sundays = [d for d in calendar.Calendar().itermonthdates(year, 3)
                   if d.month == 3 and d.weekday() == 6]
        return sundays[1]

    def _first_sunday_november(self, year):
        sundays = [d for d in calendar.Calendar().itermonthdates(year, 11)
                   if d.month == 11 and d.weekday() == 6]
        return sundays[0]

    def test_transition_dates_match_the_real_calendar(self):
        # Independently derive the transition days rather than hardcoding
        # them, so this keeps holding in future seasons.
        for year in (2026, 2027, 2028):
            start = self._second_sunday_march(year)
            end = self._first_sunday_november(year)

            before = et(start.year, start.month, start.day, 1, 59)
            after = et(start.year, start.month, start.day, 3, 0)
            self.assertEqual(before.tzname(), "EST", f"{year} pre-spring-forward")
            self.assertEqual(after.tzname(), "EDT", f"{year} post-spring-forward")

            still_dst = et(end.year, end.month, end.day, 1, 0)
            back_to_std = et(end.year, end.month, end.day, 3, 0)
            self.assertEqual(still_dst.tzname(), "EDT", f"{year} pre-fall-back")
            self.assertEqual(back_to_std.tzname(), "EST", f"{year} post-fall-back")

    def test_utc_offsets(self):
        self.assertEqual(et(2026, 10, 15, 18).utcoffset(), datetime.timedelta(hours=-4))
        self.assertEqual(et(2026, 12, 10, 18).utcoffset(), datetime.timedelta(hours=-5))

    def test_converts_to_utc_correctly_across_the_season(self):
        # An NFL season straddles the November transition, so a fixed offset
        # would be an hour wrong for part of it - the reason this class exists.
        self.assertEqual(
            et(2026, 10, 15, 18).astimezone(datetime.timezone.utc).hour, 22)
        self.assertEqual(
            et(2026, 12, 10, 18).astimezone(datetime.timezone.utc).hour, 23)

    def test_naive_input_is_treated_as_utc(self):
        naive_utc = datetime.datetime(2026, 10, 15, 22, 0)
        self.assertEqual(ir.to_eastern(naive_utc).hour, 18)


class TestCheckpoints(unittest.TestCase):
    def test_thursday_checkpoint_boundary(self):
        # Oct 15 2026 is a Thursday.
        self.assertEqual(datetime.date(2026, 10, 15).weekday(), 3)
        just_before = ir.last_checkpoint_before(et(2026, 10, 15, 17, 59))
        just_after = ir.last_checkpoint_before(et(2026, 10, 15, 18, 1))
        self.assertEqual((just_before.month, just_before.day), (10, 11))  # prior Sunday
        self.assertEqual((just_after.month, just_after.day, just_after.hour), (10, 15, 18))

    def test_sunday_checkpoint_boundary(self):
        self.assertEqual(datetime.date(2026, 10, 18).weekday(), 6)
        just_before = ir.last_checkpoint_before(et(2026, 10, 18, 10, 59))
        just_after = ir.last_checkpoint_before(et(2026, 10, 18, 11, 1))
        self.assertEqual((just_before.month, just_before.day, just_before.hour), (10, 15, 18))
        self.assertEqual((just_after.month, just_after.day, just_after.hour), (10, 18, 11))

    def test_next_checkpoint_alternates_thursday_and_sunday(self):
        after_thu = ir.next_checkpoint_after(et(2026, 10, 15, 18, 1))
        self.assertEqual((after_thu.weekday(), after_thu.hour), (6, 11))
        after_sun = ir.next_checkpoint_after(et(2026, 10, 18, 11, 1))
        self.assertEqual((after_sun.weekday(), after_sun.hour), (3, 18))

    def test_checkpoint_is_exact_boundary_inclusive(self):
        exactly = ir.last_checkpoint_before(et(2026, 10, 15, 18, 0))
        self.assertEqual((exactly.day, exactly.hour), (15, 18))


class TestIsRefreshDue(unittest.TestCase):
    def test_never_fetched_is_due(self):
        self.assertTrue(ir.is_refresh_due(None, et(2026, 10, 15, 19)))

    def test_fetched_before_the_checkpoint_is_due(self):
        now = et(2026, 10, 18, 12)             # Sunday, after the 11:00 checkpoint
        fetched = et(2026, 10, 17, 20).timestamp()   # Saturday evening
        self.assertTrue(ir.is_refresh_due(fetched, now))

    def test_fetched_after_the_checkpoint_is_not_due(self):
        now = et(2026, 10, 18, 12)
        fetched = et(2026, 10, 18, 11, 30).timestamp()
        self.assertFalse(ir.is_refresh_due(fetched, now))

    def test_catches_up_after_downtime(self):
        # The whole reason the scheduler compares against the last elapsed
        # checkpoint instead of firing at a moment: a machine that was off all
        # Thursday evening must still refresh when it next runs.
        now = et(2026, 10, 20, 9)              # Tuesday morning
        fetched = et(2026, 10, 14, 9).timestamp()  # previous Wednesday
        self.assertTrue(ir.is_refresh_due(fetched, now))

    def test_not_due_twice_within_the_same_window(self):
        now = et(2026, 10, 16, 9)              # Friday
        fetched = et(2026, 10, 15, 18, 5).timestamp()  # just after Thu checkpoint
        self.assertFalse(ir.is_refresh_due(fetched, now))


class TestMergeInjurySources(unittest.TestCase):
    ESPN = {
        101: {"status": "OUT", "name": "Real Player", "team": "SF",
              "detail": "out", "comment": "ruled out", "date": "2026-10-15T12:00Z"},
        102: {"status": "QUESTIONABLE", "name": "Other Player", "team": "KC",
              "detail": "questionable", "comment": None, "date": None},
    }
    NFLVERSE = {
        101: {3: {"report_status": "Doubtful", "report_primary_injury": "Ankle",
                  "report_secondary_injury": None,
                  "practice_status": "Did Not Participate In Practice",
                  "practice_primary_injury": "Ankle", "practice_secondary_injury": None}},
        103: {3: {"report_status": "Questionable", "report_primary_injury": "Hamstring",
                  "report_secondary_injury": None,
                  "practice_status": "Limited Participation in Practice",
                  "practice_primary_injury": "Hamstring", "practice_secondary_injury": None}},
    }

    def test_espn_status_wins_over_nflverse(self):
        # ESPN is the live feed; nflverse's report is filed once and not
        # revised, so a disagreement should resolve to ESPN.
        merged = ir.merge_injury_sources(self.ESPN, self.NFLVERSE, week=3)
        self.assertEqual(merged[101]["status"], "OUT")

    def test_nflverse_contributes_practice_detail(self):
        merged = ir.merge_injury_sources(self.ESPN, self.NFLVERSE, week=3)
        self.assertEqual(merged[101]["practice_status"], "Did Not Participate In Practice")
        self.assertEqual(merged[101]["injury"], "Ankle")
        self.assertEqual(sorted(merged[101]["sources"]), ["espn", "nflverse"])

    def test_nflverse_only_player_is_included(self):
        merged = ir.merge_injury_sources(self.ESPN, self.NFLVERSE, week=3)
        self.assertIn(103, merged)
        self.assertEqual(merged[103]["status"], "Questionable")
        self.assertEqual(merged[103]["sources"], ["nflverse"])

    def test_espn_only_player_is_included(self):
        merged = ir.merge_injury_sources(self.ESPN, self.NFLVERSE, week=3)
        self.assertEqual(merged[102]["sources"], ["espn"])
        self.assertIsNone(merged[102]["practice_status"])

    def test_other_weeks_are_not_mixed_in(self):
        merged = ir.merge_injury_sources(self.ESPN, self.NFLVERSE, week=9)
        self.assertIsNone(merged[101]["practice_status"])
        self.assertNotIn(103, merged)

    def test_string_keyed_ids_survive_a_json_round_trip(self):
        # Cache files turn int keys into strings; the merge must cope.
        espn = {"101": self.ESPN[101]}
        nflverse = {"101": self.NFLVERSE[101]}
        merged = ir.merge_injury_sources(espn, nflverse, week=3)
        self.assertIn(101, merged)
        self.assertEqual(merged[101]["injury"], "Ankle")

    def test_empty_sources_produce_empty_result(self):
        self.assertEqual(ir.merge_injury_sources({}, {}, week=1), {})
        self.assertEqual(ir.merge_injury_sources(None, None, week=1), {})


if __name__ == "__main__":
    unittest.main()
