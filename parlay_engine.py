"""Builds player-prop 'legs' from per-week (or season-pace fallback)
projections and combines them into parlay suggestions with model-estimated
hit probabilities.

Nothing here is a real sportsbook line. Once ESPN publishes real per-week
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

import correlation
import espn_client
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


def build_legs(players, week, risk="balanced", position_filter="ALL"):
    tier_factor = RISK_TIERS.get(risk, RISK_TIERS["balanced"])
    legs = []

    for p in players:
        if position_filter != "ALL" and p["position"] != position_filter:
            continue

        per_game, _, source = espn_client.get_week_stats(p, week)
        cv_multiplier = WEEKLY_PROJECTION_CV_MULTIPLIER if source == "weekly_projection" else 1.0

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
                })

    # Round-robin merge by stat category so a flat top-N slice still spans
    # multiple prop types, instead of being dominated by whichever category
    # (usually "anytime TD") happens to score highest probability across the board.
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


def build_parlays(legs, num_legs=3, candidate_pool=24, top_n=10, require_distinct_categories=True,
                   schedule=None, team_strength=None):
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

        best.append({
            "legs": list(combo),
            "combined_probability": round(combined_prob, 4),
            "estimated_decimal_odds": decimal_odds,
            "estimated_american_odds": american_odds,
            "correlation_warnings": correlation.analyze_correlations(normalized),
            "game_script": correlation.generate_game_script_summary(normalized, team_strength),
            "risk_score": odds_math.risk_score([leg["probability"] for leg in combo]),
        })

    best.sort(key=lambda pl: pl["combined_probability"], reverse=True)

    if not best and require_distinct_categories:
        # Filtering by position can shrink the pool to fewer distinct
        # categories than num_legs (e.g. QB-only has 3 categories max).
        # Fall back to allowing repeated categories rather than showing nothing.
        return build_parlays(legs, num_legs, candidate_pool, top_n, require_distinct_categories=False,
                              schedule=schedule, team_strength=team_strength)

    return best[:top_n]
