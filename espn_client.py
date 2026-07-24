"""Fetches NFL fantasy projections (season-long and per-week) from ESPN's
public fantasy API.

The endpoints used here are the same ones that power
https://fantasy.espn.com/football/players/projections - undocumented but
requiring no authentication for league-default (non-personal-league) data.

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

CACHE_PATH = os.path.join(os.path.dirname(__file__), "cache", "projections.json")
CACHE_TTL_SECONDS = 6 * 60 * 60  # 6 hours
GAME_STATE_CACHE_TTL_SECONDS = 60 * 60  # 1 hour - current week rarely changes
SCHEDULE_CACHE_TTL_SECONDS = 12 * 60 * 60  # 12 hours - matchups rarely change mid-week

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


def _get(url):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; nfl-parlay-advisor/1.0)"},
    )
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

    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump({"season": season, "week": week}, f)

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
                datetime.datetime.utcfromtimestamp(game_date_ms / 1000).isoformat() + "Z"
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

    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(result, f)

    return result


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
    for s in player.get("stats", []):
        if s.get("seasonId") != season or s.get("statSourceId") != 1:
            continue
        period = s.get("scoringPeriodId")
        if period == 0:
            season_proj = s
        elif period and 1 <= period <= MAX_WEEK:
            weekly_projections[period] = {
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


def get_projections(season, force_refresh=False):
    if not force_refresh and os.path.exists(CACHE_PATH):
        age = time.time() - os.path.getmtime(CACHE_PATH)
        if age < CACHE_TTL_SECONDS:
            with open(CACHE_PATH, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if cached.get("season") == season:
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

    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f)

    return result
