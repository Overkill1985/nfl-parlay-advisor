"""Builds player-prop 'legs' from per-week (or season-pace fallback)
projections and combines them into parlay suggestions with model-estimated
hit probabilities.

`build_legs` invents its own lines - nothing it produces is a real sportsbook
line. (`build_book_legs` is the opposite: it takes a chosen sportsbook's real
line and price and scores them with the same model.) Once ESPN publishes real per-week
projections for a player (usually a few days before that week's games), we
use those directly - see `espn_client.get_week_stats`. Until then we fall
back to season-total / 17 as a per-game estimate, with a wider variance
assumption to reflect the extra uncertainty. Legs model single-game variance
around the per-game mean with a position/stat-specific coefficient of
variation (yardage/reception props) or a Poisson process (touchdown props).
All probabilities are illustrative, not market odds.
"""
import itertools
import math
import time

import correlation
import espn_client
import odds_client
import odds_math

# (stat field, display label, unit, applicable positions, coefficient of variation)
YARDAGE_PROPS = [
    ("pass_yds", "Passing Yards", "yds", {"QB"}, 0.28),
    ("rush_yds", "Rushing Yards", "yds", {"QB", "RB", "WR"}, 0.42),
    ("rec_yds", "Receiving Yards", "yds", {"RB", "WR", "TE"}, 0.45),
    ("receptions", "Receptions", "rec", {"RB", "WR", "TE"}, 0.32),
]

RISK_TIERS = {
    "safe": 0.72,
    "balanced": 0.88,
    "boom": 1.05,
}

MIN_PER_GAME_THRESHOLDS = {
    "pass_yds": 120,
    "rush_yds": 25,
    "rec_yds": 20,
    "receptions": 2.0,
}

# A real published weekly projection is more informative than a season-pace
# average, so we shrink the assumed variance a bit when we have one.
WEEKLY_PROJECTION_CV_MULTIPLIER = 0.8


def _norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _prob_over(mean, sd, line):
    if sd <= 0:
        return 1.0 if mean > line else 0.0
    z = (line - mean) / sd
    return 1 - _norm_cdf(z)


def _prob_anytime_td(lambda_per_game):
    return 1 - math.exp(-lambda_per_game)


def _usage_fields(player_usage):
    """Picks out just the display-relevant nflverse fields, omitted entirely
    (not zeroed) when there's no usage record for this player/week - see
    build_legs' docstring."""
    if not player_usage:
        return {}
    fields = {}
    for key in ("target_share", "offense_pct", "wopr"):
        if player_usage.get(key) is not None:
            fields[key] = round(player_usage[key], 3)
    return fields


# A player ESPN says is out for the game can't hit a prop, so he's left out of
# the pool entirely. Doubtful/questionable players stay (they often do play,
# and a book will still post their props) but carry a visible warning.
EXCLUDED_INJURY_STATUSES = {"OUT", "IR", "SUSPENDED"}
WARN_INJURY_STATUSES = {"DOUBTFUL", "QUESTIONABLE"}


def _is_ruled_out(injury):
    return bool(injury) and injury.get("status") in EXCLUDED_INJURY_STATUSES


def _injury_fields(injury):
    """Warning fields for a doubtful/questionable player's legs; omitted
    entirely for a healthy player so the frontend can tell "no concern" from
    "flagged" (same convention as _usage_fields)."""
    if injury and injury.get("status") in WARN_INJURY_STATUSES:
        return {"injury_status": injury["status"], "injury_note": injury.get("note")}
    return {}


def build_legs(players, week, risk="balanced", position_filter="ALL", usage=None,
                schedule=None, scoreboard=None, injuries=None):
    """`usage` is optional: nflverse_client.get_usage(season)["players"]
    ({espn_id: {week: {target_share, offense_pct, wopr, ...}}}). When
    supplied, matching legs get target_share/offense_pct/wopr attached as
    display/rationale metadata only - they don't affect the probability
    model in this pass. Omitted entirely (not zeroed) when unavailable for
    a given player/week, so the frontend can tell "no data" from "0%".

    `schedule` (espn_client.get_schedule's return) and `scoreboard`
    (espn_client.get_scoreboard's return, for this same `week`) are both
    optional and independently applied:
    - `schedule` drops a player on a bye that week (no game at all - a team
      absent from `schedule["weeks"][str(week)]` has no entry to check).
    - `scoreboard`, when available, drops a player whose game has already
      finished - there's nothing left to bet on once the final whistle
      blows. A scoreboard fetch failure (`available: False`) leaves legs
      unfiltered by completion rather than hiding everyone, since a
      transient ESPN scoreboard hiccup shouldn't take down parlay
      generation entirely.
    `injuries` ({player_id: {"status", "note"}}, only for players with a
    non-healthy designation) drops anyone ruled OUT/IR/SUSPENDED and tags
    doubtful/questionable players' legs with `injury_status`/`injury_note`.
    All three are opt-in (default None = no filtering) so existing callers and
    tests that don't pass them keep today's behavior."""
    tier_factor = RISK_TIERS.get(risk, RISK_TIERS["balanced"])
    week_map = (schedule or {}).get("weeks", {}).get(str(week)) if schedule else None
    completed_teams = set()
    if scoreboard and scoreboard.get("available"):
        completed_teams = {
            abbr for abbr, game in scoreboard.get("teams", {}).items() if game.get("completed")
        }
    legs = []

    for p in players:
        if position_filter != "ALL" and p["position"] != position_filter:
            continue
        if week_map is not None and p["team"] not in week_map:
            continue  # bye week - no game to build a prop for
        if p["team"] in completed_teams:
            continue  # game's over - nothing left to bet on
        injury = (injuries or {}).get(p["id"])
        if _is_ruled_out(injury):
            continue  # not playing - nothing to bet on

        per_game, _, source = espn_client.get_week_stats(p, week)
        cv_multiplier = WEEKLY_PROJECTION_CV_MULTIPLIER if source == "weekly_projection" else 1.0
        player_usage = (usage or {}).get(p["id"], {}).get(week)

        for field, label, unit, positions, cv in YARDAGE_PROPS:
            if p["position"] not in positions:
                continue
            mean = per_game[field]
            if mean < MIN_PER_GAME_THRESHOLDS[field]:
                continue
            line = round(mean * tier_factor * 2) / 2  # round to nearest 0.5
            if line <= 0:
                continue
            sd = cv * cv_multiplier * mean
            prob = _prob_over(mean, sd, line)
            if prob < 0.5 or prob > 0.97:
                continue
            legs.append({
                "player_id": p["id"],
                "player": p["name"],
                "team": p["team"],
                "position": p["position"],
                "stat": field,
                "label": label,
                "unit": unit,
                "line": line,
                "direction": "Over",
                "projected_avg": round(mean, 1),
                "probability": round(prob, 3),
                "source": source,
                "week": week,
                **_usage_fields(player_usage),
                **_injury_fields(injury),
            })

        # Anytime TD (rush + rec TDs combined for skill positions, pass TD for QB)
        if p["position"] == "QB":
            td_lambda = per_game["pass_td"]
            label = "Throws a TD Pass"
        else:
            td_lambda = per_game["rush_td"] + per_game["rec_td"]
            label = "Anytime TD"
        if td_lambda > 0.15:
            prob = _prob_anytime_td(td_lambda)
            if 0.5 <= prob <= 0.97:
                legs.append({
                    "player_id": p["id"],
                    "player": p["name"],
                    "team": p["team"],
                    "position": p["position"],
                    "stat": "anytime_td",
                    "label": label,
                    "unit": "",
                    "line": None,
                    "direction": "Yes",
                    "projected_avg": round(td_lambda, 2),
                    "probability": round(prob, 3),
                    "source": source,
                    "week": week,
                    **_usage_fields(player_usage),
                    **_injury_fields(injury),
                })

    return _diversify_by_stat(legs)


def _diversify_by_stat(legs):
    """Round-robin merge by stat category so a flat top-N slice still spans
    multiple prop types, instead of being dominated by whichever category
    (usually "anytime TD") happens to score highest probability across the board."""
    buckets = {}
    for leg in legs:
        buckets.setdefault(leg["stat"], []).append(leg)
    for bucket in buckets.values():
        bucket.sort(key=lambda l: l["probability"], reverse=True)

    diversified = []
    bucket_lists = list(buckets.values())
    idx = 0
    while any(bucket_lists):
        bucket = bucket_lists[idx % len(bucket_lists)]
        if bucket:
            diversified.append(bucket.pop(0))
        bucket_lists = [b for b in bucket_lists if b]
        if bucket_lists:
            idx += 1

    return diversified


def match_prop_rows(rows, events, players, matchups=None):
    """Joins one sportsbook's cached prop rows (odds_client.get_props) to this
    app's ESPN player records. Returns ([(row, player, event)], {"unmatched":
    n, "ambiguous": n}).

    The Odds API has no player ids, so this can't be this codebase's usual
    exact-id join. It's the strictest join that can work instead: the row's
    normalized name must equal the player's, AND the player's team must be one
    of the two teams in that row's game. `matchups` ({team: opponent} for the
    target week) additionally requires the game to be the one the schedule has
    that team playing that week, so a Week 3 game can't attach to a Week 2
    request. A name that matches more than one player on the team is dropped
    as ambiguous - never guessed.
    """
    by_name = {}
    for player in players:
        by_name.setdefault(odds_client.normalize_player_name(player["name"]), []).append(player)

    matched = []
    unmatched = ambiguous = 0
    for row in rows:
        event = events.get(row["event_id"])
        if not event:
            unmatched += 1
            continue
        teams = {event["home"], event["away"]}
        candidates = [p for p in by_name.get(row["norm"], []) if p["team"] in teams]
        if matchups is not None:
            candidates = [
                p for p in candidates
                if matchups.get(p["team"]) == (event["away"] if p["team"] == event["home"] else event["home"])
            ]
        if len(candidates) == 1:
            matched.append((row, candidates[0], event))
        elif candidates:
            ambiguous += 1
        else:
            unmatched += 1
    return matched, {"unmatched": unmatched, "ambiguous": ambiguous}


def build_book_legs(players, week, book_rows, events, position_filter="ALL", usage=None,
                    schedule=None, scoreboard=None, now_ts=None, injuries=None):
    """Legs priced by a real sportsbook instead of by this model's own lines.

    Where `build_legs` invents a line (mean x risk-tier factor) and reports
    fair odds, this takes the LINE and PRICE the book actually posts and asks
    what this model thinks of it. Each leg carries the book's `line`,
    `book_price` (American), `book_decimal`, the price's `implied_probability`
    (vig included), and `edge` (model probability minus implied).

    Read `edge` with suspicion. The model here is a season-pace average
    with no matchup or game-script information (injuries only remove players who
    are out, and weather only adds a warning - neither moves a probability) - books price all of
    that in. A large "edge" against a sharp book usually means the model is
    missing something the book knows, not that the book is wrong.

    Games already started are excluded (their pre-game lines are gone), on top
    of the same bye/completed filtering `build_legs` applies. Returns
    (legs, match_stats).
    """
    now_ts = now_ts if now_ts is not None else time.time()
    week_map = (schedule or {}).get("weeks", {}).get(str(week)) if schedule else None
    completed_teams = set()
    if scoreboard and scoreboard.get("available"):
        completed_teams = {
            abbr for abbr, game in scoreboard.get("teams", {}).items() if game.get("completed")
        }
    matchups = {team: info["opponent"] for team, info in week_map.items()} if week_map is not None else None

    upcoming = {eid: e for eid, e in events.items() if e.get("start_ts", 0) > now_ts}
    matched, stats = match_prop_rows(book_rows, upcoming, players, matchups)

    yardage = {stat: (label, unit, positions, cv) for stat, label, unit, positions, cv in YARDAGE_PROPS}
    legs = []
    for row, player, _event in matched:
        if position_filter != "ALL" and player["position"] != position_filter:
            continue
        if player["team"] in completed_teams:
            continue
        injury = (injuries or {}).get(player["id"])
        if _is_ruled_out(injury):
            continue

        stat = odds_client.PROP_MARKETS[row["market"]]
        per_game, _, source = espn_client.get_week_stats(player, week)
        cv_multiplier = WEEKLY_PROJECTION_CV_MULTIPLIER if source == "weekly_projection" else 1.0

        if stat == "anytime_td":
            # The book's market is "scores a touchdown", so it's rushing +
            # receiving TDs for everyone - a QB's rushing TDs, NOT the passing
            # TDs build_legs uses for its "Throws a TD Pass" leg.
            td_lambda = per_game["rush_td"] + per_game["rec_td"]
            if td_lambda <= 0:
                continue
            prob = _prob_anytime_td(td_lambda)
            label, unit, line, direction, projected = "Anytime TD", "", None, "Yes", round(td_lambda, 2)
        else:
            label, unit, positions, cv = yardage[stat]
            if player["position"] not in positions:
                continue
            mean = per_game[stat]
            if mean < MIN_PER_GAME_THRESHOLDS[stat]:
                continue
            line = row["point"]
            prob = _prob_over(mean, cv * cv_multiplier * mean, line)
            direction, projected = "Over", round(mean, 1)

        if prob < 0.5 or prob > 0.97:
            continue

        decimal = odds_math.american_to_decimal(row["price"])
        implied = odds_math.implied_probability(row["price"])
        legs.append({
            "player_id": player["id"],
            "player": player["name"],
            "team": player["team"],
            "position": player["position"],
            "stat": stat,
            "label": label,
            "unit": unit,
            "line": line,
            "direction": direction,
            "projected_avg": projected,
            "probability": round(prob, 3),
            "source": source,
            "week": week,
            "book_price": row["price"],
            "book_decimal": round(decimal, 3),
            "implied_probability": round(implied, 3),
            "edge": round(prob - implied, 3),
            **_usage_fields((usage or {}).get(player["id"], {}).get(week)),
            **_injury_fields(injury),
        })

    return _diversify_by_stat(legs), stats


SORT_OPTIONS = ("probability", "payout", "market")


def _parlay_sort_key(parlay, sort_by):
    if sort_by == "payout":
        return parlay.get("book_decimal_odds") or parlay["estimated_decimal_odds"] or 0
    if sort_by == "market":
        return parlay.get("book_implied_probability", parlay["combined_probability"])
    return parlay["combined_probability"]


def build_parlays(legs, num_legs=3, candidate_pool=24, top_n=10, require_distinct_categories=True,
                   schedule=None, team_strength=None, market_odds_by_game=None, sort_by="probability"):
    """`sort_by` is one of SORT_OPTIONS. With sportsbook-priced legs (see
    build_book_legs) "payout" orders by what the book actually pays, and
    "market" by how likely the book's own prices say the parlay is (the
    product of each leg's implied probability) - usually the more trustworthy
    read of the two, since this model's probabilities often disagree wildly
    with a real book's. With model-only legs "payout" orders by the model's own
    fair odds and "market" falls back to the model's probability.

    There is deliberately no "expected value" sort: this model's probabilities
    are season-pace averages with no matchup information, so ranking by EV
    against a real book would just surface wherever the model is most wrong."""
    pool = legs[:candidate_pool]
    best = []

    for combo in itertools.combinations(pool, num_legs):
        player_ids = {leg["player_id"] for leg in combo}
        if len(player_ids) != num_legs:
            continue  # no duplicate players in one parlay

        if require_distinct_categories:
            categories = {leg["stat"] for leg in combo}
            if len(categories) != num_legs:
                continue  # keep each parlay's legs on different prop types

        combined_prob = 1.0
        for leg in combo:
            combined_prob *= leg["probability"]

        decimal_odds = odds_math.fair_decimal_odds(combined_prob)
        decimal_odds = round(decimal_odds, 2) if decimal_odds else None
        american_odds = odds_math.decimal_to_american(decimal_odds)

        normalized = correlation.attach_opponents(
            [correlation.from_model_leg(leg) for leg in combo], schedule
        )

        parlay = {
            "legs": list(combo),
            "combined_probability": round(combined_prob, 4),
            "estimated_decimal_odds": decimal_odds,
            "estimated_american_odds": american_odds,
            "correlation_warnings": correlation.analyze_correlations(normalized),
            "game_script": correlation.generate_game_script_summary(normalized, team_strength, market_odds_by_game),
            "risk_score": odds_math.risk_score([leg["probability"] for leg in combo]),
        }

        if all("book_price" in leg for leg in combo):
            # Real payout at the chosen book: every leg's decimal odds
            # multiplied, which is how a sportsbook prices independent legs.
            # `estimated_*` above stays the model's own fair odds, so the two
            # can be compared side by side.
            book_decimal = round(odds_math.combined_decimal_odds([leg["book_price"] for leg in combo]), 2)
            parlay["book_decimal_odds"] = book_decimal
            parlay["book_american_odds"] = odds_math.decimal_to_american(book_decimal)
            book_implied = 1.0
            for leg in combo:
                book_implied *= leg["implied_probability"]
            parlay["book_implied_probability"] = round(book_implied, 4)
            # Books reprice same-game legs (they're correlated), so for these
            # the multiplied price is an approximation of what the slip pays.
            game_keys = [
                frozenset({n["team"], n["opponent"]}) if n.get("opponent") else frozenset({n["team"]})
                for n in normalized
            ]
            parlay["same_game"] = len(set(game_keys)) < len(game_keys)

        best.append(parlay)

    best.sort(key=lambda pl: _parlay_sort_key(pl, sort_by), reverse=True)

    if not best and require_distinct_categories:
        # Filtering by position can shrink the pool to fewer distinct
        # categories than num_legs (e.g. QB-only has 3 categories max).
        # Fall back to allowing repeated categories rather than showing nothing.
        return build_parlays(legs, num_legs, candidate_pool, top_n, require_distinct_categories=False,
                              schedule=schedule, team_strength=team_strength,
                              market_odds_by_game=market_odds_by_game, sort_by=sort_by)

    return best[:top_n]
