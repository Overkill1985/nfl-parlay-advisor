"""Rule-based correlation and game-script warnings for a set of parlay legs.

Nothing here estimates real joint probabilities - the combined-probability
math elsewhere assumes independence between legs, which is often false (a QB
and his own WR, two legs from the same game, etc). This module just flags
those relationships so the user can see past the naive product-of-probabilities
number. Legs are normalized into plain dicts - see `from_model_leg` and
`from_pick` for the two adapters used by parlay_engine.py and server.py.
"""

PASS_CATCHER_STATS = {"rec_yds", "receptions"}
QB_STATS = {"pass_yds", "pass_td"}
RUSH_STATS = {"rush_yds"}
TD_STAT = "anytime_td"
PASS_LIKE_STATS = QB_STATS | PASS_CATCHER_STATS | {TD_STAT}


def from_model_leg(leg):
    """Normalizes a parlay_engine.py leg dict (player_id, player, team, stat,
    label, direction, line, week, ...) into the shape analyze_correlations
    expects."""
    label = leg.get("label", "")
    if leg.get("stat") == "anytime_td":
        desc = f"{leg['player']} {label}".strip()
    else:
        desc = f"{leg['player']} {leg.get('direction', '')} {leg.get('line', '')} {label}".strip()
    return {
        "id": leg.get("player_id"),
        "description": desc,
        "team": leg.get("team"),
        "opponent": None,
        "week": leg.get("week"),
        "stat": leg.get("stat"),
        "market_type": "player_prop",
        "direction": leg.get("direction"),
        "odds_american": None,
        "line_entered": leg.get("line"),
    }


def from_pick(pick):
    """Normalizes a storage.py `picks` row into the shape analyze_correlations
    expects - covers player props as well as moneyline/spread/total picks,
    which model-generated legs never include."""
    if pick.get("player_name"):
        desc = f"{pick['player_name']} {pick.get('direction') or ''} {pick.get('line_entered') or ''} {pick.get('label') or ''}".strip()
    else:
        desc = f"{pick.get('team') or ''} {pick.get('market_type') or ''} {pick.get('direction') or ''} {pick.get('line_entered') or ''}".strip()
    return {
        "id": pick.get("id"),
        "description": desc,
        "team": pick.get("team"),
        "opponent": pick.get("opponent"),
        "week": pick.get("week"),
        "stat": pick.get("stat"),
        "market_type": pick.get("market_type"),
        "direction": pick.get("direction"),
        "odds_american": pick.get("odds_american"),
        "line_entered": pick.get("line_entered"),
    }


def attach_opponents(legs, schedule):
    """Fills in `opponent` on each leg from ESPN schedule data (the full
    espn_client.get_schedule() result) using that leg's own `week`, where not
    already known. Mutates and returns `legs`."""
    weeks = (schedule or {}).get("weeks", {})
    for leg in legs:
        if leg.get("opponent") or not leg.get("team") or not leg.get("week"):
            continue
        team_info = weeks.get(str(leg["week"]), {}).get(leg["team"])
        if team_info:
            leg["opponent"] = team_info["opponent"]
    return legs


def _game_key(leg):
    if not leg.get("team"):
        return None
    if leg.get("opponent"):
        return frozenset((leg["team"], leg["opponent"]))
    return frozenset((leg["team"],))  # opponent unknown - still groups same-team legs


def analyze_correlations(legs):
    """Returns a list of {type, severity ("info"/"caution"), message} warnings."""
    warnings = []
    by_team = {}
    for leg in legs:
        if leg.get("team"):
            by_team.setdefault(leg["team"], []).append(leg)

    for team, team_legs in by_team.items():
        qb_legs = [l for l in team_legs if l.get("stat") in QB_STATS]
        catcher_legs = [l for l in team_legs if l.get("stat") in PASS_CATCHER_STATS]
        if qb_legs and catcher_legs:
            warnings.append({
                "type": "positive_stack",
                "severity": "info",
                "message": (
                    f"{team}: {qb_legs[0]['description']} and {catcher_legs[0]['description']} "
                    "are positively correlated - both need the same passing game to go well."
                ),
            })

        td_legs = [l for l in team_legs if l.get("stat") == TD_STAT]
        if len(td_legs) >= 2:
            names = " and ".join(l["description"] for l in td_legs)
            warnings.append({
                "type": "competing_td",
                "severity": "caution",
                "message": f"{team}: {names} are competing for the same limited number of touchdowns.",
            })

        rush_legs = [l for l in team_legs if l.get("stat") in RUSH_STATS]
        favored_legs = [
            l for l in team_legs
            if (l.get("market_type") == "moneyline" and (l.get("odds_american") or 0) < 0)
            or (l.get("market_type") == "spread" and (l.get("line_entered") or 0) < 0)
        ]
        if rush_legs and favored_legs:
            warnings.append({
                "type": "favored_rush_stack",
                "severity": "info",
                "message": (
                    f"{team}: being favored ({favored_legs[0]['description']}) tends to support "
                    f"{rush_legs[0]['description']} - teams protecting a lead tend to run more."
                ),
            })

        pass_like_legs = [l for l in team_legs if l.get("stat") in PASS_LIKE_STATS]
        under_totals = [
            l for l in team_legs
            if l.get("market_type") == "total" and l.get("direction") == "Under"
        ]
        if pass_like_legs and under_totals:
            warnings.append({
                "type": "total_conflict",
                "severity": "caution",
                "message": (
                    f"{team}: {pass_like_legs[0]['description']} may conflict with "
                    f"{under_totals[0]['description']} - a low-scoring game works against passing/TD props."
                ),
            })

    game_groups = {}
    for leg in legs:
        key = _game_key(leg)
        if key and len(key) == 2:  # only count games where both sides are known
            game_groups.setdefault(key, []).append(leg)
    for key, game_legs in game_groups.items():
        if len(game_legs) >= 3:
            teams = " vs ".join(sorted(key))
            warnings.append({
                "type": "game_concentration",
                "severity": "caution",
                "message": (
                    f"{len(game_legs)} of your legs all depend on the {teams} game - "
                    "a single bad break there (blowout, injury, weather) sinks the whole ticket."
                ),
            })

    return warnings


def generate_game_script_summary(legs, team_strength=None, market_odds_by_game=None):
    """One entry per distinct real-world game represented in `legs`. Priority
    order: (a) a manually-tracked moneyline/spread/total pick for that game -
    the user's own real bet, most specific; (b) automatic real market odds
    from `market_odds_by_game` (odds_client.get_odds()'s "games" dict,
    {frozenset({team_a, team_b}): {favorite, spread, total}}) when no manual
    pick exists; (c) a clearly-labeled speculative note from relative roster
    strength (`team_strength`: {team_abbr: avg_projected_points}); (d) a
    neutral "unknown" note if nothing is available.

    This function only ever produces narrative text - it never feeds EV or
    grading math (those use a pick's own odds_american directly). So when a
    manual pick and auto market odds exist and disagree on the favorite, we
    don't try to reconcile them into one number: the manual pick's sentence
    stays primary (it's the user's real, already-placed bet) with a short
    caveat appended, surfacing that the market has since moved rather than
    silently masking the drift.
    """
    games = {}
    for leg in legs:
        key = _game_key(leg)
        if key and len(key) == 2:
            games.setdefault(key, []).append(leg)

    market_odds_by_game = market_odds_by_game or {}

    summaries = []
    for key, game_legs in games.items():
        teams = sorted(key)
        favored_pick = next(
            (l for l in game_legs if (
                (l.get("market_type") == "moneyline" and (l.get("odds_american") or 0) < 0)
                or (l.get("market_type") == "spread" and (l.get("line_entered") or 0) < 0)
            )), None
        )
        total_pick = next((l for l in game_legs if l.get("market_type") == "total"), None)
        game_label = f"{teams[0]} vs {teams[1]}"
        auto_market = market_odds_by_game.get(key)

        if favored_pick:
            sentence = f"{favored_pick['team']} are the favorite in this game (per your entered odds)"
            if total_pick and total_pick.get("line_entered") is not None:
                sentence += f", with the total at {total_pick['direction']} {total_pick['line_entered']}"
            sentence += f" - this parlay leans on {favored_pick['team']} controlling {game_label}."
            if auto_market and auto_market.get("favorite") and auto_market["favorite"] != favored_pick["team"]:
                sentence += (
                    f" Note: the market now favors {auto_market['favorite']} instead - "
                    "your line may have moved since you entered it."
                )
            summaries.append({"game": game_label, "summary": sentence, "basis": "market_manual"})
        elif auto_market and auto_market.get("favorite"):
            sentence = f"{auto_market['favorite']} are the market favorite in this game"
            total_info = auto_market.get("total")
            if total_info and total_info.get("point") is not None:
                sentence += f", with the total set around {total_info['point']}"
            sentence += f" - this parlay leans on {auto_market['favorite']} controlling {game_label}."
            summaries.append({"game": game_label, "summary": sentence, "basis": "market_auto"})
        elif team_strength and all(t in team_strength for t in teams):
            stronger = max(teams, key=lambda t: team_strength[t])
            summaries.append({
                "game": game_label,
                "summary": (
                    "No market line entered for this game - based on roster strength only, "
                    f"{stronger} projects as the stronger fantasy roster, but this is a weak signal, "
                    "not a real odds read."
                ),
                "basis": "roster_strength_speculative",
            })
        else:
            summaries.append({
                "game": game_label,
                "summary": f"{game_label} - no market line entered, game-script direction unknown.",
                "basis": "none",
            })

    return summaries
