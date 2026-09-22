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
sports data - `get_scoreboard` for real final scores, `get_injuries` for the
leaguewide live injury feed). These two hosts
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

### Parlay selection: spread across games (`_select_spread`)

`build_parlays` ranks every valid combination, then `_select_spread` picks the `top_n`
(default on; `spread_games=False` gives the plain top N). The plain top N was badly
clustered - measured on a real week, one game and one single leg each appeared in *all*
10 parlays and only 5 of 13 available games were covered, because the strongest legs win
nearly every combination they're in.

Two limits, both counted per chosen parlay (a game counts once per parlay however many of
its legs it holds): no leg (player+stat) in more than `leg_cap` parlays, no game in more
than `game_cap`. Parlays are taken strictly in rank order, so the best is always first and
`sort_by` still governs the ordering. Both limits start as tight as possible and loosen
only as far as needed to fill the list. Three things here were learned the hard way:

- **`game_cap` has a floor** of `ceil(top_n * min(legs_per_parlay, games) / games)` - the
  game-uses parlays inherently need (10 parlays x 3 legs over 13 games -> 3). With only a
  few games (sportsbook mode can have props for just 2) a parlay spanning both games uses
  *each* game once, so 10 of them use each game 10 times. An earlier floor of
  `ceil(top_n / games)` assumed one game per parlay and demanded the impossible.
- **The limits loosen independently, by total slack,** not as one shared cap. A shared cap
  had to rise to ~10 to satisfy the game limit and dragged single legs to 10 reuses (8
  distinct legs used of 24) while 16 were available. Few games must never force legs to
  be reused with them.
- **The test fixture must look like the real pool.** The bug only appeared when scarce
  prop types (1 anytime-TD leg, 2 passing legs) were also the strongest, so the top parlays
  all leaned on them; a fixture with evenly spread prop types passed the broken version.

`games_in_parlays` in the response (and the UI's summary line) reports coverage so it's
visible, not just trusted.

### Build-your-own parlays (the Build Parlay tab)

Same engine, one shared scorer. `parlay_engine.score_parlay(legs, ...)` is what
`build_parlays` runs on every combination and what `POST /api/builder/evaluate` runs on a
hand-built slip, so the two can never drift (a test asserts a generated parlay re-scored
through it is identical). The differences from the auto path are deliberate:
- `build_manual_leg` has **no probability clamp**: the auto pool drops legs under 50% /
  over 97%, but a user who picks Nacua over 9.5 receptions should see "6%", not an error.
- `prop_options` offers every stat that fits the position and clears the auto pool's
  minimum, with the risk tier's line as an editable starting point. In sportsbook mode
  only props the book posts are offered and the book's line is fixed (the UI hides the
  line input); the server rejects a leg the book doesn't price rather than silently
  falling back to a model line.
- Anytime-TD for a QB is "throws a TD pass" in model mode but rushing+receiving at a book,
  exactly as in `build_book_legs` - `build_manual_leg` mirrors that split.
- `Handler._build_manual_legs` is the single validator for evaluate and save; every
  rejection names the leg and the reason (bye, finished game, ruled-out player, stat that
  doesn't fit the position, missing line, book doesn't post that prop). It's tested
  directly with a synthetic `_parlay_context`.
- Save writes a `parlay_groups` row plus one `picks` row per leg with `model_probability`
  and `model_line`; `odds_american` is the book's price in sportsbook mode and null
  otherwise, which the My Picks EV needs filled in - the UI says so after saving.
- `_parlay_context(week)` gathers everything both paths need in one place; every call in
  it is a cache read (nothing spends Odds API credits).

### Sportsbook prop odds (the "Odds" dropdown on the Parlays tab)

Picking a sportsbook prices parlays at that book's real lines and odds instead of the
model's invented lines. Data comes from The Odds API's per-event endpoint
(`odds_client.refresh_props`), and several things about it are non-obvious:

- **It costs credits, so it is the one sanctioned exception to "only the background loop
  fetches."** One credit per market per game (`PROP_MARKETS`, 5 markets), so a full slate
  is ~75-80 credits against a 500/month free tier - impossible to poll (the existing
  game-odds poll alone can burn ~12/day if the app runs 24/7). `POST /api/odds/props/refresh`
  runs only from the user pressing the button (which needs a second confirming click that
  states the cost), behind a 60-credit reserve (`CREDIT_RESERVE`, it trims the slate to
  fit rather than spend into it) and a 10-minute cooldown. Never call `refresh_props` from
  a timer, a page load, or `_refresh_cycle`. One fetch returns *every* book, so switching
  books afterwards is free. The remaining balance is read off each response's
  `x-requests-remaining` header into `cache/odds_quota.json`; the events list endpoint is free.
- **Player matching cannot be an exact-id join.** The API identifies players only by name,
  so `parlay_engine.match_prop_rows` is the one place this codebase matches on names: an
  exact match on `normalize_player_name` (lowercase, no accents/punctuation/Jr-III) AND the
  player's team must be one of the two in that game AND (via `matchups`) that game must be
  the one the schedule has that team playing that week. A name matching two players on a
  team is dropped as ambiguous, never guessed. Unmatched props are counted and shown.
- **Bovada posts an alternate-line ladder under the standard market key** (28 pass-yard
  outcomes vs 4 at every other book), so `parse_event_props` keeps one row per (market,
  player) per book: the price nearest even money. Only Over/Yes sides are stored.
- **The book's anytime-TD market means rushing + receiving TDs for everyone**, so
  `build_book_legs` uses that for QBs too - not the passing TDs the model-only "Throws a TD
  Pass" leg uses. Don't unify them.
- **Games already kicked off are excluded** (their pre-game lines are gone), on top of the
  usual completed/bye filtering.
- **Deliberately no "expected value" ranking.** Against a real book this model's
  probabilities (season-pace averages, no matchup information) routinely disagree wildly -
  e.g. 83% vs a book-implied 39% on the same prop - so an EV sort would just rank wherever the
  model is most wrong. Sort options are the book's implied probability (`market`, the
  default in book mode and usually the more trustworthy), the model's probability, and
  payout. The UI shows both probabilities side by side with that caveat.
- Parlay payout is each leg's decimal odds multiplied. Books reprice *same-game* legs
  because they're correlated, so those parlays are flagged (`same_game`) as approximate.

### Injuries and weather inside parlays (filter vs. warn)

Both feed `/api/parlays`, but deliberately differently:

- **Injuries filter.** `parlay_engine.build_legs`/`build_book_legs` take an `injuries` map
  (`server._injury_status_by_player`) and drop OUT/IR/SUSPENDED players from every leg
  kind (yardage *and* touchdown) - this used to be a real bug: ~39 legs for players who
  weren't playing sat in the Week 2 pool. DOUBTFUL/QUESTIONABLE stay, tagged with
  `injury_status`/`injury_note`. The map uses ESPN's live leaguewide feed where it has the
  player, else the fantasy API's flag (they agreed on all ~290 overlapping players when
  checked). The background calibration snapshot applies the same filter so it never
  records a "prediction" for someone who's out.
- **Weather only warns.** `weather.warnings_for_leg` returns cautions and touches no
  probability, ranking or exclusion: the size of these effects isn't known here, so
  inventing an adjustment would be fabricated precision. It's a pure function keyed on
  stat (`WIND/PRECIP/COLD_AFFECTED_STATS`); rushing and TD legs are deliberately never
  flagged. `server._attach_weather_warnings` runs only over legs that made it into the
  returned parlays (a handful of games, not the ~400-leg pool), uses the *home* team's
  stadium for the forecast, and swallows a failed lookup - weather is a nice-to-have and
  must never break parlay generation. Domes ("controlled"), unreliable (>~16 days out)
  and failed forecasts produce nothing: an unknown is not a warning.

### Injury reporting (two sources, one schedule)

Injuries come from two feeds merged in `injury_report.py`:

- `espn_client.get_injuries()` - ESPN's leaguewide live feed (`site.api`), ~800
  players, updated continuously, carrying a status plus a human-readable beat-writer
  note. **This feed omits `athlete.id`** - the ESPN player id is recovered by regex
  from the player-card link in `athlete.links` (`.../player/_/id/4430841/...`), which
  keeps the join on an exact id rather than a name. It also reports a team *id* rather
  than an abbreviation; those ids resolve through the existing `PRO_TEAM_ABBR` (ESPN
  uses one team-id space across both API families - all 32 verified), so no second
  team table was needed.
- `nflverse_client.get_injury_reports(season)` - the official NFL injury report,
  contributing what ESPN doesn't carry: `practice_status` (DNP/Limited/Full through
  the practice week) and the named body part. Joins `gsis_id` -> `espn_id` through the
  same crosswalk as usage data. Subject to the same publication lag as everything else
  from nflverse, so for a live season this half is often `not_yet_published` while
  ESPN's half works - the UI says so rather than showing empty columns.

**ESPN wins any status disagreement** - it's the live feed, while the official report
is filed once and not revised. `sources` on each merged record names which feeds
contributed, so a cross-confirmed entry is distinguishable from a single-source one.
`server.py`'s dashboard additionally unions in the fantasy API's own per-player
`injury_status`, tagged `espn_fantasy`, since a player can carry a designation there
without appearing in the leaguewide feed at all.

**Why not Yahoo**: the original request was to scrape
`sports.yahoo.com/nfl/injuries`. Yahoo's `robots.txt` disallows `ClaudeBot`,
`Claude-Web`, and `anthropic-ai` sitewide, so an AI agent shouldn't be crawling it to
build or maintain a parser. Both sources above are structured feeds carrying strictly
more than that page renders, and neither breaks on an HTML redesign.

**The refresh schedule is Thursday 6:00 PM ET and Sunday 11:00 AM ET**
(`injury_report.SCHEDULE_CHECKPOINTS`), chosen to sit after Thursday's practice report
and ~90 minutes before the Sunday 1pm slate. Two non-obvious things about it:

1. **US Eastern is implemented by hand** (`injury_report._USEastern`), not via
   `zoneinfo`. Windows ships no IANA tz database, and stdlib `zoneinfo` falls back to
   the `tzdata` *pip* package - which the zero-dependency constraint forbids. So
   `ZoneInfo("America/New_York")` raises `ZoneInfoNotFoundError` on a Windows host
   while working fine in the Linux container: silently divergent behavior per platform.
   The hand-rolled version applies the post-2007 DST rule (second Sunday of March /
   first Sunday of November) and is correct only for US Eastern, 2007 onward.
2. **It catches up rather than firing.** This is a local app that is usually *not*
   running at 6pm on a Thursday, so a scheduler that waits for the clock to strike
   would never run. `is_refresh_due` instead asks whether the cached data predates the
   most recent checkpoint that already passed - so a machine that was asleep all
   Thursday evening refreshes on its next launch. `_refresh_injuries` in `server.py`
   deliberately ignores the background loop's generic `force_refresh` for this reason;
   honoring it would quietly demote the schedule back to the app's 6-hourly cadence.
   `GET /api/injuries?refresh=1` is the manual override.

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
  out are reported `forecast_reliable: False` rather than guessed. Its forecasts feed the
  Dashboard table and, via `warnings_for_leg`, cautions on parlay legs (never a
  probability change) - see "Injuries and weather inside parlays".
- `injury_report.py` - the Thursday/Sunday ET refresh schedule and the two-source
  injury merge. See "Injury reporting" above; the hand-rolled US Eastern tzinfo and
  the catch-up (rather than fire-at-time) scheduling are both load-bearing, not
  incidental.

### Frontend

Vanilla JS/CSS, no build step, no framework. `static/common.js` holds shared
fetch/format helpers and tab-switching; one file per tab (`parlays.js`, `builder.js`,
`picks.js`, `dashboard.js`, `history.js`) listens for a `tabshown` custom event to
lazy-load its data rather than fetching on page load. Scripts are loaded in dependency
order in `index.html` and rely on globals rather than modules/bundling - which means
top-level `const`/`let` names are shared across files: `builder.js` wraps itself in an
IIFE because it wants its own `weekSelect`/`bookSelect`/`riskSelect` for the Build Parlay
tab's controls, and reuses `renderParlayCard`/`fmtLeg`/`trackLeg` from `parlays.js`.

### `_bmad/` and `.claude/skills/bmad-*`

BMad-method planning-workflow tooling (agents, skills) installed into this repo for
planning/process work - not part of the application itself. Ignore unless a task
specifically involves the BMad workflow.
