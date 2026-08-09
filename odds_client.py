"""Fetches real market moneylines/spreads/totals from The Odds API
(the-odds-api.com), for two purposes: (1) upgrading correlation.py's
game-script narrative from a speculative roster-strength guess to a real
market read, and (2) letting the user auto-fill a tracked pick's odds
instead of typing them by hand.

Requires a free API key (sign up at the-odds-api.com, 500 credits/month on
the free tier) - read from the ODDS_API_KEY environment variable. Without
one set, `get_odds()` returns {"available": False, "reason": "no_api_key"}
immediately, no network call - this integration is optional; the rest of
the app works identically without it.

IMPORTANT: each /odds call costs API credits scaling with markets x regions
requested. This module's cache TTL is deliberately conservative, and
`get_odds()` should only ever be called with force_refresh=True from the
background refresh loop (server.py's `_refresh_cycle`) - never from a
request handler, where a cache hit is free but a forced refresh on every
page load would burn through the free tier's monthly quota in hours.
"""
import json
import os
import statistics
import time
import urllib.parse
import urllib.request

from cache_io import atomic_write_json

CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")
CACHE_PATH = os.path.join(CACHE_DIR, "odds.json")
ODDS_CACHE_TTL_SECONDS = 5 * 60 * 60  # 5 hours - conserve the free tier's 500 credits/month

ODDS_URL = "https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds"

# The Odds API identifies teams by full name; everything else in this app
# uses ESPN's 3-letter abbreviations (espn_client.PRO_TEAM_ABBR) - a
# separate dialect from nflverse's own team codes (nflverse_client's
# NFLVERSE_TEAM_ABBR). Each external source is normalized to ESPN's
# abbreviation at its own module boundary, so nothing downstream ever has
# to know about this table. A team name not in this map is a real gap
# worth knowing about, so it's dropped (see parse_odds_events), not guessed.
ODDS_API_TEAM_ABBR = {
    "Arizona Cardinals": "ARI", "Atlanta Falcons": "ATL", "Baltimore Ravens": "BAL",
    "Buffalo Bills": "BUF", "Carolina Panthers": "CAR", "Chicago Bears": "CHI",
    "Cincinnati Bengals": "CIN", "Cleveland Browns": "CLE", "Dallas Cowboys": "DAL",
    "Denver Broncos": "DEN", "Detroit Lions": "DET", "Green Bay Packers": "GB",
    "Houston Texans": "HOU", "Indianapolis Colts": "IND", "Jacksonville Jaguars": "JAX",
    "Kansas City Chiefs": "KC", "Las Vegas Raiders": "LV", "Los Angeles Chargers": "LAC",
    "Los Angeles Rams": "LAR", "Miami Dolphins": "MIA", "Minnesota Vikings": "MIN",
    "New England Patriots": "NE", "New Orleans Saints": "NO", "New York Giants": "NYG",
    "New York Jets": "NYJ", "Philadelphia Eagles": "PHI", "Pittsburgh Steelers": "PIT",
    "San Francisco 49ers": "SF", "Seattle Seahawks": "SEA", "Tampa Bay Buccaneers": "TB",
    "Tennessee Titans": "TEN", "Washington Commanders": "WSH",
}


# ---------------------------------------------------------------------------
# Pure parsing/math - no I/O, unit-tested directly with fixture data.
# ---------------------------------------------------------------------------

def _implied_probability(american_odds):
    if american_odds is None:
        return None
    if american_odds > 0:
        return 100 / (american_odds + 100)
    return abs(american_odds) / (abs(american_odds) + 100)


def _probability_to_american(prob):
    if not prob or prob <= 0 or prob >= 1:
        return None
    if prob >= 0.5:
        return round(-100 * prob / (1 - prob))
    return round(100 * (1 - prob) / prob)


def consensus_price(prices):
    """Collapses multiple bookmakers' American-odds prices for the same
    outcome into one consensus number: median IMPLIED PROBABILITY (not a
    raw average of American odds, which isn't linear and would bias the
    result), converted back to a representative American price. This app
    is a single-user narrative/correlation tool, not a line-shopping
    product, so one consensus number per market is the right level of
    detail - not a per-bookmaker breakdown."""
    implied = [p for p in (_implied_probability(x) for x in prices) if p is not None]
    if not implied:
        return None
    return _probability_to_american(statistics.median(implied))


def parse_odds_events(events):
    """events: the raw list from The Odds API's /odds endpoint. Returns
    {frozenset({team_a, team_b}): {home_team, away_team, commence_time,
    moneyline: {abbr: american_odds}, favorite: abbr_or_None,
    spread: {abbr: point}, total: {point, over_odds, under_odds}_or_None}}
    for games where both teams map to a known ESPN abbreviation - a game
    involving an unrecognized team name is skipped, not guessed."""
    games = {}
    for event in events:
        home = ODDS_API_TEAM_ABBR.get(event.get("home_team"))
        away = ODDS_API_TEAM_ABBR.get(event.get("away_team"))
        if not home or not away:
            continue

        h2h_prices = {home: [], away: []}
        spread_points = {home: [], away: []}
        total_points = []
        over_prices = []
        under_prices = []

        for bookmaker in event.get("bookmakers", []):
            for market in bookmaker.get("markets", []):
                key = market.get("key")
                outcomes = market.get("outcomes", [])
                if key == "h2h":
                    for outcome in outcomes:
                        abbr = ODDS_API_TEAM_ABBR.get(outcome.get("name"))
                        if abbr in h2h_prices:
                            h2h_prices[abbr].append(outcome.get("price"))
                elif key == "spreads":
                    for outcome in outcomes:
                        abbr = ODDS_API_TEAM_ABBR.get(outcome.get("name"))
                        if abbr in spread_points and outcome.get("point") is not None:
                            spread_points[abbr].append(outcome["point"])
                elif key == "totals":
                    for outcome in outcomes:
                        if outcome.get("point") is not None:
                            total_points.append(outcome["point"])
                        if outcome.get("name") == "Over":
                            over_prices.append(outcome.get("price"))
                        elif outcome.get("name") == "Under":
                            under_prices.append(outcome.get("price"))

        moneyline = {team: consensus_price(prices) for team, prices in h2h_prices.items() if prices}
        # American-odds favorite = most negative number; min() on the raw
        # values does the right thing (e.g. -200 beats -110 beats +150).
        favorite = min(moneyline, key=moneyline.get) if moneyline else None

        spread = {
            team: round(statistics.median(points), 1)
            for team, points in spread_points.items() if points
        }

        total = None
        if total_points:
            total = {
                "point": round(statistics.median(total_points), 1),
                "over_odds": consensus_price(over_prices) if over_prices else None,
                "under_odds": consensus_price(under_prices) if under_prices else None,
            }

        games[frozenset((home, away))] = {
            "home_team": home,
            "away_team": away,
            "commence_time": event.get("commence_time"),
            "moneyline": moneyline,
            "favorite": favorite,
            "spread": spread,
            "total": total,
        }

    return games


# ---------------------------------------------------------------------------
# Network fetch + cache
# ---------------------------------------------------------------------------

def get_odds(force_refresh=False):
    """{"available": True, "fetched_at": ..., "games": {frozenset({a, b}): {...}}}
    or {"available": False, "reason": "no_api_key"|"fetch_error: ..."}.

    Only ever call with force_refresh=True from the background refresh loop
    - see module docstring re: credit conservation. A force_refresh=False
    call from a request handler is safe (a cache hit does zero network I/O)
    but must never be the thing that triggers a live fetch.
    """
    if not force_refresh and os.path.exists(CACHE_PATH):
        age = time.time() - os.path.getmtime(CACHE_PATH)
        if age < ODDS_CACHE_TTL_SECONDS:
            with open(CACHE_PATH, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if cached.get("available"):
                # frozenset keys don't survive JSON - reconstruct from the
                # "|"-joined string form written by the fetch path below.
                cached["games"] = {frozenset(k.split("|")): v for k, v in cached["games"].items()}
            return cached

    api_key = os.environ.get("ODDS_API_KEY")
    if not api_key:
        return {"available": False, "reason": "no_api_key"}

    params = urllib.parse.urlencode({
        "regions": "us",
        "markets": "h2h,spreads,totals",
        "oddsFormat": "american",
        "apiKey": api_key,
    })
    try:
        req = urllib.request.Request(f"{ODDS_URL}?{params}")
        with urllib.request.urlopen(req, timeout=20) as resp:
            events = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return {"available": False, "reason": f"fetch_error: {exc}"}

    games = parse_odds_events(events)

    serializable_games = {"|".join(sorted(k)): v for k, v in games.items()}
    fetched_at = time.time()
    atomic_write_json(CACHE_PATH, {"available": True, "fetched_at": fetched_at, "games": serializable_games})

    return {"available": True, "fetched_at": fetched_at, "games": games}
