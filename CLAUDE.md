# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A local, single-user web app for building and evaluating NFL prop parlays. It pulls
projections/schedule/injuries from ESPN's undocumented public fantasy API (plus real
final scores from a second ESPN API, real usage stats from nflverse, and optionally
real market odds), scores player-prop parlays with a variance model, lets the user
track real sportsbook odds against those or their own picks, flags correlated/
conflicting legs, surfaces injury/usage/weather risk, and grades outcomes for
backtesting and model calibration. No accounts, no external database, zero pip
dependencies (Python stdlib only - see "External data sources" below for why that
constraint shaped a couple of non-obvious design choices).

## Commands

Run the app:
```
python server.py
```
Then open http://localhost:8787. `PORT` and `HOST` env vars override the defaults
(`127.0.0.1:8787`) - used by the Docker setup, see below. `ODDS_API_KEY` is optional
(a free key from the-odds-api.com) - the app runs identically without it, just without
real market odds (see `odds_client.py`).

Run tests (unittest, not pytest):
```
python -m unittest discover -s tests          # all tests
python -m unittest tests.test_odds_math -v    # a single module
```

Docker:
```
docker compose up --build
```
`cache/` and `data/` are bind-mounted so the ESPN response cache and the tracked-picks
sqlite db survive a rebuild. Published on `127.0.0.1:8787` by default in
`docker-compose.yml` - the app has no auth, so don't drop that loopback prefix unless
you actually want it reachable from other machines.

There is no build step, linter, or formatter configured.

## Architecture

### Data flow

`espn_client.py` is the only module that talks to ESPN, across **two distinct,
unauthenticated host families**: `lm-api-reads.fantasy.espn.com` (the fantasy API -
`get_projections`, `get_current_week`, `get_schedule`) and `site.api.espn.com` (general
sports scores, used only by `get_scoreboard` for real final scores). These two hosts
have opposite User-Agent requirements, discovered the hard way: the fantasy host wants
an explicit custom UA (`_get`), while the scoreboard host 403s any request carrying a
custom UA at all - reproducibly, regardless of value - and only works with urllib's own
default (`_get_no_ua`). Don't "clean up" that apparent inconsistency; it's load-bearing.
Everything else in the app consumes the normalized player dicts this module returns -
no other module makes ESPN requests. Responses are cached to `cache/*.json` per-season
(so backtesting a past season never clobbers the live current-season cache) via
`cache_io.py`'s atomic write (temp file + `os.replace`, since request-handler threads
and the background refresh loop can write concurrently).

**Season-long vs. per-week data**: ESPN only publishes real per-week projections a few
days before that week's games. Until then, `get_week_stats()` falls back to
`season_total / 17` as a per-game estimate. Every consumer (`parlay_engine.py`) tags
which source was used (`weekly_projection` vs `season_pace_estimate`) rather than
hiding the distinction.

**Int-keyed dicts through the JSON cache**: `weekly_projections`/`weekly_actuals` are
`{week_number: {...}}` dicts. JSON has no integer-keyed-object concept, so every
round-trip through the cache file turns those keys into strings. `get_projections()`
normalizes them back to `int` on every read (`_normalize_week_keys`) - if you add a new
per-week dict to the player record, you need to normalize it there too, or lookups will
silently miss on any cache hit (this bit the app once already: see git history around
`history.py`'s introduction).

### External data sources beyond ESPN

- `nflverse_client.py` - real snap %/target share/air yards share/WOPR, fetched as
  plain CSVs from `github.com/nflverse/nflverse-data`'s GitHub release assets via
  stdlib `urllib`/`csv` - **not** the `nfl_data_py` pip package, which pulls parquet
  via pandas/pyarrow and would break the zero-dependency constraint. Joins to ESPN
  player records through **exact ids** (`player_stats_{season}.csv`'s `gsis_id` and
  `snap_counts_{season}.csv`'s `pfr_player_id`, both resolved to `espn_id` via
  `players.csv`'s crosswalk) - no fuzzy name/team matching anywhere, confirmed live
  that both id columns are actually populated. **nflverse's publication cadence lags
  a live season independent of this app's `SEASON` constant** - a season nflverse
  hasn't published yet is a normal, expected state (`{"available": True, "players":
  {}}`, not an error), distinguished from a genuine fetch failure via the `reason`
  field on the underlying per-file fetchers.
- `odds_client.py` - real consensus moneylines/spreads/totals from The Odds API
  (`the-odds-api.com`), gated behind an optional `ODDS_API_KEY` env var - returns
  `{"available": False, "reason": "no_api_key"}` with zero network calls when unset,
  so this integration is fully optional. Collapses multiple bookmakers to one
  consensus price via median implied probability, not a raw American-odds average
  (which isn't linear). **Only ever call `get_odds(force_refresh=True)` from the
  background refresh loop** - the free tier is 500 credits/month, and a request
  handler calling it is a cache-read (free) by design, never a trigger for a live
  fetch. Each of the three external-data modules (`espn_client`'s `PRO_TEAM_ABBR`,
  `nflverse_client`'s `NFLVERSE_TEAM_ABBR`, `odds_client`'s `ODDS_API_TEAM_ABBR`)
  normalizes team names to ESPN's abbreviation dialect at its own boundary - nothing
  downstream (`correlation.py` etc.) should ever see a non-ESPN abbreviation.

### Two kinds of "backtesting" - don't conflate them

`history.py` deliberately separates two different validations, and the History tab UI
labels them separately:
1. **Mechanical backtest** (`backtest_mechanical`) - replays a fixed rule ("bet the
   over at X% of trailing average, no lookahead") against ESPN's real historical actual
   stats. Works for any completed season, today, independent of this app's model.
2. **Model calibration** (`calibration_report`) - checks whether *this app's own*
   generated probabilities were accurate, using `model_snapshots` rows the background
   refresh loop writes every cycle (`server.py:_snapshot_current_week_legs`). This can
   only ever cover weeks since that logging started, because ESPN exposes no
   historical point-in-time projections to check against retroactively.

Grading itself (`grade_pick_against_actual`) covers all four market types now:
player props against `weekly_actuals` (unchanged), and moneyline/spread/total against
real final scores (`espn_client.get_scoreboard`, passed in as `scores_by_team` -
`history.py` stays network-free, a pure function of whatever's passed in). Spread and
total both reduce to the same `_grade_over_under` helper player props use (spread via
a margin/sign-flip transform - see `grade_game_market_pick`'s docstring for the exact
convention `team`/`direction`/`line_entered` are expected to hold per market type).

### Storage

`storage.py` wraps a local sqlite db (`data/parlay_advisor.db`, gitignored) with three
tables, deliberately separate from the ESPN response cache in `cache/`:
- `picks` - user-tracked bets (player props, moneylines, spreads, totals), low-volume,
  user-edited.
- `parlay_groups` - ties multiple `picks` into one real slip with a stake, for
  ticket-level EV.
- `model_snapshots` - every leg the engine generates, written automatically
  (high-volume, append-only/immutable once graded) for calibration tracking. Kept in a
  separate table from `picks` specifically so calibration queries don't need to filter
  out user data and vice versa.

Concurrency: a fresh `sqlite3.connect()` per call (not a shared connection) with WAL
mode + `busy_timeout`, since `ThreadingHTTPServer` gives every request its own thread.
Schema changes go through `MIGRATIONS` (a list of `(version, [sql...])` tuples applied
against `PRAGMA user_version`), with a timestamped backup of the db file before any
migration runs.

### Server routing

`server.py` has no framework - `BaseHTTPRequestHandler` subclass with routes registered
via a `@route(method, path_regex)` decorator into a module-level `ROUTES` list, matched
in `_dispatch()`. Static files are the fallback when no API route matches. Raise
`ApiError(message, status=...)` from a handler for an intentional 4xx; anything else
that escapes becomes a 502 with a server-side traceback (so it's debuggable, not a
silent black box). Use `_query_int`/`_query_float` for query-param parsing so bad input
becomes a 400 rather than an uncaught `ValueError`.

### Cross-cutting logic modules (pure, no I/O)

- `odds_math.py` - American/decimal/implied-probability conversions, parlay combined
  odds, payout, EV, risk score. Shared by model-generated parlays (`parlay_engine.py`)
  and tracked picks (`server.py`'s `/api/parlay-groups/{id}/ev`) - don't duplicate this
  math in either caller.
- `correlation.py` - takes a *normalized* leg list (see `from_model_leg`/`from_pick`
  adapters - model legs and tracked picks have different native shapes) plus schedule
  data, and returns correlation warnings + a game-script summary. `attach_opponents()`
  must run before `analyze_correlations()`/`generate_game_script_summary()` since same-
  game detection depends on it. `generate_game_script_summary`'s priority order is (a)
  a manually-tracked pick for that game > (b) real auto market odds
  (`market_odds_by_game`, from `odds_client.get_odds()`) > (c) roster-strength
  speculation > (d) neutral unknown - it's narrative-only (never feeds EV/grading
  math), so when (a) and (b) disagree it surfaces a caveat rather than reconciling them.
- `weather.py` - static 32-team stadium/roof table + Open-Meteo forecasts for outdoor
  stadiums only (dome/retractable never hits the network). Forecasts beyond ~16 days
  out are reported `forecast_reliable: False` rather than guessed.

### Frontend

Vanilla JS/CSS, no build step, no framework. `static/common.js` holds shared
fetch/format helpers and tab-switching; one file per tab (`parlays.js`, `picks.js`,
`dashboard.js`, `history.js`) listens for a `tabshown` custom event to lazy-load its
data rather than fetching on page load. Scripts are loaded in dependency order in
`index.html` and rely on globals rather than modules/bundling.

### `_bmad/` and `.claude/skills/bmad-*`

BMad-method planning-workflow tooling (agents, skills) installed into this repo for
planning/process work - not part of the application itself. Ignore unless a task
specifically involves the BMad workflow.
