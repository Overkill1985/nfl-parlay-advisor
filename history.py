"""Grading, model calibration tracking, and mechanical backtesting.

Two distinct kinds of "backtesting" happen here, and they should not be
conflated:

1. **Mechanical backtest** (`backtest_mechanical`) - replays a simple,
   fixed rule ("bet the over at X% of trailing average") against ESPN's
   real historical actual stats, using only the trailing average available
   *at the time* (no lookahead into the season). This is honestly
   computable today, even for seasons before this app existed, because it
   only needs actual results, not historical projections.

2. **Model calibration** (`calibration_report`) - checks whether *this
   app's own* generated probabilities were accurate, using `model_snapshots`
   rows the background refresh loop writes every cycle (see server.py).
   This can only ever cover weeks since that logging started - there is no
   way to retroactively know what our model would have said in a past week,
   since ESPN doesn't expose historical point-in-time projections. Don't
   present (1) as if it validates (2), or vice versa.
"""
import datetime

import odds_math
import storage

GRADE_HIT = "hit"
GRADE_MISS = "miss"
GRADE_PUSH = "push"

PICK_STATUS_FOR_GRADE = {GRADE_HIT: "graded_win", GRADE_MISS: "graded_loss", GRADE_PUSH: "graded_push"}


def _now():
    return datetime.datetime.now(datetime.UTC).isoformat()


def _grade_over_under(direction, line, actual_value):
    if line is None or actual_value is None:
        return None
    if actual_value == line:
        return GRADE_PUSH
    cleared = actual_value > line
    if direction == "Under":
        cleared = not cleared
    return GRADE_HIT if cleared else GRADE_MISS


def _grade_anytime(actual_count):
    if actual_count is None:
        return None
    return GRADE_HIT if actual_count > 0 else GRADE_MISS


def _actual_td_count(stat_field, position, actual_stats):
    if stat_field == "anytime_td":
        return actual_stats["pass_td"] if position == "QB" else actual_stats["rush_td"] + actual_stats["rec_td"]
    return actual_stats.get(stat_field)


def _grade_moneyline(picked_won, tied):
    """NFL regular-season ties are rare but real; the standard sportsbook
    convention is a push, not a loss, so that's handled explicitly rather
    than falling through to a default."""
    if tied:
        return GRADE_PUSH
    return GRADE_HIT if picked_won else GRADE_MISS


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------

def grade_game_market_pick(pick, scores_by_team):
    """Returns (actual_value, grade_result) for a moneyline/spread/total
    pick using real final scores (`scores_by_team`, keyed by team
    abbreviation - see espn_client.get_scoreboard), or (None, None) if it
    can't be graded yet (game not played, or the pick's team isn't in the
    scoreboard data supplied).

    Convention used here (documented, not schema-enforced - storage.py's
    PICK_FIELDS/PICK_DIRECTIONS already support all three shapes without a
    migration):
      - moneyline: `team` = the team backed to win. `direction`/
        `line_entered` unused.
      - spread: `team` = the team backed. `line_entered` = the SIGNED
        spread for that team (e.g. -3.5 = favored by 3.5). `direction`
        unused.
      - total: `team` = either team in the game (just identifies which
        game). `direction` = Over/Under. `line_entered` = the total number.
    """
    if pick["market_type"] not in ("moneyline", "spread", "total") or not pick.get("team"):
        return None, None
    game = scores_by_team.get(pick["team"])
    if not game or not game.get("completed"):
        return None, None

    team_score = game["team_score"]
    opponent_score = game["opponent_score"]
    if team_score is None or opponent_score is None:
        return None, None

    if pick["market_type"] == "moneyline":
        margin = team_score - opponent_score
        return margin, _grade_moneyline(game["winner"], team_score == opponent_score)

    if pick["market_type"] == "spread":
        margin = team_score - opponent_score
        line = pick.get("line_entered")
        if line is None:
            return margin, None
        # Cover condition is margin + line > 0 <=> margin > -line, which is
        # exactly what _grade_over_under("Over", -line, margin) checks -
        # spread grading reduces to the same over/under logic used for
        # player props and totals, just with a sign flip.
        return margin, _grade_over_under("Over", -line, margin)

    total = team_score + opponent_score
    return total, _grade_over_under(pick.get("direction"), pick.get("line_entered"), total)


def grade_pick_against_actual(pick, players_by_id, scores_by_team=None):
    """Returns (actual_value, grade_result) for a pick, or (None, None) if
    it can't be auto-graded yet. Player props are graded against real
    per-player weekly stats (`players_by_id`); moneyline/spread/total picks
    are graded against real final scores (`scores_by_team`) when supplied -
    without it, game-market picks are simply left ungraded rather than
    erroring, since wiring up a score feed is optional.
    """
    if pick["market_type"] in ("moneyline", "spread", "total"):
        if scores_by_team is None:
            return None, None
        return grade_game_market_pick(pick, scores_by_team)

    if pick["market_type"] != "player_prop" or not pick.get("player_id") or not pick.get("stat"):
        return None, None
    player = players_by_id.get(pick["player_id"])
    if not player:
        return None, None
    actual = player.get("weekly_actuals", {}).get(pick["week"])
    if not actual:
        return None, None

    actual_value = _actual_td_count(pick["stat"], player["position"], actual["stats"])
    if pick["stat"] == "anytime_td":
        return actual_value, _grade_anytime(actual_value)
    return actual_value, _grade_over_under(pick["direction"], pick["line_entered"], actual_value)


def auto_grade_pending_picks(players, week=None, scores_by_week=None):
    """Grades any "placed" picks for weeks we now have actual results for.
    Player props use `players`' weekly_actuals; moneyline/spread/total picks
    use `scores_by_week` ({week: scores_by_team}, built from
    espn_client.get_scoreboard per week) when supplied - omitting it grades
    only player-prop picks, same as before this parameter existed. Idempotent
    to re-run - already-graded picks have status graded_win/graded_loss/
    graded_push and won't be re-selected here since we only pull
    status="placed"."""
    players_by_id = {p["id"]: p for p in players}
    graded = 0
    for pick in storage.list_picks(status="placed"):
        if week is not None and pick.get("week") != week:
            continue
        scores_by_team = (scores_by_week or {}).get(pick.get("week"))
        actual_value, grade_result = grade_pick_against_actual(pick, players_by_id, scores_by_team)
        if grade_result is None:
            continue
        storage.update_pick(
            pick["id"],
            status=PICK_STATUS_FOR_GRADE[grade_result],
            actual_value=actual_value,
            graded_at=_now(),
        )
        graded += 1
    return graded


def auto_grade_pending_snapshots(players, season, week):
    """Grades model_snapshots rows for a specific (season, week) once actual
    results exist - this is what feeds `calibration_report`."""
    players_by_id = {p["id"]: p for p in players}
    graded = 0
    for snap in storage.list_ungraded_snapshots(season, week):
        player = players_by_id.get(snap["player_id"])
        if not player:
            continue
        actual = player.get("weekly_actuals", {}).get(snap["week"])
        if not actual:
            continue
        actual_value = _actual_td_count(snap["stat"], snap["position"], actual["stats"])
        grade_result = (
            _grade_anytime(actual_value) if snap["stat"] == "anytime_td"
            else _grade_over_under(snap["direction"], snap["line"], actual_value)
        )
        if grade_result:
            storage.grade_snapshot(snap["id"], actual_value, grade_result)
            graded += 1
    return graded


# ---------------------------------------------------------------------------
# Model calibration (uses model_snapshots - see caveat in module docstring)
# ---------------------------------------------------------------------------

def calibration_report(season=None):
    return storage.calibration_by_confidence_bucket(season)


# ---------------------------------------------------------------------------
# Tracked-picks performance (ROI / win rate breakdowns)
# ---------------------------------------------------------------------------

def picks_performance_summary():
    all_picks = storage.list_picks()
    graded_picks = [p for p in all_picks if p["status"] in PICK_STATUS_FOR_GRADE.values()]

    def bucket_by(key_fn):
        buckets = {}
        for p in graded_picks:
            key = key_fn(p)
            if key is None:
                continue
            b = buckets.setdefault(key, {"total": 0, "wins": 0, "losses": 0, "pushes": 0})
            b["total"] += 1
            if p["status"] == "graded_win":
                b["wins"] += 1
            elif p["status"] == "graded_loss":
                b["losses"] += 1
            else:
                b["pushes"] += 1
        return [
            {
                "key": k, **v,
                "win_rate": round(v["wins"] / (v["wins"] + v["losses"]), 4) if (v["wins"] + v["losses"]) else None,
            }
            for k, v in sorted(buckets.items())
        ]

    by_market_type = bucket_by(lambda p: p["market_type"])
    by_direction = bucket_by(lambda p: p["direction"])
    by_team = bucket_by(lambda p: p["team"])

    # Parlay-group level: a group's outcome is derived from its legs (a
    # parlay loses if any leg lost; otherwise wins if every leg won or
    # pushed). Still-pending groups (any leg not yet graded) are excluded.
    leg_count_buckets = {}
    total_staked = 0.0
    total_profit = 0.0
    for g in storage.list_parlay_groups():
        detail = storage.get_parlay_group(g["id"])
        picks = detail["picks"]
        if not picks:
            continue
        statuses = [p["status"] for p in picks]
        if any(s in ("considered", "placed") for s in statuses):
            continue
        outcome = "loss" if any(s == "graded_loss" for s in statuses) else "win"

        leg_count = len(picks)
        b = leg_count_buckets.setdefault(leg_count, {"total": 0, "wins": 0, "losses": 0})
        b["total"] += 1
        b[f"{outcome}s"] += 1

        stake = g.get("total_stake")
        odds_list = [p["odds_american"] for p in picks if p["odds_american"] is not None]
        if stake and odds_list:
            combined_decimal = odds_math.combined_decimal_odds(odds_list)
            leg_profit = odds_math.profit(stake, combined_decimal) if outcome == "win" else -stake
            total_staked += stake
            total_profit += leg_profit

    by_leg_count = [
        {"leg_count": k, **v, "win_rate": round(v["wins"] / v["total"], 4) if v["total"] else None}
        for k, v in sorted(leg_count_buckets.items())
    ]

    return {
        "by_market_type": by_market_type,
        "by_direction": by_direction,
        "by_team": by_team,
        "by_leg_count": by_leg_count,
        "total_staked": round(total_staked, 2),
        "total_profit": round(total_profit, 2),
        "roi_pct": round(total_profit / total_staked, 4) if total_staked else None,
        "graded_picks_count": len(graded_picks),
        "graded_groups_count": sum(b["total"] for b in leg_count_buckets.values()),
    }


# ---------------------------------------------------------------------------
# Mechanical backtest (see caveat in module docstring)
# ---------------------------------------------------------------------------

def backtest_mechanical(players, stat, threshold_pct, direction="Over",
                         position=None, start_week=4, end_week=17, min_trailing_games=3):
    """For every player-week in [start_week, end_week], sets a hypothetical
    line at `threshold_pct` of that player's trailing average using only
    games played *before* that week (no lookahead), then checks whether the
    actual result would have cleared it.
    """
    results = []
    for p in players:
        if position and p["position"] != position:
            continue
        actuals = p.get("weekly_actuals", {})
        if not actuals:
            continue

        for week in range(start_week, end_week + 1):
            this_week = actuals.get(week)
            if not this_week:
                continue
            prior_weeks = [w for w in actuals if w < week]
            if len(prior_weeks) < min_trailing_games:
                continue

            trailing_avg = sum(actuals[w]["stats"][stat] for w in prior_weeks) / len(prior_weeks)
            line = round(trailing_avg * threshold_pct * 2) / 2  # nearest 0.5, matches parlay_engine's convention
            if line <= 0:
                continue

            actual_value = this_week["stats"].get(stat)
            grade = _grade_over_under(direction, line, actual_value)
            if grade is None:
                continue
            results.append({
                "player_id": p["id"], "player": p["name"], "week": week,
                "line": line, "actual": actual_value, "grade": grade,
            })

    hits = sum(1 for r in results if r["grade"] == GRADE_HIT)
    misses = sum(1 for r in results if r["grade"] == GRADE_MISS)
    pushes = sum(1 for r in results if r["grade"] == GRADE_PUSH)
    decided = hits + misses  # pushes don't count toward hit rate either way

    return {
        "stat": stat,
        "direction": direction,
        "threshold_pct": threshold_pct,
        "position": position,
        "week_range": [start_week, end_week],
        "sample_size": len(results),
        "hits": hits,
        "misses": misses,
        "pushes": pushes,
        "hit_rate": round(hits / decided, 4) if decided else None,
    }
