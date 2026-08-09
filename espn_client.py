"""Fetches NFL data from ESPN's public APIs - two distinct, unauthenticated
host families:

  - `lm-api-reads.fantasy.espn.com` - the fantasy football API, same one
    that powers https://fantasy.espn.com/football/players/projections.
    Projections, schedule/matchups, injuries.
  - `site.api.espn.com` - ESPN's general sports API (scores, not fantasy).
    Used only for `get_scoreboard` (real final scores, for grading
    moneyline/spread/total picks in history.py).

Neither requires an API key or login.

Weekly (per-scoringPeriod) projections don't exist in ESPN's data until
shortly before that week's games (historically a few days out). Before that,
callers should fall back to season-long projection / 17 as a per-game
estimate - see `get_week_stats` below, which does this automatically.
"""
import datetime
import json
import os
import time
import urllib.request

from cache_io import atomic_write_json

CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")
CACHE_TTL_SECONDS = 6 * 60 * 60  # 6 hours


def _projections_cache_path(season):
    # Season-specific filename - backtesting against a past season (see
    # history.py) must not clobber the live current-season cache.
    return os.path.join(CACHE_DIR, f"projections_{season}.json")
GAME_STATE_CACHE_TTL_SECONDS = 60 * 60  # 1 hour - current week rarely changes
SCHEDULE_CACHE_TTL_SECONDS = 12 * 60 * 60  # 12 hours - matchups rarely change mid-week
SCOREBOARD_CACHE_TTL_SECONDS = 30 * 60  # 30 min while a week's still in progress

PLAYERS_URL_TMPL = (
    "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}"
    "/segments/0/leaguedefaults/3?view=kona_player_info"
)
GAME_STATE_URL_TMPL = (
    "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}"
    "?view=kona_game_state"
)
SCHEDULE_URL_TMPL = (
    "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}"
    "?view=proTeamSchedules_wl"
)
SCOREBOARD_URL_TMPL = (
    "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
    "?seasontype=2&week={week}&dates={season}"
)

PRO_TEAM_ABBR = {
    0: "FA", 1: "ATL", 2: "BUF", 3: "CHI", 4: "CIN", 5: "CLE", 6: "DAL", 7: "DEN",
    8: "DET", 9: "GB", 10: "TEN", 11: "IND", 12: "KC", 13: "LV", 14: "LAR", 15: "MIA",
    16: "MIN", 17: "NE", 18: "NO", 19: "NYG", 20: "NYJ", 21: "PHI", 22: "ARI", 23: "PIT",
    24: "LAC", 25: "SF", 26: "SEA", 27: "TB", 28: "WSH", 29: "CAR", 30: "JAX", 33: "BAL",
    34: "HOU",
}

POSITION_ID_TO_NAME = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "D/ST"}

# Stat ID -> field name, reverse engineered from ESPN's kona_player_info payload
# by cross-checking known season totals (see build notes).
STAT_IDS = {
    "pass_att": 0,
    "pass_cmp": 1,
    "pass_yds": 3,
    "pass_td": 4,
    "pass_int": 20,
    "rush_att": 23,
    "rush_yds": 24,
    "rush_td": 25,
    "rec_yds": 42,
    "rec_td": 43,
    "receptions": 53,
}

FILTER_LIMIT = 400  # top N players by ownership/rank - plenty for QB/RB/WR/TE parlay legs
MAX_WEEK = 18

# ESPN's injuryStatus strings, normalized to a small fixed set. Anything not
# listed here passes through as-is rather than being silently dropped, since
# preseason/early-season coverage can be inconsistent.
INJURY_STATUS_MAP = {
    "ACTIVE": "ACTIVE",
    "PROBABLE": "PROBABLE",
    "QUESTIONABLE": "QUESTIONABLE",
    "DOUBTFUL": "DOUBTFUL",
    "OUT": "OUT",
    "INJURY_RESERVE": "IR",
    "SUSPENSION": "SUSPENDED",
    "DAY_TO_DAY": "QUESTIONABLE",
}

# Stat 41 looked like it might be receiving targets, but it exactly matches
# receptions (stat 53) in every player checked - not a distinct field, or at
# least not reliably one. Left unused rather than reporting a wrong number.
TARGETS_STAT_CONFIRMED = False


def _get(url):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; nfl-parlay-advisor/1.0)"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get_no_ua(url):
    # site.api.espn.com (used only by get_scoreboard) 403s any request that
    # sets a custom User-Agent header at all - reproducibly, regardless of
    # what the value is - but allows urllib's own default UA through. Cause
    # not understood, just worked around: don't set one for this host.
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_current_week(season):
    """Returns ESPN's current scoringPeriodId (1-18) for the season.

    Cached to a small file since this changes at most once a week.
    """
    cache_path = os.path.join(os.path.dirname(__file__), "cache", "game_state.json")
    if os.path.exists(cache_path):
        age = time.time() - os.path.getmtime(cache_path)
        if age < GAME_STATE_CACHE_TTL_SECONDS:
            with open(cache_path, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if cached.get("season") == season:
                return cached["week"]

    try:
        data = _get(GAME_STATE_URL_TMPL.format(season=season))
        week = data.get("currentScoringPeriod", {}).get("id") or 1
    except Exception:
        week = 1  # don't let a game-state hiccup take down the whole projections fetch
    week = max(1, min(MAX_WEEK, week))

    atomic_write_json(cache_path, {"season": season, "week": week})

    return week


def get_schedule(season, force_refresh=False):
    """Weekly matchup data: {"weeks": {"1": {"KC": {"opponent": "DEN", "is_home":
    True, "game_date": iso_str, "game_id": int}, ...}, ...}, "bye_weeks": {"KC": 5}}.

    Used for same-game/opponent correlation detection and to know which date to
    pull a weather forecast for. Comes from the same ESPN API family as
    everything else here - `proTeamSchedules_wl` on the season endpoint.
    """
    cache_path = os.path.join(os.path.dirname(__file__), "cache", "schedule.json")
    if not force_refresh and os.path.exists(cache_path):
        age = time.time() - os.path.getmtime(cache_path)
        if age < SCHEDULE_CACHE_TTL_SECONDS:
            with open(cache_path, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if cached.get("season") == season:
                return cached

    data = _get(SCHEDULE_URL_TMPL.format(season=season))
    pro_teams = data.get("settings", {}).get("proTeams", [])

    weeks = {}
    bye_weeks = {}
    for team in pro_teams:
        team_id = team.get("id")
        abbr = PRO_TEAM_ABBR.get(team_id)
        if not abbr or abbr == "FA":
            continue
        if team.get("byeWeek"):
            bye_weeks[abbr] = team["byeWeek"]

        for week_str, games in team.get("proGamesByScoringPeriod", {}).items():
            if not games:
                continue
            game = games[0]
            is_home = game.get("homeProTeamId") == team_id
            opponent_id = game.get("awayProTeamId") if is_home else game.get("homeProTeamId")
            game_date_ms = game.get("date")
            game_date_iso = (
                datetime.datetime.fromtimestamp(game_date_ms / 1000, tz=datetime.timezone.utc)
                    .replace(tzinfo=None).isoformat() + "Z"
                if game_date_ms else None
            )
            weeks.setdefault(week_str, {})[abbr] = {
                "opponent": PRO_TEAM_ABBR.get(opponent_id, "UNK"),
                "is_home": is_home,
                "game_date": game_date_iso,
                "game_id": game.get("id"),
            }

    result = {
        "season": season,
        "fetched_at": time.time(),
        "bye_weeks": bye_weeks,
        "weeks": weeks,
    }

    atomic_write_json(cache_path, result)

    return result


def _scoreboard_cache_path(season, week):
    return os.path.join(CACHE_DIR, f"scoreboard_{season}_{week}.json")


def get_scoreboard(season, week, force_refresh=False):
    """Real final scores for `week`: {"available": True, "all_completed":
    bool, "teams": {team_abbr: {opponent, team_score, opponent_score,
    winner, completed}}} - shaped symmetrically to `get_schedule`'s
    per-team-per-week structure. From `site.api.espn.com`, a different,
    unauthenticated ESPN API family (general sports scores, not fantasy)
    than everything else in this module.

    This is what makes moneyline/spread/total auto-grading possible in
    history.py, which previously could only grade player-prop picks.

    A completed game's result can't change, so once a week is fully
    completed its cache is treated as permanently valid - only an
    in-progress/upcoming week's cache expires on the normal short TTL.
    Returns {"available": False, "reason": ...} on fetch failure rather
    than raising, since a scoreboard hiccup shouldn't take down grading or
    the background refresh loop.
    """
    cache_path = _scoreboard_cache_path(season, week)
    if not force_refresh and os.path.exists(cache_path):
        with open(cache_path, "r", encoding="utf-8") as f:
            cached = json.load(f)
        if cached.get("season") == season and cached.get("week") == week:
            if cached.get("all_completed"):
                return cached
            age = time.time() - os.path.getmtime(cache_path)
            if age < SCOREBOARD_CACHE_TTL_SECONDS:
                return cached

    try:
        data = _get_no_ua(SCOREBOARD_URL_TMPL.format(season=season, week=week))
    except Exception as exc:
        return {"available": False, "season": season, "week": week, "reason": f"fetch_error: {exc}"}

    events = data.get("events", [])
    teams = {}
    all_completed = bool(events)
    for event in events:
        completed = bool(event.get("status", {}).get("type", {}).get("completed"))
        if not completed:
            all_completed = False

        competitors = event.get("competitions", [{}])[0].get("competitors", [])
        if len(competitors) != 2:
            continue
        for i, comp in enumerate(competitors):
            other = competitors[1 - i]
            abbr = comp.get("team", {}).get("abbreviation")
            other_abbr = other.get("team", {}).get("abbreviation")
            if not abbr or not other_abbr:
                continue
            teams[abbr] = {
                "opponent": other_abbr,
                "team_score": _int_or_none(comp.get("score")),
                "opponent_score": _int_or_none(other.get("score")),
                "winner": bool(comp.get("winner")),
                "completed": completed,
            }

    result = {
        "available": True,
        "season": season,
        "week": week,
        "fetched_at": time.time(),
        "all_completed": all_completed,
        "teams": teams,
    }
    atomic_write_json(cache_path, result)
    return result


def _int_or_none(raw):
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _fetch_players_from_espn(season):
    espn_filter = {
        "players": {
            "filterActive": {"value": True},
            # A high "top scoring periods" value pulls back every period ESPN
            # has data for (season total + all weeks), not just the season
            # total - this is what lets weekly projections show up here
            # automatically once ESPN publishes them, with no code changes.
            "filterStatsForTopScoringPeriodIds": {
                "value": MAX_WEEK + 2,
                "additionalValue": [
                    f"00{season}", f"10{season}", f"00{season - 1}",
                ],
            },
            "limit": FILTER_LIMIT,
            "sortDraftRanks": {
                "sortPriority": 100,
                "sortAsc": True,
                "value": "STANDARD",
            },
        }
    }
    req = urllib.request.Request(
        PLAYERS_URL_TMPL.format(season=season),
        headers={
            "x-fantasy-filter": json.dumps(espn_filter),
            "User-Agent": "Mozilla/5.0 (compatible; nfl-parlay-advisor/1.0)",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _stats_dict(raw_stats):
    return {name: raw_stats.get(str(sid), 0.0) for name, sid in STAT_IDS.items()}


def _extract_player(entry, season):
    player = entry["player"]
    pos_id = player.get("defaultPositionId")
    position = POSITION_ID_TO_NAME.get(pos_id)
    if position not in ("QB", "RB", "WR", "TE"):
        return None

    season_proj = None
    weekly_projections = {}
    weekly_actuals = {}
    for s in player.get("stats", []):
        if s.get("seasonId") != season:
            continue
        period = s.get("scoringPeriodId")
        if s.get("statSourceId") == 1:
            if period == 0:
                season_proj = s
            elif period and 1 <= period <= MAX_WEEK:
                weekly_projections[period] = {
                    "stats": _stats_dict(s.get("stats", {})),
                    "applied_total": s.get("appliedTotal", 0.0),
                }
        elif s.get("statSourceId") == 0 and period and 1 <= period <= MAX_WEEK:
            # Real results for weeks already played this season - the input
            # for trailing-form/usage tracking (Step 4) and grading/backtesting
            # (Step 5), neither of which existed when this parser was written.
            weekly_actuals[period] = {
                "stats": _stats_dict(s.get("stats", {})),
                "applied_total": s.get("appliedTotal", 0.0),
            }

    if season_proj is None:
        return None

    return {
        "id": player["id"],
        "name": player["fullName"],
        "position": position,
        "team": PRO_TEAM_ABBR.get(player.get("proTeamId"), "FA"),
        "percent_owned": player.get("ownership", {}).get("percentOwned", 0.0),
        "projected_points_season": season_proj.get("appliedTotal", 0.0),
        "projected_points_per_game": season_proj.get("appliedAverage", 0.0),
        "stats_season": _stats_dict(season_proj.get("stats", {})),
        "games": 17,
        "weekly_projections": weekly_projections,
        "weekly_actuals": weekly_actuals,
        "injury_status": INJURY_STATUS_MAP.get(player.get("injuryStatus"), player.get("injuryStatus") or "UNKNOWN"),
        "injured": bool(player.get("injured", False)),
    }


def get_week_stats(player, week):
    """Per-game stat line for `player` for `week`.

    Returns (stats_dict, projected_points, source) where source is
    "weekly_projection" if ESPN has published real numbers for that specific
    week, or "season_pace_estimate" if we fell back to season-total / 17.
    """
    weekly = player.get("weekly_projections", {}).get(week)
    if weekly:
        return weekly["stats"], weekly["applied_total"], "weekly_projection"

    per_game_stats = {k: v / player["games"] for k, v in player["stats_season"].items()}
    return per_game_stats, player["projected_points_per_game"], "season_pace_estimate"


def get_recent_form(player, upto_week, n=5):
    """Trailing-N actual game stat lines for `player`, using only games
    already played this season (weeks strictly before `upto_week`).

    This is the practical stand-in for "usage" - ESPN's free API doesn't
    expose snap share or red-zone touches, so recent-game trend in the stats
    we do have (receptions, rush attempts, yardage) is what's available.
    Returns games_available=0 (not an error) when nothing's been played yet,
    e.g. before the season starts or in a player's first couple of weeks.
    """
    actuals = player.get("weekly_actuals", {})
    played_weeks = sorted((w for w in actuals if w < upto_week), reverse=True)[:n]
    games = [actuals[w] for w in played_weeks]

    if not games:
        return {"games_available": 0, "weeks_used": [], "avg_stats": {}, "avg_points": None}

    avg_stats = {
        stat_name: round(sum(g["stats"][stat_name] for g in games) / len(games), 1)
        for stat_name in STAT_IDS
    }
    avg_points = round(sum(g["applied_total"] for g in games) / len(games), 1)

    return {
        "games_available": len(games),
        "weeks_used": played_weeks,
        "avg_stats": avg_stats,
        "avg_points": avg_points,
    }


def _normalize_week_keys(players):
    """JSON has no integer-keyed-dict concept, so every round-trip through
    the cache file turns weekly_projections/weekly_actuals keys into strings.
    Every other function here (get_week_stats, get_recent_form, history.py's
    grading/backtesting) indexes those dicts with an int week number, so
    without this they'd silently miss on every cache hit - falling back to
    season-pace estimates and reporting "no games played" even when real
    data exists. Idempotent: a no-op on already-int keys.
    """
    for p in players:
        for field in ("weekly_projections", "weekly_actuals"):
            p[field] = {int(k): v for k, v in p.get(field, {}).items()}
    return players


def get_projections(season, force_refresh=False):
    cache_path = _projections_cache_path(season)
    if not force_refresh and os.path.exists(cache_path):
        age = time.time() - os.path.getmtime(cache_path)
        if age < CACHE_TTL_SECONDS:
            with open(cache_path, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if cached.get("season") == season:
                _normalize_week_keys(cached["players"])
                return cached

    data = _fetch_players_from_espn(season)
    players = []
    for entry in data.get("players", []):
        parsed = _extract_player(entry, season)
        if parsed:
            players.append(parsed)

    players.sort(key=lambda p: p["projected_points_season"], reverse=True)

    result = {
        "season": season,
        "fetched_at": time.time(),
        "current_week": get_current_week(season),
        "players": players,
    }

    atomic_write_json(cache_path, result)

    _normalize_week_keys(result["players"])
    return result
