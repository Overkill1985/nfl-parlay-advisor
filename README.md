# NFL Fantasy Parlay Advisor

A local, single-user web app for building and evaluating NFL prop parlays: it turns
ESPN's fantasy projections into model-scored parlay suggestions, lets you track real
sportsbook odds against those (or your own) picks, flags correlated/conflicting legs,
surfaces real injury/usage/weather risk, and records outcomes for backtesting and model
calibration. Beyond ESPN's fantasy API, it also pulls real snap%/target-share usage
from nflverse, real final scores for grading, and (optionally) real market odds.

> **Informational only, not gambling advice.** Probabilities are statistical estimates
> from an assumed variance model, not real sportsbook odds. Entering your own
> sportsbook odds is for your own tracking only. Nothing in this app places a bet or
> moves money.

## How it works

- **Data source**: [`espn_client.py`](espn_client.py) pulls player projections,
  weekly matchups/schedule, and injury status from ESPN's public fantasy football API
  (the same one behind
  [fantasy.espn.com/football/players/projections](https://fantasy.espn.com/football/players/projections)),
  plus real final scores from a second, separate ESPN API (general sports scores, not
  fantasy). No API key or login required for either. Responses are cached to
  `cache/*.json` (season-specific filenames, so a backtest against a past season never
  clobbers the live cache) and refreshed automatically by a background thread in
  `server.py`.
- **Real usage data** ([`nflverse_client.py`](nflverse_client.py)): real snap %, target
  share, air yards share, and WOPR from [nflverse](https://github.com/nflverse/nflverse-data)'s
  public CSVs - no API key, no pip dependency (plain CSV parsing, not the `nfl_data_py`
  package). Joins to ESPN's player records through exact ids, not name matching. Note
  that nflverse typically publishes a season's data a bit behind real time, independent
  of where the season actually is - "not available yet" for the current season is
  expected, not a bug.
- **Real market odds** ([`odds_client.py`](odds_client.py), optional): real consensus
  moneylines/spreads/totals from [The Odds API](https://the-odds-api.com/) (free tier,
  requires your own API key via the `ODDS_API_KEY` env var - see "Running it" below).
  Without a key, this simply doesn't run; nothing else in the app depends on it. The same
  key also powers the **sportsbook selector** on the Parlays tab: real player-prop lines
  and prices from FanDuel, DraftKings, BetMGM, BetRivers, BetOnline and Bovada. Fetching
  those props costs API credits (about 75 for a full week's slate, against 500/month on
  the free tier), so it only happens when you press the button - never automatically.
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
  plain-English game-script summary per game, preferring a manually-tracked pick, then
  real auto market odds, then a clearly-labeled roster-strength guess, in that order.
- **Injury reports** ([`injury_report.py`](injury_report.py)): two sources merged into
  one view - ESPN's leaguewide live feed (status plus the beat-writer note, updated
  continuously) and [nflverse](https://github.com/nflverse/nflverse-data)'s official
  NFL injury report (practice participation DNP/Limited/Full, and the named body
  part). ESPN wins any disagreement on game status since it's the live feed; every row
  shows which sources backed it. Refreshed on a fixed schedule - **Thursdays 6:00 PM ET
  and Sundays 11:00 AM ET** - after Thursday's practice report and ~90 minutes before
  the Sunday afternoon slate. If the app wasn't running at a checkpoint it catches up
  the next time it starts, rather than skipping that window.
- **Weather** ([`weather.py`](weather.py)): a static 32-team stadium/roof reference
  table plus live forecasts from Open-Meteo (free, no API key) for outdoor stadiums.
  Dome/retractable-roof games never hit the network. Forecasts more than ~16 days out
  are honestly marked unreliable rather than guessed. Beyond the Dashboard table, wind,
  rain and cold show up as cautions on the parlay legs they could affect (see "Parlays
  tab" below) - a warning only, never a change to a probability.
- **Storage** ([`storage.py`](storage.py)): a local sqlite database (`data/`,
  gitignored) holding your tracked picks, parlay groups, and the model's own
  leg-generation history (for calibration tracking) - separate from the ESPN response
  cache.
- **History & backtesting** ([`history.py`](history.py)): auto-grades placed picks
  (player props, and now moneyline/spread/total too, against real final scores) and
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

Optional: set `ODDS_API_KEY` (a free key from [the-odds-api.com](https://the-odds-api.com/))
to enable real market odds and the per-sportsbook prop odds on the Parlays tab.
Everything else works with zero configuration.

The key must be a real environment variable - `python server.py` does **not** read a
`.env` file (there's no dotenv dependency). On Windows, `setx ODDS_API_KEY "your-key"`
saves it for future terminals; a terminal that was already open won't see it, so open a
new one. Docker Compose *does* pick up a `.env` file next to `docker-compose.yml`.

### Running the tests

```
python -m unittest discover -s tests
```

Pure-function tests, no network, no API credits spent.

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
- **Week**: defaults to ESPN's current week. Only games that haven't finished are
  used - once a game ends its players drop out of the parlays, and a team on its bye
  week never appears - so late in a slate you're choosing from what's still to play.
- **Legs**: 2, 3, or 4-leg parlays. **Risk**: Safe / Balanced / Boom, trading hit
  probability for payout. **Position**: filter to one position or leave on All.
- **Odds**: leave on "Model" for the app's own lines and fair odds, or pick a sportsbook
  to build parlays from *that book's* real lines and prices, showing what the book would
  actually pay. Press **Fetch prop odds** first (it asks you to click twice, since it
  spends API credits; one fetch covers every book, so switching books afterwards is
  free). With a book selected, Risk is disabled (the book sets the lines) and a **Sort
  by** control appears - the book's implied probability by default, since the model's
  own probabilities often disagree sharply with a real book's.
- **Injuries**: players ESPN lists as out, on injured reserve or suspended are left out
  of the pool entirely (the page says who), while doubtful/questionable players stay
  with a badge on each leg - hover it for ESPN's latest note. Uses ESPN's live injury
  feed, falling back to the fantasy API's own status.
- **Weather**: a leg gets a caution when its game's forecast makes it harder - wind or
  cold for passing and receiving yards, rain or snow for those plus receptions.
  Rushing yards and touchdowns are never flagged (conditions can push them either way).
  Domes and games too far out to forecast reliably say nothing. These are heads-up
  notes only; the hit probabilities are unchanged.
- Each parlay shows correlation warnings, a game-script summary, and a risk score.
  Same-game legs are flagged, because sportsbooks reprice those and the real payout is
  usually lower than the multiplied price.
- **Track**: carries a leg's player/stat/line/probability into the My Picks form - and,
  for a sportsbook-priced leg, the book's actual odds and name.
- **Browse Season Projections**: a searchable table of ESPN's full projections.

**My Picks tab** - track real sportsbook odds:
- Log any player prop, moneyline, spread, or total with your sportsbook's actual odds,
  opening/closing lines, and status.
- Group picks into a **Parlay Group** with a stake, then **Calculate EV** for combined
  odds, implied probability, model probability, edge, payout, expected value, risk
  score, and the same correlation/game-script checks.

**Dashboard tab** - injury/usage/weather:
- **Injury report**: status, the specific injury, practice participation, and the
  latest beat-writer note, merged from ESPN's live feed and nflverse's official
  report. Shows when it last refreshed and when the next scheduled check is due
  (Thursdays 6 PM ET / Sundays 11 AM ET). Practice-participation columns depend on
  nflverse having published the season - during a live season that half often lags,
  which the page states plainly instead of showing blank columns.
- Trailing-5-game stat trend plus real snap %/target share/WOPR from nflverse when
  available for that player/week, and per-game weather with prop-impact flags.

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
espn_client.py     ESPN API client (projections, schedule, injuries, scores)
nflverse_client.py Real usage data (snap %, target share, WOPR) + official injury reports
injury_report.py   Thu/Sun ET refresh schedule + two-source injury merge
odds_client.py     Real market odds (moneyline/spread/total) + per-sportsbook player props
parlay_engine.py   Prop-leg generation, probability model, parlay combination logic
odds_math.py       Odds conversions, payout, expected value, risk score
correlation.py     Correlation warnings + game-script summaries
weather.py         Stadium reference table + Open-Meteo forecasts
cache_io.py        Atomic (temp-file + rename) JSON cache writes, shared by the clients above
storage.py         sqlite storage for picks, parlay groups, model snapshots
history.py         Grading, calibration reporting, mechanical backtesting
server.py          Local HTTP server (regex router + all JSON endpoints)
static/            Frontend - common.js, parlays.js, picks.js, dashboard.js, history.js
tests/             unittest suite - pure-function tests, no network
cache/             Cached ESPN/nflverse/odds/weather responses (gitignored, created at runtime)
data/              sqlite db + migration backups (gitignored, created at runtime)
```

## CI/CD & Security Scanning

### GitHub Actions Workflow

Every push to `main` or `develop`, and every pull request, automatically:

1. **Runs the unit tests** (on the same Python version the container ships)
2. **Builds** the Docker image
3. **Scans** with [Trivy](https://github.com/aquasecurity/trivy) for vulnerabilities
4. **Blocks** the build if critical, fixable vulnerabilities are detected
5. **Comments** on pull requests with scan results
6. **Uploads** SARIF report to GitHub Security tab
7. **Pushes** to Docker Hub (on merge to `main` only)

Steps 1-6 need **no credentials at all** - Trivy requires no account, so build and scan
work on a fresh clone with nothing configured. (This is why the scanner is Trivy rather
than Docker Scout: Scout requires Docker Hub authentication for every command, so it
can't scan at all without secrets.) Only step 7, publishing the image, needs secrets.

### Keeping it patched

The app has no pip dependencies, so the things that go stale are the CI actions and the
container's base image:

- **Dependabot** ([`.github/dependabot.yml`](.github/dependabot.yml)) checks the GitHub
  Actions and the Dockerfile's base image weekly and opens a PR for anything newer.
- The **Dockerfile runs `apt-get upgrade`** at build time. `python:3.14-slim` is a floating
  tag that Docker Hub doesn't rebuild the moment Debian ships a fix, so a patched package
  can sit unpicked-up for days - this closes that gap instead of waiting on the rebuild.
  (It's what cleared three critical `perl-base` CVEs that failed the scan gate.)

### Security notes

This is a no-login, single-user app, so it's built to be reachable from your own machine
only: the server binds `127.0.0.1` by default, and rejects any request whose `Host` or
`Origin` header isn't `localhost` or `127.0.0.1`. That blocks cross-site requests
and DNS-rebinding attacks from a web page you happen to have open. The consequence: open
the app at `localhost` or `127.0.0.1`, not your machine's LAN address or hostname (you'll
get a 403).

### Setup (optional - only needed to publish images)

1. **Add GitHub Secrets** (Settings → Secrets and variables → Actions):
   ```
   DOCKER_USERNAME = your-docker-hub-username
   DOCKER_PASSWORD = your-docker-personal-access-token
   ```
   Leave these unset and the workflow still builds and scans; it just skips the push.

2. **Trigger the workflow** by pushing to `main` or opening a pull request:
   ```bash
   git push origin main
   ```

3. **View results**:
   - GitHub Actions tab: full build logs and scan output
   - GitHub Security tab (Code scanning alerts): vulnerability details
   - Pull request comments: scan summary, also saved as a workflow artifact

### Scanning Locally

Before pushing, scan your local image:

```bash
docker build -t nfl-parlay-advisor:local .
```

```bash
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock aquasec/trivy:latest image nfl-parlay-advisor:local
```

### Vulnerability Gate

The workflow fails if **critical** vulnerabilities with a known fix are detected. To
adjust the threshold, edit the "Check for critical vulnerabilities" step in
`.github/workflows/docker-build-scan.yml`:

```yaml
severity: CRITICAL      # add HIGH to also fail on high-severity
ignore-unfixed: true    # set false to also fail on vulns with no fix available
exit-code: '1'
```
