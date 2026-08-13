"""Weekly injury-report scheduling and multi-source merging.

Two things live here, both pure (no I/O) so they're unit-testable without a
network or a clock:

1. **A weekly schedule in US Eastern time.** The injury feeds are refreshed at
   fixed checkpoints tied to the NFL's own reporting rhythm (see
   `SCHEDULE_CHECKPOINTS`), not on the app's generic 6-hour cadence.
2. **A merge of the two injury sources** (`espn_client.get_injuries` and
   `nflverse_client.get_injury_reports`) into one per-player view.

## Why US Eastern is implemented by hand here

The obvious approach - `zoneinfo.ZoneInfo("America/New_York")` - raises
`ZoneInfoNotFoundError` on Windows, which ships no IANA tz database; stdlib
`zoneinfo` expects either the OS database (Linux/macOS) or the `tzdata` pip
package, and this app has a hard zero-pip-dependency constraint. That would
make the app work in the Linux container but not on a Windows host, which is
the worse of the two failure modes (silently different behavior per platform).

So `US_EASTERN` below implements the post-2007 US DST rule directly: forward
on the second Sunday of March, back on the first Sunday of November. That rule
has been stable since the Energy Policy Act of 2005 took effect in 2007, and
it covers every date this app deals with. It is deliberately *not* a general
timezone implementation - it is only correct for US Eastern, 2007 onward.

## Why "catch up" rather than "fire at the checkpoint"

This is a local app on a personal machine; it is very often not running at
6pm on a Thursday. A scheduler that only fires when the clock strikes the
checkpoint would simply never run for most users. Instead `is_refresh_due`
asks a question that survives downtime: *is the data I have older than the
most recent checkpoint that has already passed?* If the machine was asleep
Thursday evening, the next launch sees stale data and refreshes immediately.
"""
import datetime

# (weekday, hour, minute) in US Eastern. weekday matches datetime.weekday():
# Monday=0 ... Sunday=6.
#
#   Thursday 18:00 - after Thursday's practice report is filed and before the
#                    ~20:15 ET Thursday night kickoff.
#   Sunday   11:00 - roughly 90 minutes before the 13:00 ET main slate, when
#                    game-day inactives and final statuses are known.
SCHEDULE_CHECKPOINTS = (
    (3, 18, 0),
    (6, 11, 0),
)

_EPOCH_DAY = datetime.timedelta(days=1)
_HOUR = datetime.timedelta(hours=1)
_ZERO = datetime.timedelta(0)


def _first_sunday_on_or_after(dt):
    days_ahead = 6 - dt.weekday()  # weekday(): Sunday == 6
    if days_ahead:
        dt += datetime.timedelta(days=days_ahead)
    return dt


class _USEastern(datetime.tzinfo):
    """US Eastern time under the post-2007 DST rule. See module docstring for
    why this exists instead of zoneinfo."""

    _STD_OFFSET = datetime.timedelta(hours=-5)
    _DST_OFFSET = datetime.timedelta(hours=-4)

    # Naive local-time markers; the year is substituted per-call.
    #   DST starts: second Sunday in March  = first Sunday on or after Mar 8
    #   DST ends:   first Sunday in November
    _DST_START = datetime.datetime(1, 3, 8, 2)
    _DST_END = datetime.datetime(1, 11, 1, 2)

    def utcoffset(self, dt):
        return self._STD_OFFSET + self.dst(dt)

    def dst(self, dt):
        if dt is None or dt.year < 2007:
            # Pre-2007 used different rule dates. Nothing in this app reaches
            # back that far; report standard time rather than silently
            # applying the modern rule to a date it never governed.
            return _ZERO
        start = _first_sunday_on_or_after(self._DST_START.replace(year=dt.year))
        end = _first_sunday_on_or_after(self._DST_END.replace(year=dt.year))
        naive = dt.replace(tzinfo=None)
        if start <= naive < end:
            return _HOUR
        return _ZERO

    def tzname(self, dt):
        return "EDT" if self.dst(dt) else "EST"


US_EASTERN = _USEastern()


def to_eastern(when):
    """Converts an aware datetime (or a naive one, assumed UTC) to Eastern."""
    if when.tzinfo is None:
        when = when.replace(tzinfo=datetime.timezone.utc)
    return when.astimezone(US_EASTERN)


def last_checkpoint_before(when, checkpoints=SCHEDULE_CHECKPOINTS):
    """The most recent scheduled checkpoint at or before `when`, as an aware
    Eastern datetime. `when` may be naive (treated as UTC) or aware.

    Searching backwards a full week from the current Eastern date is enough:
    every checkpoint recurs weekly, so one of the last 8 days must hold the
    most recent one.
    """
    now_et = to_eastern(when)
    best = None
    for days_back in range(0, 8):
        day = (now_et - datetime.timedelta(days=days_back)).date()
        for weekday, hour, minute in checkpoints:
            if day.weekday() != weekday:
                continue
            candidate = datetime.datetime(
                day.year, day.month, day.day, hour, minute, tzinfo=US_EASTERN
            )
            if candidate <= now_et and (best is None or candidate > best):
                best = candidate
    return best


def next_checkpoint_after(when, checkpoints=SCHEDULE_CHECKPOINTS):
    """The soonest upcoming checkpoint strictly after `when`, as an aware
    Eastern datetime. Display-only - the refresh decision uses
    `is_refresh_due`, which is downtime-tolerant in a way a "next fire time"
    is not."""
    now_et = to_eastern(when)
    best = None
    for days_ahead in range(0, 8):
        day = (now_et + datetime.timedelta(days=days_ahead)).date()
        for weekday, hour, minute in checkpoints:
            if day.weekday() != weekday:
                continue
            candidate = datetime.datetime(
                day.year, day.month, day.day, hour, minute, tzinfo=US_EASTERN
            )
            if candidate > now_et and (best is None or candidate < best):
                best = candidate
    return best


def is_refresh_due(last_fetched_at, now=None, checkpoints=SCHEDULE_CHECKPOINTS):
    """True when the injury data on hand predates the most recent checkpoint.

    `last_fetched_at` is a POSIX timestamp (as stored in the cache files) or
    None if nothing has ever been fetched. Because this compares against the
    last checkpoint that *already passed* rather than waiting for a firing
    moment, an app that was closed all Thursday evening still refreshes the
    first time it runs afterwards.
    """
    if last_fetched_at is None:
        return True
    now = now or datetime.datetime.now(datetime.timezone.utc)
    boundary = last_checkpoint_before(now, checkpoints)
    if boundary is None:
        return True
    return last_fetched_at < boundary.timestamp()


# ---------------------------------------------------------------------------
# Merging the two sources
# ---------------------------------------------------------------------------

# Normalized game-status vocabulary, shared by both sources so the frontend
# never has to know which feed a row came from.
STATUS_OUT = "OUT"
STATUS_DOUBTFUL = "DOUBTFUL"
STATUS_QUESTIONABLE = "QUESTIONABLE"
STATUS_IR = "IR"
STATUS_SUSPENDED = "SUSPENDED"
STATUS_ACTIVE = "ACTIVE"

# Rough severity ordering, used only to sort the report so the most
# consequential names are at the top.
STATUS_SEVERITY = {
    STATUS_IR: 5,
    STATUS_SUSPENDED: 4,
    STATUS_OUT: 3,
    STATUS_DOUBTFUL: 2,
    STATUS_QUESTIONABLE: 1,
    STATUS_ACTIVE: 0,
}


def merge_injury_sources(espn_players, nflverse_players, week):
    """Combines the two feeds into {espn_id: {...}} for a single `week`.

    ESPN is the live feed and wins on game status - it reflects today, while
    nflverse's official report is filed once and not revised. nflverse
    contributes the practice-participation detail ESPN doesn't carry
    (DNP/Limited/Full across the practice week) plus the named body part.

    `sources` on each record names which feeds contributed, so a caller can
    tell a cross-confirmed entry from a single-source one rather than
    treating them as equally certain.
    """
    merged = {}

    for espn_id, rec in (espn_players or {}).items():
        merged[int(espn_id)] = {
            "status": rec.get("status"),
            "detail": rec.get("detail"),
            "comment": rec.get("comment"),
            "updated_at": rec.get("date"),
            "espn_name": rec.get("name"),
            "espn_team": rec.get("team"),
            "practice_status": None,
            "injury": None,
            "secondary_injury": None,
            "sources": ["espn"],
        }

    for espn_id, weeks in (nflverse_players or {}).items():
        rec = (weeks or {}).get(week)
        if not rec:
            continue
        espn_id = int(espn_id)
        entry = merged.get(espn_id)
        if entry is None:
            entry = {
                "status": rec.get("report_status"),
                "detail": None,
                "comment": None,
                "updated_at": None,
                "espn_name": None,
                "espn_team": None,
                "practice_status": None,
                "injury": None,
                "secondary_injury": None,
                "sources": [],
            }
            merged[espn_id] = entry
        entry["practice_status"] = rec.get("practice_status")
        entry["injury"] = rec.get("report_primary_injury") or rec.get("practice_primary_injury")
        entry["secondary_injury"] = (
            rec.get("report_secondary_injury") or rec.get("practice_secondary_injury")
        )
        # Only fill status from nflverse when ESPN didn't supply one - ESPN is
        # the fresher feed, so it wins any disagreement.
        if not entry.get("status"):
            entry["status"] = rec.get("report_status")
        if "nflverse" not in entry["sources"]:
            entry["sources"].append("nflverse")

    return merged
