"""Fetches NFL usage data (snap %, target share, air yards share, WOPR) from
nflverse's public GitHub release CSVs - not the `nfl_data_py` pip package,
which pulls parquet via pandas/pyarrow and would break this app's zero-
dependency constraint. These are plain CSV files fetched over HTTP with
stdlib `urllib`/`csv`, no auth, no pip install.

nflverse publishes three files we care about, all as GitHub release assets
under github.com/nflverse/nflverse-data:
  - players.csv (the "players" release) - an id crosswalk: gsis_id, pfr_id,
    espn_id, etc. Large (~7MB) but changes rarely.
  - player_stats_{season}.csv (the "player_stats" release) - weekly
    per-player rows keyed by gsis_id, with target_share/air_yards_share/wopr.
  - snap_counts_{season}.csv (the "snap_counts" release) - weekly per-player
    snap counts/percentages keyed by pfr_player_id.

Both per-season files join to our ESPN player records through the crosswalk
- no fuzzy name matching needed anywhere, confirmed live against real data
(both id columns are populated in practice, not just present in the schema).

IMPORTANT: nflverse's real-world publication cadence lags a live season - a
season this app considers "current" may not have a player_stats/snap_counts
release yet, independent of what this app's SEASON constant says. Every
fetch here degrades gracefully to {"available": False, "reason": ...} rather
than raising, distinguishing "nflverse hasn't published this season yet" (a
404, expected) from "something's actually broken" (any other fetch failure).
"""
import csv
import io
import json
import os
import time
import urllib.error
import urllib.request

from cache_io import atomic_write_json

CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")
CROSSWALK_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60  # 7 days - the id crosswalk rarely changes
STATS_CACHE_TTL_SECONDS = 6 * 60 * 60  # 6 hours - matches espn_client's refresh cadence

RELEASE_BASE = "https://github.com/nflverse/nflverse-data/releases/download"
CROSSWALK_URL = f"{RELEASE_BASE}/players/players.csv"
PLAYER_STATS_URL_TMPL = f"{RELEASE_BASE}/player_stats/player_stats_{{season}}.csv"
SNAP_COUNTS_URL_TMPL = f"{RELEASE_BASE}/snap_counts/snap_counts_{{season}}.csv"

# nflverse's own `team`/`recent_team` columns use a slightly different
# abbreviation dialect than ESPN's (PRO_TEAM_ABBR in espn_client.py) for a
# handful of teams. Not needed for the id-based joins below (player_stats
# and snap_counts both join through players.csv's espn_id/gsis_id/pfr_id,
# never through team+name), but kept here for any future display use of
# nflverse's own team field.
NFLVERSE_TEAM_ABBR = {
    "LA": "LAR",
    "WAS": "WSH",
}


# ---------------------------------------------------------------------------
# Pure parsing/merge logic - no I/O, unit-tested directly with fixture rows.
# ---------------------------------------------------------------------------

def _coerce_id(raw):
    """nflverse's crosswalk sometimes serializes numeric ids as floats (e.g.
    "2577417.0") when they've round-tripped through pandas/R upstream. A
    bare int(x) would raise on those. Returns None for blank/NA cells rather
    than raising, so a row with no ESPN id just gets skipped, not crashed on.
    """
    if raw is None or raw == "":
        return None
    if isinstance(raw, str) and raw.strip().upper() == "NA":
        return None
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return None


def _to_float(raw):
    if raw is None or raw == "" or (isinstance(raw, str) and raw.strip().upper() == "NA"):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def parse_crosswalk_rows(rows):
    """rows: list of dicts from players.csv (needs gsis_id, pfr_id, espn_id
    columns). Returns {"by_gsis": {gsis_id: espn_id}, "by_pfr": {pfr_id: espn_id}}.
    gsis_id/pfr_id are already opaque strings (e.g. "00-0023459", "AbanIs00")
    with no float-serialization risk - only espn_id needs numeric coercion.
    """
    by_gsis = {}
    by_pfr = {}
    for row in rows:
        espn_id = _coerce_id(row.get("espn_id"))
        if espn_id is None:
            continue
        gsis_id = row.get("gsis_id") or None
        pfr_id = row.get("pfr_id") or None
        if gsis_id:
            by_gsis[gsis_id] = espn_id
        if pfr_id:
            by_pfr[pfr_id] = espn_id
    return {"by_gsis": by_gsis, "by_pfr": by_pfr}


def parse_player_stats_rows(rows):
    """rows: list of dicts from player_stats_{season}.csv. Returns
    {gsis_id: {week: {targets, target_share, air_yards_share, wopr}}}."""
    by_player = {}
    for row in rows:
        gsis_id = row.get("player_id")
        week_raw = row.get("week")
        if not gsis_id or not week_raw:
            continue
        try:
            week = int(week_raw)
        except ValueError:
            continue

        by_player.setdefault(gsis_id, {})[week] = {
            "targets": _to_float(row.get("targets")),
            "target_share": _to_float(row.get("target_share")),
            "air_yards_share": _to_float(row.get("air_yards_share")),
            "wopr": _to_float(row.get("wopr")),
        }
    return by_player


def parse_snap_counts_rows(rows):
    """rows: list of dicts from snap_counts_{season}.csv. Returns
    {pfr_player_id: {week: {offense_pct, defense_pct, st_pct}}}."""
    by_player = {}
    for row in rows:
        pfr_id = row.get("pfr_player_id")
        week_raw = row.get("week")
        if not pfr_id or not week_raw:
            continue
        try:
            week = int(week_raw)
        except ValueError:
            continue

        by_player.setdefault(pfr_id, {})[week] = {
            "offense_pct": _to_float(row.get("offense_pct")),
            "defense_pct": _to_float(row.get("defense_pct")),
            "st_pct": _to_float(row.get("st_pct")),
        }
    return by_player


def merge_usage(crosswalk, player_stats_by_gsis, snap_counts_by_pfr):
    """Joins player_stats (by gsis_id) and snap_counts (by pfr_player_id)
    onto ESPN player ids via the crosswalk. Returns {espn_id: {week: {...,
    match_method}}}. A player present in both source files gets fields from
    both merged into the same per-week record. `match_method` records which
    id the record was ever joined through - "gsis", "pfr_id", or "gsis+pfr_id"
    if both contributed - so a caller can tell a fully-resolved record from
    a partial one."""
    merged = {}

    for gsis_id, weeks in (player_stats_by_gsis or {}).items():
        espn_id = crosswalk["by_gsis"].get(gsis_id)
        if espn_id is None:
            continue
        for week, stats in weeks.items():
            entry = merged.setdefault(espn_id, {}).setdefault(week, {"match_method": "gsis"})
            entry.update(stats)

    for pfr_id, weeks in (snap_counts_by_pfr or {}).items():
        espn_id = crosswalk["by_pfr"].get(pfr_id)
        if espn_id is None:
            continue
        for week, stats in weeks.items():
            entry = merged.setdefault(espn_id, {}).setdefault(week, {})
            if "match_method" in entry and entry["match_method"] != "pfr_id":
                entry["match_method"] = f"{entry['match_method']}+pfr_id"
            else:
                entry.setdefault("match_method", "pfr_id")
            # offense_pct/defense_pct/st_pct only ever come from snap_counts,
            # so a plain update is safe even on a record that already
            # existed from player_stats.
            entry.update(stats)

    return merged


def _normalize_week_keys(by_id):
    """Same JSON-int-key issue already hit once in espn_client.py: every
    round-trip through the cache file turns the inner {week: {...}} dict's
    keys into strings. Normalizes back to int. Idempotent."""
    for id_, weeks in by_id.items():
        by_id[id_] = {int(w): v for w, v in weeks.items()}
    return by_id


# ---------------------------------------------------------------------------
# Network fetch + cache
# ---------------------------------------------------------------------------

def _fetch_csv_rows(url):
    """Returns (rows, error). `rows` is a list of dict rows on success, or
    None with `error` describing what went wrong - distinguishes a 404
    (season not yet published upstream - expected, not a bug) from any
    other failure."""
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (compatible; nfl-parlay-advisor/1.0)"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            text = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None, "not_yet_published"
        return None, f"fetch_error: HTTP {exc.code}"
    except Exception as exc:
        return None, f"fetch_error: {exc}"

    return list(csv.DictReader(io.StringIO(text))), None


def _crosswalk_cache_path():
    return os.path.join(CACHE_DIR, "nflverse_players_crosswalk.json")


def get_players_crosswalk(force_refresh=False):
    """{"available": True, "by_gsis": {...}, "by_pfr": {...}} or
    {"available": False, "reason": ...}. Cached ~7 days."""
    cache_path = _crosswalk_cache_path()
    if not force_refresh and os.path.exists(cache_path):
        age = time.time() - os.path.getmtime(cache_path)
        if age < CROSSWALK_CACHE_TTL_SECONDS:
            with open(cache_path, "r", encoding="utf-8") as f:
                return json.load(f)

    rows, error = _fetch_csv_rows(CROSSWALK_URL)
    if rows is None:
        # A refresh failing shouldn't erase a crosswalk that was working a
        # moment ago - fall back to a stale cache if one exists.
        if os.path.exists(cache_path):
            with open(cache_path, "r", encoding="utf-8") as f:
                stale = json.load(f)
            if stale.get("available"):
                return stale
        return {"available": False, "reason": error}

    parsed = parse_crosswalk_rows(rows)
    result = {"available": True, "fetched_at": time.time(), **parsed}
    atomic_write_json(cache_path, result)
    return result


def _player_stats_cache_path(season):
    return os.path.join(CACHE_DIR, f"nflverse_player_stats_{season}.json")


def get_player_stats(season, force_refresh=False):
    """{"available": True, "season": ..., "by_gsis": {gsis_id: {week: {...}}}}
    or {"available": False, "season": ..., "reason": "not_yet_published"|"fetch_error: ..."}.
    """
    cache_path = _player_stats_cache_path(season)
    if not force_refresh and os.path.exists(cache_path):
        age = time.time() - os.path.getmtime(cache_path)
        if age < STATS_CACHE_TTL_SECONDS:
            with open(cache_path, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if cached.get("season") == season:
                if cached.get("available"):
                    _normalize_week_keys(cached["by_gsis"])
                return cached

    rows, error = _fetch_csv_rows(PLAYER_STATS_URL_TMPL.format(season=season))
    if rows is None:
        result = {"available": False, "season": season, "reason": error}
        atomic_write_json(cache_path, result)
        return result

    by_gsis = parse_player_stats_rows(rows)
    result = {"available": True, "season": season, "fetched_at": time.time(), "by_gsis": by_gsis}
    atomic_write_json(cache_path, result)
    return result


def _snap_counts_cache_path(season):
    return os.path.join(CACHE_DIR, f"nflverse_snap_counts_{season}.json")


def get_snap_counts(season, force_refresh=False):
    """{"available": True, "season": ..., "by_pfr": {pfr_id: {week: {...}}}}
    or {"available": False, "season": ..., "reason": "not_yet_published"|"fetch_error: ..."}.
    """
    cache_path = _snap_counts_cache_path(season)
    if not force_refresh and os.path.exists(cache_path):
        age = time.time() - os.path.getmtime(cache_path)
        if age < STATS_CACHE_TTL_SECONDS:
            with open(cache_path, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if cached.get("season") == season:
                if cached.get("available"):
                    _normalize_week_keys(cached["by_pfr"])
                return cached

    rows, error = _fetch_csv_rows(SNAP_COUNTS_URL_TMPL.format(season=season))
    if rows is None:
        result = {"available": False, "season": season, "reason": error}
        atomic_write_json(cache_path, result)
        return result

    by_pfr = parse_snap_counts_rows(rows)
    result = {"available": True, "season": season, "fetched_at": time.time(), "by_pfr": by_pfr}
    atomic_write_json(cache_path, result)
    return result


def get_usage(season, force_refresh=False):
    """Merged, ESPN-id-keyed usage view for `season`:
    {"available": True, "players": {espn_id: {week: {targets, target_share,
    air_yards_share, wopr, offense_pct, defense_pct, st_pct, match_method}}}}

    Returns {"available": False, "reason": ...} only if the id crosswalk
    itself is unavailable - a season nflverse hasn't published yet still
    returns {"available": True, "players": {}} (empty, not an error), so a
    caller can tell "the whole integration is broken" from "no data exists
    for this season yet".
    """
    crosswalk = get_players_crosswalk(force_refresh=force_refresh)
    if not crosswalk.get("available"):
        return {"available": False, "reason": crosswalk.get("reason", "crosswalk_unavailable")}

    player_stats = get_player_stats(season, force_refresh=force_refresh)
    snap_counts = get_snap_counts(season, force_refresh=force_refresh)

    merged = merge_usage(
        crosswalk,
        player_stats.get("by_gsis") if player_stats.get("available") else {},
        snap_counts.get("by_pfr") if snap_counts.get("available") else {},
    )

    return {
        "available": True,
        "season": season,
        "player_stats_available": player_stats.get("available", False),
        "snap_counts_available": snap_counts.get("available", False),
        "players": merged,
    }
