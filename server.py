"""Local dev server for the NFL Fantasy Parlay Advisor.

Run with: python server.py
Then open http://localhost:8787
"""
import json
import mimetypes
import os
import re
import threading
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import correlation
import espn_client
import history
import odds_math
import parlay_engine
import storage
import weather

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
SEASON = 2026
PORT = 8787
REFRESH_INTERVAL_SECONDS = espn_client.CACHE_TTL_SECONDS  # keep the cache from ever going stale


class ApiError(Exception):
    """Raise from a route handler to send a specific HTTP status + message,
    instead of everything falling through to a generic 502."""
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


def _team_strength(players):
    """Rough proxy for roster fantasy strength: total projected points/game
    across all rostered skill players on a team. Only used as a last-resort,
    clearly-labeled-speculative signal for game-script summaries when no
    market line (moneyline/spread) has been entered for that game."""
    strength = {}
    for p in players:
        strength[p["team"]] = strength.get(p["team"], 0.0) + p["projected_points_per_game"]
    return strength


def _describe_pick(pick):
    if pick["player_name"]:
        parts = [pick["player_name"]]
        if pick["direction"] and pick["line_entered"] is not None:
            parts.append(f"{pick['direction']} {pick['line_entered']}")
        elif pick["direction"]:
            parts.append(pick["direction"])
        if pick["label"]:
            parts.append(pick["label"])
        return " ".join(parts)
    if pick["team"] and pick["opponent"]:
        return f"{pick['team']} vs {pick['opponent']} ({pick['market_type']})"
    return pick["market_type"]


# Populated by @route as route handler methods are defined on Handler below.
# Each entry is (http_method, compiled_path_regex, handler_method_name).
ROUTES = []


def route(method, pattern):
    compiled = re.compile(pattern)

    def decorator(fn):
        ROUTES.append((method, compiled, fn.__name__))
        return fn

    return decorator


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args))

    # -- response helpers ---------------------------------------------------

    def _send_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path):
        if not os.path.isfile(path):
            self.send_error(404, "Not found")
            return
        ctype, _ = mimetypes.guess_type(path)
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ApiError(f"Invalid JSON body: {exc}", status=400)

    # -- dispatch -------------------------------------------------------

    def _dispatch(self, method):
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)

        for route_method, pattern, handler_name in ROUTES:
            if route_method != method:
                continue
            match = pattern.match(parsed.path)
            if not match:
                continue
            try:
                result = getattr(self, handler_name)(query=query, **match.groupdict())
                self._send_json(result if result is not None else {"ok": True})
            except ApiError as exc:
                self._send_json({"error": exc.message}, status=exc.status)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=502)
            return True
        return False

    def do_GET(self):
        if self._dispatch("GET"):
            return
        # No API route matched - serve a static file.
        parsed = urllib.parse.urlparse(self.path)
        rel_path = parsed.path.lstrip("/") or "index.html"
        file_path = os.path.normpath(os.path.join(STATIC_DIR, rel_path))
        if not file_path.startswith(STATIC_DIR):
            self.send_error(403)
            return
        self._send_file(file_path)

    def do_POST(self):
        if not self._dispatch("POST"):
            self.send_error(404, "Not found")

    def do_PUT(self):
        if not self._dispatch("PUT"):
            self.send_error(404, "Not found")

    def do_DELETE(self):
        if not self._dispatch("DELETE"):
            self.send_error(404, "Not found")

    # -- routes: projections / parlays --------------------------------------

    @route("GET", r"^/api/projections$")
    def get_projections(self, query):
        refresh = query.get("refresh", ["0"])[0] == "1"
        return espn_client.get_projections(SEASON, force_refresh=refresh)

    @route("GET", r"^/api/schedule$")
    def get_schedule_route(self, query):
        schedule = espn_client.get_schedule(SEASON)
        week = query.get("week", [None])[0]
        if week:
            return {
                "season": SEASON,
                "week": int(week),
                "matchups": schedule["weeks"].get(week, {}),
                "bye_weeks": schedule["bye_weeks"],
            }
        return schedule

    @route("GET", r"^/api/dashboard$")
    def get_dashboard(self, query):
        data = espn_client.get_projections(SEASON)
        current_week = data["current_week"]
        week = int(query.get("week", [current_week])[0])
        week = max(1, min(espn_client.MAX_WEEK, week))
        position = query.get("position", ["ALL"])[0]
        team = query.get("team", [None])[0]

        players = data["players"]
        if position != "ALL":
            players = [p for p in players if p["position"] == position]
        if team:
            players = [p for p in players if p["team"] == team.upper()]

        injuries = [
            {
                "player_id": p["id"], "name": p["name"], "team": p["team"], "position": p["position"],
                "injury_status": p["injury_status"], "injured": p["injured"],
            }
            for p in players if p["injury_status"] not in ("ACTIVE", "UNKNOWN")
        ]

        recent_form = []
        for p in players:
            form = espn_client.get_recent_form(p, week)
            if form["games_available"] > 0:
                recent_form.append({
                    "player_id": p["id"], "name": p["name"], "team": p["team"], "position": p["position"],
                    **form,
                })
        recent_form.sort(key=lambda r: r["avg_points"], reverse=True)

        schedule = espn_client.get_schedule(SEASON)
        week_map = schedule["weeks"].get(str(week), {})
        weather_reports = []
        seen_games = set()
        for team_abbr, info in week_map.items():
            if not info["is_home"]:
                continue
            game_key = frozenset((team_abbr, info["opponent"]))
            if game_key in seen_games:
                continue
            seen_games.add(game_key)
            forecast = weather.get_forecast(team_abbr, info["game_date"])
            weather_reports.append({
                "home_team": team_abbr, "away_team": info["opponent"], "game_date": info["game_date"],
                **forecast,
            })

        return {
            "season": SEASON,
            "week": week,
            "current_week": current_week,
            "position": position,
            "team": team,
            "injuries": injuries,
            "recent_form": recent_form[:100],
            "weather": weather_reports,
            "targets_stat_available": espn_client.TARGETS_STAT_CONFIRMED,
        }

    @route("GET", r"^/api/parlays$")
    def get_parlays(self, query):
        num_legs = int(query.get("legs", ["3"])[0])
        risk = query.get("risk", ["balanced"])[0]
        position = query.get("position", ["ALL"])[0]
        num_legs = max(2, min(4, num_legs))

        data = espn_client.get_projections(SEASON)
        current_week = data["current_week"]
        week = int(query.get("week", [current_week])[0])
        week = max(1, min(espn_client.MAX_WEEK, week))

        schedule = espn_client.get_schedule(SEASON)
        team_strength = _team_strength(data["players"])

        legs = parlay_engine.build_legs(data["players"], week, risk=risk, position_filter=position)
        parlays = parlay_engine.build_parlays(legs, num_legs=num_legs, schedule=schedule, team_strength=team_strength)
        weekly_leg_count = sum(1 for l in legs if l["source"] == "weekly_projection")
        return {
            "season": SEASON,
            "fetched_at": data["fetched_at"],
            "current_week": current_week,
            "week": week,
            "risk": risk,
            "position": position,
            "num_legs": num_legs,
            "leg_pool_size": len(legs),
            "legs_from_weekly_projection": weekly_leg_count,
            "legs_from_season_pace": len(legs) - weekly_leg_count,
            "parlays": parlays,
        }


    # -- routes: picks --------------------------------------------------

    NUMERIC_PICK_FIELDS = {
        "model_line", "model_probability", "odds_american", "opening_odds_american",
        "closing_odds_american", "line_entered", "stake", "result_profit_loss",
        "actual_value", "player_id", "source_snapshot_id", "parlay_group_id",
        "season", "week",
    }

    @staticmethod
    def _validate_pick_fields(fields):
        if "status" in fields and fields["status"] not in storage.PICK_STATUSES:
            raise ApiError(f"Invalid status. Must be one of {sorted(storage.PICK_STATUSES)}")
        if "market_type" in fields and fields["market_type"] not in storage.MARKET_TYPES:
            raise ApiError(f"Invalid market_type. Must be one of {sorted(storage.MARKET_TYPES)}")
        for key in Handler.NUMERIC_PICK_FIELDS & fields.keys():
            val = fields[key]
            if val is not None and not isinstance(val, (int, float)):
                raise ApiError(f"Field '{key}' must be a number, got {type(val).__name__}")

    @route("GET", r"^/api/picks$")
    def list_picks_route(self, query):
        filters = {}
        for key in ("status", "market_type"):
            if key in query:
                filters[key] = query[key][0]
        for key in ("season", "week", "parlay_group_id"):
            if key in query:
                filters[key] = int(query[key][0])
        return {"picks": storage.list_picks(**filters)}

    @route("POST", r"^/api/picks$")
    def create_pick_route(self, query):
        body = self._read_json_body()
        if "market_type" not in body:
            raise ApiError("market_type is required")
        self._validate_pick_fields(body)
        pick_id = storage.create_pick(**body)
        return storage.get_pick(pick_id)

    @route("GET", r"^/api/picks/(?P<pick_id>\d+)$")
    def get_pick_route(self, query, pick_id):
        pick = storage.get_pick(int(pick_id))
        if pick is None:
            raise ApiError("Pick not found", status=404)
        return pick

    @route("PUT", r"^/api/picks/(?P<pick_id>\d+)$")
    def update_pick_route(self, query, pick_id):
        body = self._read_json_body()
        self._validate_pick_fields(body)
        if not storage.update_pick(int(pick_id), **body):
            raise ApiError("Pick not found", status=404)
        return storage.get_pick(int(pick_id))

    @route("DELETE", r"^/api/picks/(?P<pick_id>\d+)$")
    def delete_pick_route(self, query, pick_id):
        if not storage.delete_pick(int(pick_id)):
            raise ApiError("Pick not found", status=404)
        return {"deleted": True}

    # -- routes: parlay groups -------------------------------------------

    @route("GET", r"^/api/parlay-groups$")
    def list_parlay_groups_route(self, query):
        return {"parlay_groups": storage.list_parlay_groups()}

    @route("POST", r"^/api/parlay-groups$")
    def create_parlay_group_route(self, query):
        body = self._read_json_body()
        stake = body.get("total_stake")
        if stake is not None and not isinstance(stake, (int, float)):
            raise ApiError("total_stake must be a number")
        group_id = storage.create_parlay_group(
            sportsbook=body.get("sportsbook"),
            total_stake=stake,
            notes=body.get("notes"),
        )
        return storage.get_parlay_group(group_id)

    @route("GET", r"^/api/parlay-groups/(?P<group_id>\d+)$")
    def get_parlay_group_route(self, query, group_id):
        group = storage.get_parlay_group(int(group_id))
        if group is None:
            raise ApiError("Parlay group not found", status=404)
        return group

    @route("PUT", r"^/api/parlay-groups/(?P<group_id>\d+)$")
    def update_parlay_group_route(self, query, group_id):
        body = self._read_json_body()
        stake = body.get("total_stake")
        if stake is not None and not isinstance(stake, (int, float)):
            raise ApiError("total_stake must be a number")
        allowed = {"sportsbook", "total_stake", "notes"}
        fields = {k: v for k, v in body.items() if k in allowed}
        if not storage.update_parlay_group(int(group_id), **fields):
            raise ApiError("Parlay group not found", status=404)
        return storage.get_parlay_group(int(group_id))

    @route("DELETE", r"^/api/parlay-groups/(?P<group_id>\d+)$")
    def delete_parlay_group_route(self, query, group_id):
        if not storage.delete_parlay_group(int(group_id)):
            raise ApiError("Parlay group not found", status=404)
        return {"deleted": True}

    @route("GET", r"^/api/parlay-groups/(?P<group_id>\d+)/ev$")
    def get_parlay_group_ev(self, query, group_id):
        group = storage.get_parlay_group(int(group_id))
        if group is None:
            raise ApiError("Parlay group not found", status=404)

        leg_results = []
        excluded = []
        odds_list = []
        probabilities_used = []

        for pick in group["picks"]:
            if pick["odds_american"] is None:
                excluded.append({"pick_id": pick["id"], "reason": "no odds entered yet"})
                continue
            implied = odds_math.implied_probability(pick["odds_american"])
            model_prob = pick["model_probability"]
            used_model = model_prob is not None
            probability_used = model_prob if used_model else implied

            odds_list.append(pick["odds_american"])
            probabilities_used.append(probability_used)
            leg_results.append({
                "pick_id": pick["id"],
                "description": _describe_pick(pick),
                "odds_american": pick["odds_american"],
                "implied_probability": round(implied, 4),
                "model_probability": model_prob,
                "probability_used": round(probability_used, 4),
                "used_model": used_model,
                "edge": round(probability_used - implied, 4) if used_model else 0.0,
            })

        if not odds_list:
            return {
                "group_id": group["id"], "legs": [], "excluded_legs": excluded,
                "error": "No picks with odds entered yet",
            }

        combined_decimal = odds_math.combined_decimal_odds(odds_list)
        combined_implied = 1 / combined_decimal
        combined_model_prob = 1.0
        for p in probabilities_used:
            combined_model_prob *= p

        stake = group.get("total_stake") or 100.0
        ev = odds_math.expected_value(combined_model_prob, combined_decimal, stake)

        schedule = espn_client.get_schedule(SEASON)
        normalized = correlation.attach_opponents(
            [correlation.from_pick(p) for p in group["picks"]], schedule
        )
        team_strength = _team_strength(espn_client.get_projections(SEASON)["players"])

        return {
            "group_id": group["id"],
            "legs": leg_results,
            "correlation_warnings": correlation.analyze_correlations(normalized),
            "game_script": correlation.generate_game_script_summary(normalized, team_strength),
            "excluded_legs": excluded,
            "num_legs": len(leg_results),
            "stake": stake,
            "combined_decimal_odds": round(combined_decimal, 3),
            "combined_american_odds": odds_math.decimal_to_american(combined_decimal),
            "combined_implied_probability": round(combined_implied, 4),
            "combined_model_probability": round(combined_model_prob, 4),
            "edge": round(combined_model_prob - combined_implied, 4),
            "potential_payout": round(odds_math.payout(stake, combined_decimal), 2),
            "potential_profit": round(odds_math.profit(stake, combined_decimal), 2),
            "expected_value": round(ev, 2) if ev is not None else None,
            "expected_value_pct": round(ev / stake, 4) if ev is not None and stake else None,
            "risk_score": odds_math.risk_score(probabilities_used),
            "all_legs_have_model_estimate": all(l["used_model"] for l in leg_results),
        }

    # -- routes: history / calibration / backtesting -------------------

    @route("POST", r"^/api/history/grade$")
    def grade_history_route(self, query):
        data = espn_client.get_projections(SEASON)
        current_week = data["current_week"]
        week = query.get("week", [None])[0]
        week = int(week) if week else None

        graded_snapshots = 0
        weeks_to_grade = [week] if week is not None else list(range(1, current_week))
        for w in weeks_to_grade:
            graded_snapshots += history.auto_grade_pending_snapshots(data["players"], SEASON, w)
        graded_picks = history.auto_grade_pending_picks(data["players"], week=week)

        return {"graded_snapshots": graded_snapshots, "graded_picks": graded_picks}

    @route("GET", r"^/api/history/stats$")
    def get_history_stats(self, query):
        season = query.get("season", [str(SEASON)])[0]
        return {
            "calibration": history.calibration_report(season=int(season)),
            "picks_performance": history.picks_performance_summary(),
        }

    @route("GET", r"^/api/history/export$")
    def get_history_export(self, query):
        return storage.export_all()

    @route("GET", r"^/api/backtest/mechanical$")
    def get_backtest_mechanical(self, query):
        backtest_season = int(query.get("season", [str(SEASON - 1)])[0])
        stat = query.get("stat", ["rush_yds"])[0]
        threshold_pct = float(query.get("threshold", ["0.75"])[0])
        direction = query.get("direction", ["Over"])[0]
        position = query.get("position", [None])[0]
        start_week = int(query.get("start_week", ["4"])[0])
        end_week = int(query.get("end_week", ["17"])[0])

        if stat not in espn_client.STAT_IDS:
            raise ApiError(f"Unknown stat '{stat}'. Must be one of {sorted(espn_client.STAT_IDS)}")
        if direction not in ("Over", "Under"):
            raise ApiError("direction must be 'Over' or 'Under'")

        backtest_data = espn_client.get_projections(backtest_season)
        result = history.backtest_mechanical(
            backtest_data["players"], stat, threshold_pct, direction=direction,
            position=position, start_week=start_week, end_week=end_week,
        )
        result["season"] = backtest_season
        return result


def _snapshot_current_week_legs(data, week):
    """Writes a default sweep of model-generated legs into model_snapshots
    for calibration tracking - this is what lets us eventually check "did
    our 60%-confidence legs actually hit ~60% of the time?" (history.py).
    Uses the default balanced/all-positions view; the snapshot doesn't need
    to cover every risk tier a user might pick, just a consistent baseline."""
    legs = parlay_engine.build_legs(data["players"], week, risk="balanced", position_filter="ALL")
    for leg in legs:
        storage.upsert_model_snapshot(
            season=SEASON, week=week,
            player_id=leg["player_id"], player_name=leg["player"], team=leg["team"],
            position=leg["position"], stat=leg["stat"], label=leg["label"],
            direction=leg["direction"], line=leg["line"],
            model_probability=leg["probability"], projected_avg=leg["projected_avg"],
            source=leg["source"], risk_tier="balanced",
        )
    return len(legs)


def _refresh_cycle(force_refresh):
    data = espn_client.get_projections(SEASON, force_refresh=force_refresh)
    current_week = data["current_week"]
    snapshot_count = _snapshot_current_week_legs(data, current_week)

    graded_snapshots = 0
    for week in range(1, current_week):
        graded_snapshots += history.auto_grade_pending_snapshots(data["players"], SEASON, week)
    graded_picks = history.auto_grade_pending_picks(data["players"])

    print(f"Refresh: projections updated, {snapshot_count} legs snapshotted for week "
          f"{current_week}, {graded_snapshots} snapshots and {graded_picks} picks graded")


def _background_refresh_loop():
    """Keeps the projections cache warm on its own, so the app has fresh data
    even if nobody happens to visit right after ESPN publishes a new week's
    projections. Also snapshots the current week's legs for calibration
    tracking, and auto-grades any prior weeks' picks/snapshots once actual
    results exist. Runs once immediately (so calibration data starts
    accumulating right away) then repeats on a timer - no external
    scheduler needed."""
    try:
        _refresh_cycle(force_refresh=False)
    except Exception:
        print("Initial refresh failed:")
        traceback.print_exc()

    while True:
        time.sleep(REFRESH_INTERVAL_SECONDS)
        try:
            _refresh_cycle(force_refresh=True)
        except Exception:
            print("Background refresh failed:")
            traceback.print_exc()


def main():
    storage.init_db()
    threading.Thread(target=_background_refresh_loop, daemon=True).start()

    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"NFL Parlay Advisor running at http://localhost:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
