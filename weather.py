"""Game-day weather via Open-Meteo (free, no API key) for outdoor NFL
stadiums, plus a static per-team stadium/roof reference table.

Forecasts are only meaningful a matter of days out - Open-Meteo's free
forecast endpoint reliably covers roughly the next 16 days. Anything further
out is reported as forecast_reliable=False rather than a stale or fabricated
number. Stadium coordinates/roof types are best-effort static data as of
this writing and may need updating after a stadium change (e.g. a team
relocating to a new venue).
"""
import datetime
import json
import os
import time
import urllib.parse
import urllib.request

from cache_io import atomic_write_json

CACHE_PATH = os.path.join(os.path.dirname(__file__), "cache", "weather.json")
CACHE_TTL_SECONDS = 4 * 60 * 60  # 4 hours - forecasts drift, shouldn't go stale slowly
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
MAX_FORECAST_DAYS = 16

# roof: "outdoor" (live weather matters), "dome" (fully enclosed, never
# affected), "retractable" (closes in bad weather - treated like a dome for
# risk-flagging purposes, since a closed roof means the forecast doesn't
# matter regardless of what it says outside).
STADIUMS = {
    "ARI": {"lat": 33.5276, "lon": -112.2626, "roof": "retractable", "surface": "grass"},
    "ATL": {"lat": 33.7554, "lon": -84.4008, "roof": "retractable", "surface": "turf"},
    "BAL": {"lat": 39.2780, "lon": -76.6227, "roof": "outdoor", "surface": "grass"},
    "BUF": {"lat": 42.7738, "lon": -78.7870, "roof": "outdoor", "surface": "turf"},
    "CAR": {"lat": 35.2258, "lon": -80.8528, "roof": "outdoor", "surface": "grass"},
    "CHI": {"lat": 41.8623, "lon": -87.6167, "roof": "outdoor", "surface": "grass"},
    "CIN": {"lat": 39.0955, "lon": -84.5161, "roof": "outdoor", "surface": "turf"},
    "CLE": {"lat": 41.5061, "lon": -81.6995, "roof": "outdoor", "surface": "grass"},
    "DAL": {"lat": 32.7473, "lon": -97.0945, "roof": "retractable", "surface": "turf"},
    "DEN": {"lat": 39.7439, "lon": -105.0201, "roof": "outdoor", "surface": "grass"},
    "DET": {"lat": 42.3400, "lon": -83.0456, "roof": "dome", "surface": "turf"},
    "GB": {"lat": 44.5013, "lon": -88.0622, "roof": "outdoor", "surface": "grass"},
    "HOU": {"lat": 29.6847, "lon": -95.4107, "roof": "retractable", "surface": "turf"},
    "IND": {"lat": 39.7601, "lon": -86.1639, "roof": "retractable", "surface": "turf"},
    "JAX": {"lat": 30.3239, "lon": -81.6373, "roof": "outdoor", "surface": "grass"},
    "KC": {"lat": 39.0489, "lon": -94.4839, "roof": "outdoor", "surface": "grass"},
    "LAC": {"lat": 33.9535, "lon": -118.3390, "roof": "dome", "surface": "turf"},
    "LAR": {"lat": 33.9535, "lon": -118.3390, "roof": "dome", "surface": "turf"},
    "LV": {"lat": 36.0909, "lon": -115.1833, "roof": "dome", "surface": "grass"},
    "MIA": {"lat": 25.9580, "lon": -80.2389, "roof": "outdoor", "surface": "grass"},
    "MIN": {"lat": 44.9735, "lon": -93.2575, "roof": "dome", "surface": "turf"},
    "NE": {"lat": 42.0909, "lon": -71.2643, "roof": "outdoor", "surface": "turf"},
    "NO": {"lat": 29.9511, "lon": -90.0812, "roof": "dome", "surface": "turf"},
    "NYG": {"lat": 40.8135, "lon": -74.0745, "roof": "outdoor", "surface": "turf"},
    "NYJ": {"lat": 40.8135, "lon": -74.0745, "roof": "outdoor", "surface": "turf"},
    "PHI": {"lat": 39.9008, "lon": -75.1675, "roof": "outdoor", "surface": "grass"},
    "PIT": {"lat": 40.4468, "lon": -80.0158, "roof": "outdoor", "surface": "grass"},
    "SEA": {"lat": 47.5952, "lon": -122.3316, "roof": "outdoor", "surface": "turf"},
    "SF": {"lat": 37.4033, "lon": -121.9694, "roof": "outdoor", "surface": "grass"},
    "TB": {"lat": 27.9759, "lon": -82.5033, "roof": "outdoor", "surface": "grass"},
    "TEN": {"lat": 36.1665, "lon": -86.7713, "roof": "outdoor", "surface": "grass"},
    "WSH": {"lat": 38.9076, "lon": -76.8645, "roof": "outdoor", "surface": "grass"},
}

# Rough thresholds for flagging a prop-relevant weather risk - not a
# precision model, just a heads-up that conditions could matter.
WIND_MPH_WARNING = 15
PRECIP_IN_WARNING = 0.1
COLD_F_WARNING = 25


def _load_cache():
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_cache(cache):
    atomic_write_json(CACHE_PATH, cache)


def _flag_impact(forecast):
    flags = []
    if forecast["wind_mph"] is not None and forecast["wind_mph"] >= WIND_MPH_WARNING:
        flags.append(f"Wind {forecast['wind_mph']:.0f} mph - may affect passing yards, "
                      "longest field goal, and receiving-yard overs.")
    if forecast["precip_in"] is not None and forecast["precip_in"] >= PRECIP_IN_WARNING:
        flags.append(f"Precipitation expected ({forecast['precip_in']:.2f} in) - can affect "
                      "ball security, passing volume, and the game total.")
    if forecast["temp_f"] is not None and forecast["temp_f"] <= COLD_F_WARNING:
        flags.append(f"Cold ({forecast['temp_f']:.0f}°F) - historically correlates with "
                      "lower passing volume and totals.")
    return flags


def get_forecast(team, game_date_iso, force_refresh=False):
    """Weather info for `team`'s game on `game_date_iso` (ISO datetime string,
    as produced by espn_client.get_schedule). For dome/retractable-roof teams
    this never makes a network call - it's a controlled environment
    regardless of conditions outside.
    """
    stadium = STADIUMS.get(team)
    if not stadium:
        return {"team": team, "roof": "unknown", "conditions": "unknown", "forecast_reliable": False}

    if stadium["roof"] != "outdoor":
        return {
            "team": team, "roof": stadium["roof"], "surface": stadium["surface"],
            "conditions": "controlled", "forecast_reliable": True,
            "wind_mph": None, "precip_in": None, "temp_f": None,
            "impact_flags": [],
        }

    if not game_date_iso:
        return {"team": team, "roof": stadium["roof"], "conditions": "unknown", "forecast_reliable": False}

    game_dt = datetime.datetime.fromisoformat(game_date_iso.replace("Z", "+00:00")).replace(tzinfo=None)
    date_str = game_dt.date().isoformat()
    today = datetime.datetime.now(datetime.timezone.utc).date()
    days_out = (game_dt.date() - today).days
    forecast_reliable = 0 <= days_out <= MAX_FORECAST_DAYS

    cache_key = f"{team}:{date_str}"
    cache = _load_cache()
    cached_entry = cache.get(cache_key)
    if not force_refresh and cached_entry and time.time() - cached_entry.get("fetched_at", 0) < CACHE_TTL_SECONDS:
        return cached_entry["data"]

    result = {
        "team": team, "roof": stadium["roof"], "surface": stadium["surface"],
        "forecast_reliable": forecast_reliable,
        "wind_mph": None, "precip_in": None, "temp_f": None,
        "conditions": "unknown", "impact_flags": [],
    }

    if forecast_reliable:
        try:
            params = urllib.parse.urlencode({
                "latitude": stadium["lat"],
                "longitude": stadium["lon"],
                "hourly": "temperature_2m,precipitation,wind_speed_10m",
                "start_date": date_str,
                "end_date": date_str,
                "temperature_unit": "fahrenheit",
                "wind_speed_unit": "mph",
                "precipitation_unit": "inch",
                "timezone": "UTC",
            })
            with urllib.request.urlopen(f"{FORECAST_URL}?{params}", timeout=10) as resp:
                payload = json.loads(resp.read().decode("utf-8"))

            hourly = payload.get("hourly", {})
            times = hourly.get("time", [])
            target_hour = game_dt.replace(minute=0, second=0, microsecond=0).isoformat()
            if target_hour in times:
                idx = times.index(target_hour)
            elif times:
                idx = min(range(len(times)),
                          key=lambda i: abs(datetime.datetime.fromisoformat(times[i]) - game_dt))
            else:
                idx = None

            if idx is not None:
                result["temp_f"] = hourly["temperature_2m"][idx]
                result["precip_in"] = hourly["precipitation"][idx]
                result["wind_mph"] = hourly["wind_speed_10m"][idx]
                result["conditions"] = "forecast"
                result["impact_flags"] = _flag_impact(result)
        except Exception as exc:
            result["conditions"] = "fetch_failed"
            result["error"] = str(exc)

    cache[cache_key] = {"fetched_at": time.time(), "data": result}
    _save_cache(cache)
    return result
