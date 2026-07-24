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

import espn_client
import parlay_engine
import storage

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

        legs = parlay_engine.build_legs(data["players"], week, risk=risk, position_filter=position)
        parlays = parlay_engine.build_parlays(legs, num_legs=num_legs)
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


def _background_refresh_loop():
    """Keeps the projections cache warm on its own, so the app has fresh data
    even if nobody happens to visit right after ESPN publishes a new week's
    projections. Just a timer loop - no external scheduler needed."""
    while True:
        time.sleep(REFRESH_INTERVAL_SECONDS)
        try:
            espn_client.get_projections(SEASON, force_refresh=True)
            print("Background refresh: projections cache updated")
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
