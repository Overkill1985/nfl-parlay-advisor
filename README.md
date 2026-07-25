# NFL Fantasy Parlay Advisor

A local, single-user web app for building and evaluating NFL prop parlays: it turns
ESPN's fantasy projections into model-scored parlay suggestions, lets you track real
sportsbook odds against those (or your own) picks, flags correlated/conflicting legs,
surfaces injury/usage/weather risk, and records outcomes for backtesting and model
calibration.

> **Informational only, not gambling advice.** Probabilities are statistical estimates
> from an assumed variance model, not real sportsbook odds. Entering your own
> sportsbook odds is for your own tracking only. Nothing in this app places a bet or
> moves money.

## How it works

- **Data source**: [`espn_client.py`](espn_client.py) pulls player projections,
  weekly matchups/schedule, and injury status directly from ESPN's public fantasy
  football API (the same one behind
  [fantasy.espn.com/football/players/projections](https://fantasy.espn.com/football/players/projections)).
  No API key or login required. Responses are cached to `cache/*.json` (season-specific
  filenames, so a backtest against a past season never clobbers the live cache) and
  refreshed automatically by a background thread in `server.py`.
- **Weekly vs. season-pace**: ESPN doesn't publish real per-week projections until a
  few days before that week's games. Until then, legs fall back to *season projection ÷
  17* as a per-game estimate (with wider assumed variance). Every leg is tagged with
  which source it used.
- **Parlay engine**: [`parlay_engine.py`](parlay_engine.py) converts a player's
  projected stat line into an over/under prop, estimates hit probability with a
  normal/Poisson variance model, and combines legs into parlay suggestions - along with
  a risk score ([`odds_math.py`](odds_math.py)) and correlation/game-script warnings
  ([`correlation.py`](correlation.py)).
- **Odds & EV** ([`odds_math.py`](odds_math.py)): American/decimal/implied-probability
  conversions, parlay combined odds, payout, and expected value - shared by both the
  model-generated parlays and your own tracked picks.
- **Correlation warnings** ([`correlation.py`](correlation.py)): rule-based checks -
  same-team QB+pass-catcher stacks, competing same-team TD props, a favorite's
  moneyline supporting their own rushing prop, a game total taken Under conflicting
  with passing props, and "too many legs in one game" concentration risk - plus a
  plain-English game-script summary per game.
- **Weather** ([`weather.py`](weather.py)): a static 32-team stadium/roof reference
  table plus live forecasts from Open-Meteo (free, no API key) for outdoor stadiums.
  Dome/retractable-roof games never hit the network. Forecasts more than ~16 days out
  are honestly marked unreliable rather than guessed.
- **Storage** ([`storage.py`](storage.py)): a local sqlite database (`data/`,
  gitignored) holding your tracked picks, parlay groups, and the model's own
  leg-generation history (for calibration tracking) - separate from the ESPN response
  cache.
- **History & backtesting** ([`history.py`](history.py)): auto-grades placed picks and
  model snapshots once real results exist, computes ROI/win-rate breakdowns, and runs
  two *different* kinds of validation - a **mechanical backtest** of a fixed rule
  against a completed season's real results (works today), and **model calibration**
  checking whether this app's own probability estimates were accurate (only covers
  weeks since snapshotting started, since ESPN exposes no historical point-in-time
  projections to check against retroactively).
- **Server**: [`server.py`](server.py) is a dependency-free Python `http.server` app
  with a small regex-based router, serving the frontend and all JSON endpoints.

## Running it

Requires Python 3 only (standard library, no `pip install` needed).

```
python server.py
```

Then open **http://localhost:8787**.

### Running in Docker

```
docker compose up --build
```

`cache/` and `data/` are mounted as volumes so the ESPN response cache and your tracked-picks
sqlite db survive a rebuild. The port is published as `127.0.0.1:8787:8787` by default, matching
this app's own no-auth, single-user design - drop the `127.0.0.1:` prefix in `docker-compose.yml`
only if you actually want it reachable from other machines on your network. Internally the
container always binds `0.0.0.0` (`HOST=0.0.0.0` in the `Dockerfile`) since that's required for
Docker's port-forwarding to reach the process at all; real exposure is controlled by the port
mapping, not the app.

## Using the app

**Parlays tab** - generate model-scored parlays:
- **Week**: defaults to ESPN's current week.
- **Legs**: 2, 3, or 4-leg parlays. **Risk**: Safe / Balanced / Boom, trading hit
  probability for payout. **Position**: filter to one position or leave on All.
- Each parlay shows correlation warnings, a game-script summary, and a risk score.
- **Track**: carries a leg's player/stat/line/probability into the My Picks form.
- **Browse Season Projections**: a searchable table of ESPN's full projections.

**My Picks tab** - track real sportsbook odds:
- Log any player prop, moneyline, spread, or total with your sportsbook's actual odds,
  opening/closing lines, and status.
- Group picks into a **Parlay Group** with a stake, then **Calculate EV** for combined
  odds, implied probability, model probability, edge, payout, expected value, risk
  score, and the same correlation/game-script checks.

**Dashboard tab** - injury/usage/weather:
- Injury report (ESPN's designations), trailing-5-game usage trend (the practical
  stand-in for snap%/red-zone touches, which aren't available from any free data
  source), and per-game weather with prop-impact flags.

**History tab**:
- **Your Picks Performance**: ROI and win rate by leg count, market type, direction,
  and team, once picks are graded. **Grade Completed Weeks Now** auto-grades placed
  player-prop picks against real results.
- **Model Calibration**: realized hit rate per confidence bucket, from the model's own
  generated legs (snapshotted automatically every refresh cycle).
- **Mechanical Backtest**: test a fixed rule ("bet the over at X% of trailing average")
  against any completed season's real results.
- **Export**: download all tracked data as JSON.

## Project structure

```
espn_client.py     ESPN API client (projections, schedule, injuries, weekly actuals)
parlay_engine.py   Prop-leg generation, probability model, parlay combination logic
odds_math.py       Odds conversions, payout, expected value, risk score
correlation.py     Correlation warnings + game-script summaries
weather.py         Stadium reference table + Open-Meteo forecasts
storage.py         sqlite storage for picks, parlay groups, model snapshots
history.py         Grading, calibration reporting, mechanical backtesting
server.py          Local HTTP server (regex router + all JSON endpoints)
static/            Frontend - common.js, parlays.js, picks.js, dashboard.js, history.js
cache/             Cached ESPN/weather responses (gitignored, created at runtime)
data/              sqlite db + migration backups (gitignored, created at runtime)
```
