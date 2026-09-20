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

PLAYER PROPS (per-sportsbook odds) are a separate, much more expensive
fetch: each game costs 1 credit per prop market (see `PROP_MARKETS`), so a
full slate is ~80 credits against a 500/month free tier - far too costly to
poll. `refresh_props()` is therefore the one deliberate exception to the
"only the background loop fetches" rule: it runs only from an explicit user
action (POST /api/odds/props/refresh), behind a credit-reserve guard and a
cooldown, never on a timer or a page load. One fetch returns every
bookmaker's prices, so switching sportsbooks afterwards costs nothing.
"""
import datetime
import json
import os
import re
import statistics
import time
import unicodedata
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
            _record_quota(resp.headers)
    except Exception as exc:
        return {"available": False, "reason": f"fetch_error: {exc}"}

    games = parse_odds_events(events)

    serializable_games = {"|".join(sorted(k)): v for k, v in games.items()}
    fetched_at = time.time()
    atomic_write_json(CACHE_PATH, {"available": True, "fetched_at": fetched_at, "games": serializable_games})

    return {"available": True, "fetched_at": fetched_at, "games": games}


# ---------------------------------------------------------------------------
# Player props, per sportsbook
# ---------------------------------------------------------------------------

API_BASE = "https://api.the-odds-api.com/v4/sports/americanfootball_nfl"
EVENTS_URL = f"{API_BASE}/events"  # free - costs no credits
EVENT_ODDS_URL_TMPL = f"{API_BASE}/events/{{event_id}}/odds"

PROPS_CACHE_PATH = os.path.join(CACHE_DIR, "odds_props.json")
QUOTA_PATH = os.path.join(CACHE_DIR, "odds_quota.json")

# The Odds API market key -> this app's stat field (see parlay_engine). Each
# market costs 1 credit per game, so this list IS the per-game price of a
# refresh - adding a market makes every refresh more expensive.
PROP_MARKETS = {
    "player_pass_yds": "pass_yds",
    "player_rush_yds": "rush_yds",
    "player_reception_yds": "rec_yds",
    "player_receptions": "receptions",
    "player_anytime_td": "anytime_td",
}

# Books only post props a few days before kickoff, so games further out than
# this would spend credits to return nothing.
PROPS_HORIZON_HOURS = 96
PROPS_MAX_EVENTS = 16  # a full slate
PROPS_MIN_REFRESH_SECONDS = 10 * 60  # stops a double-click double-spending
CREDIT_RESERVE = 60  # never spend the last of the month's credits on props

_NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def normalize_player_name(name):
    """Lowercased, accent/punctuation-free, suffix-free form of a player name,
    so "Kenneth Walker III" and "kenneth walker" compare equal.

    The Odds API identifies players only by name (no ids), so this app's usual
    exact-id join isn't possible for props. This is the strictest thing that
    can work: an exact match on the normalized name, which the caller must
    further constrain by team - see parlay_engine.match_prop_rows. Anything
    ambiguous is dropped rather than guessed.
    """
    if not name:
        return ""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[.'`]", "", text.lower())
    words = re.sub(r"[^a-z0-9]+", " ", text).split()
    while len(words) > 1 and words[-1] in _NAME_SUFFIXES:
        words.pop()
    return " ".join(words)


def _parse_commence(ts):
    try:
        return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return None


def select_prop_events(events, now_ts, horizon_hours=PROPS_HORIZON_HOURS, max_events=PROPS_MAX_EVENTS):
    """Games worth spending credits on: not yet started, kicking off within
    `horizon_hours`, both teams recognized. Soonest first, capped at
    `max_events`."""
    cutoff = now_ts + horizon_hours * 3600
    picked = []
    for event in events:
        start = _parse_commence(event.get("commence_time"))
        home = ODDS_API_TEAM_ABBR.get(event.get("home_team"))
        away = ODDS_API_TEAM_ABBR.get(event.get("away_team"))
        if start is None or not home or not away or not event.get("id"):
            continue
        if not (now_ts < start <= cutoff):
            continue
        picked.append({
            "id": event["id"], "home": home, "away": away,
            "commence_time": event["commence_time"], "start_ts": start,
        })
    picked.sort(key=lambda e: e["start_ts"])
    return picked[:max_events]


def estimate_props_cost(num_events):
    return num_events * len(PROP_MARKETS)


def affordable_event_count(credits_remaining, reserve=CREDIT_RESERVE):
    """How many games' props can be fetched while leaving `reserve` credits
    untouched. None when the balance isn't known yet (nothing to constrain)."""
    if credits_remaining is None:
        return None
    return max(0, (credits_remaining - reserve) // len(PROP_MARKETS))


def parse_event_props(event_odds):
    """One game's per-event odds payload -> {book_key: {"title": str, "rows":
    [{market, name, norm, point, price}]}}.

    Only the Over / Yes side is kept: parlay legs here are always "will
    exceed" or "will score", so the Under side would only bloat the cache.

    A book can list several lines for one player and market (Bovada posts a
    whole alternate-line ladder under the standard market key - 28 pass-yard
    outcomes vs 4 at every other book). Exactly one row is kept per (market,
    player) per book: the one priced closest to even money, since books set
    their main line near a coin flip. On a coarse ladder that can land one
    step off the true main line (Bovada steps 10 yards) - but every kept row
    is a real line at its real price, so a parlay built from it is still
    bettable exactly as shown.
    """
    out = {}
    for bookmaker in event_odds.get("bookmakers", []):
        best = {}
        for market in bookmaker.get("markets", []):
            market_key = market.get("key")
            if market_key not in PROP_MARKETS:
                continue
            for outcome in market.get("outcomes", []):
                if outcome.get("name") not in ("Over", "Yes"):
                    continue
                price = outcome.get("price")
                name = outcome.get("description")
                point = outcome.get("point")
                if price is None or not name:
                    continue
                if market_key != "player_anytime_td" and point is None:
                    continue
                norm = normalize_player_name(name)
                if not norm:
                    continue
                distance_from_even = abs(_implied_probability(price) - 0.5)
                key = (market_key, norm)
                if key not in best or distance_from_even < best[key][0]:
                    best[key] = (distance_from_even, {
                        "market": market_key, "name": name, "norm": norm,
                        "point": point, "price": price,
                    })
        if best:
            out[bookmaker["key"]] = {
                "title": bookmaker.get("title") or bookmaker["key"],
                "rows": [row for _, row in best.values()],
            }
    return out


def _record_quota(headers):
    """Remembers the credit balance The Odds API reports on every response, so
    the UI can show it and refresh_props can respect the reserve. Never raises
    - bookkeeping must not break a fetch that already succeeded."""
    def as_int(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    remaining = as_int(headers.get("x-requests-remaining"))
    if remaining is None:
        return
    try:
        atomic_write_json(QUOTA_PATH, {
            "remaining": remaining,
            "used": as_int(headers.get("x-requests-used")),
            "updated_at": time.time(),
        })
    except OSError:
        pass


def get_quota():
    """{"remaining", "used", "updated_at"} as last reported by The Odds API,
    or None if no call has been made yet."""
    try:
        with open(QUOTA_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _read_props_cache():
    try:
        with open(PROPS_CACHE_PATH, "r", encoding="utf-8") as f:
            cached = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    return cached if cached.get("available") else None


def get_props():
    """Cached prop odds - never touches the network or spends credits.
    {"available": True, "fetched_at", "partial", "error", "events":
    {event_id: {home, away, commence_time, start_ts}}, "books": {book_key:
    {title, rows: [{market, name, norm, point, price, event_id}]}}} or
    {"available": False, "reason": "not_fetched"}."""
    cached = _read_props_cache()
    return cached if cached else {"available": False, "reason": "not_fetched"}


def _api_get(url, params, api_key):
    query = urllib.parse.urlencode({**params, "apiKey": api_key})
    with urllib.request.urlopen(f"{url}?{query}", timeout=25) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        _record_quota(resp.headers)
        return data


def refresh_props(now_ts=None, max_events=PROPS_MAX_EVENTS):
    """Spends credits: fetches every bookmaker's player-prop odds for the
    upcoming games and caches them. Only ever call this from an explicit user
    action (see the module docstring) - never from a timer or page load.

    Returns {"ok": True, ...summary} or {"ok": False, "reason": <code>,
    "message": <human text>}. If the balance can't cover a full slate while
    leaving CREDIT_RESERVE untouched, it fetches the soonest games it can
    afford and says so (`trimmed`) rather than refusing outright. A failure
    partway keeps what was already fetched (`partial`).
    """
    api_key = os.environ.get("ODDS_API_KEY")
    if not api_key:
        return {"ok": False, "reason": "no_api_key",
                "message": "ODDS_API_KEY isn't set, so prop odds can't be fetched."}

    now_ts = now_ts if now_ts is not None else time.time()

    previous = _read_props_cache()
    if previous:
        age = now_ts - previous.get("fetched_at", 0)
        if age < PROPS_MIN_REFRESH_SECONDS:
            wait_min = int((PROPS_MIN_REFRESH_SECONDS - age) // 60) + 1
            return {"ok": False, "reason": "cooldown",
                    "message": f"Prop odds were fetched moments ago - wait about {wait_min} min before spending more credits."}

    try:
        upcoming = _api_get(EVENTS_URL, {}, api_key)  # free; also refreshes the credit balance
    except Exception as exc:
        return {"ok": False, "reason": "fetch_error", "message": f"Couldn't list upcoming games: {exc}"}

    selected = select_prop_events(upcoming, now_ts, max_events=max_events)
    if not selected:
        return {"ok": False, "reason": "no_upcoming_games",
                "message": f"No games kick off within the next {PROPS_HORIZON_HOURS} hours, so there are no props to fetch yet."}

    quota = get_quota()
    trimmed = False
    limit = affordable_event_count(quota["remaining"] if quota else None)
    if limit is not None:
        if limit < 1:
            return {"ok": False, "reason": "insufficient_credits",
                    "message": f"Only {quota['remaining']} Odds API credits remain and {CREDIT_RESERVE} are held in reserve, so there's not enough to fetch props."}
        if limit < len(selected):
            selected = selected[:limit]
            trimmed = True

    events_by_id = {}
    books = {}
    error = None
    for event in selected:
        try:
            data = _api_get(
                EVENT_ODDS_URL_TMPL.format(event_id=urllib.parse.quote(event["id"], safe="")),
                {"regions": "us", "markets": ",".join(PROP_MARKETS), "oddsFormat": "american"},
                api_key,
            )
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            break
        events_by_id[event["id"]] = {k: event[k] for k in ("home", "away", "commence_time", "start_ts")}
        for book_key, info in parse_event_props(data).items():
            entry = books.setdefault(book_key, {"title": info["title"], "rows": []})
            entry["rows"].extend({**row, "event_id": event["id"]} for row in info["rows"])

    if not events_by_id:
        return {"ok": False, "reason": "fetch_error", "message": error or "No prop odds were returned."}

    atomic_write_json(PROPS_CACHE_PATH, {
        "available": True,
        "fetched_at": now_ts,
        "partial": error is not None,
        "trimmed": trimmed,
        "error": error,
        "events": events_by_id,
        "books": books,
    })

    quota = get_quota()
    return {
        "ok": True,
        "events_fetched": len(events_by_id),
        "events_available": len(select_prop_events(upcoming, now_ts, max_events=PROPS_MAX_EVENTS)),
        "books": sorted(info["title"] for info in books.values()),
        "partial": error is not None,
        "trimmed": trimmed,
        "error": error,
        "credits_remaining": quota["remaining"] if quota else None,
    }


def props_status(now_ts=None):
    """What the UI needs to build the sportsbook dropdown: which books have
    props cached, how fresh they are, the credit balance, and what a refresh
    would cost. Reads local files only - no network, no credits."""
    now_ts = now_ts if now_ts is not None else time.time()
    quota = get_quota()
    cached = _read_props_cache()

    status = {
        "api_key_configured": bool(os.environ.get("ODDS_API_KEY")),
        "credits_remaining": quota["remaining"] if quota else None,
        "credit_reserve": CREDIT_RESERVE,
        "max_refresh_cost": estimate_props_cost(PROPS_MAX_EVENTS),
        "available": bool(cached),
        "books": [],
        "fetched_at": None,
        "age_hours": None,
        "partial": False,
        "upcoming_events": 0,
    }
    if not cached:
        return status

    status["fetched_at"] = cached["fetched_at"]
    status["age_hours"] = round((now_ts - cached["fetched_at"]) / 3600, 1)
    status["partial"] = bool(cached.get("partial"))
    status["upcoming_events"] = sum(
        1 for e in cached.get("events", {}).values() if e.get("start_ts", 0) > now_ts
    )
    status["books"] = sorted(
        ({"key": key, "title": info["title"], "props": len(info["rows"])}
         for key, info in cached.get("books", {}).items() if info["rows"]),
        key=lambda b: b["title"].lower(),
    )
    return status
