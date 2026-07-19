# NFL Fantasy Parlay Advisor

A local web app that turns ESPN's fantasy football projections into player-prop parlay
suggestions with model-estimated hit probabilities.

> **Informational only, not gambling advice.** Probabilities are statistical estimates
> from an assumed variance model, not real sportsbook odds, and legs are treated as
> independent even when they aren't (e.g. a QB and their own WR). Nothing in this app
> places a bet or moves money.

## How it works

- **Data source**: [`espn_client.py`](espn_client.py) pulls player projections directly
  from ESPN's public fantasy football API (the same endpoint that powers
  [fantasy.espn.com/football/players/projections](https://fantasy.espn.com/football/players/projections)).
  No API key or login required.
- **Weekly vs. season-pace**: ESPN doesn't publish real per-week projections until a
  few days before that week's games. Until a specific week's projection exists, legs
  fall back to *season projection ÷ 17* as a per-game estimate (with wider assumed
  variance). Each leg is tagged so you can see which source it used.
- **Parlay engine**: [`parlay_engine.py`](parlay_engine.py) converts a player's
  projected stat line into an over/under prop (passing/rushing/receiving yards,
  receptions, anytime TD), estimates hit probability with a normal/Poisson variance
  model, and combines legs across different stat categories into parlay suggestions
  ranked by combined probability.
- **Server**: [`server.py`](server.py) is a dependency-free Python `http.server` app
  that serves the frontend and two JSON endpoints (`/api/projections`, `/api/parlays`),
  caching ESPN responses for a few hours to avoid hammering their API.

## Running it

Requires Python 3 only (standard library, no `pip install` needed).

```
python server.py
```

Then open **http://localhost:8787**.

## Using the app

- **Week**: defaults to ESPN's current week; switch weeks to see how legs are built
  (falls back to season-pace estimate for any week without a published projection yet).
- **Legs**: 2, 3, or 4-leg parlays.
- **Risk**: Safe / Balanced / Boom — controls how far below (or above) the projected
  average each prop line is set, trading hit probability for payout.
- **Position**: filter legs to a single position, or leave on All for mixed parlays.
- **Refresh Data**: re-fetches projections from ESPN, bypassing the cache.
- **Browse Season Projections**: a searchable table of ESPN's full season projections
  for transparency into the underlying numbers.

## Project structure

```
espn_client.py     ESPN API client (fetch + cache projections, weekly stat lookup)
parlay_engine.py    Prop-leg generation, probability model, parlay combination logic
server.py           Local HTTP server (static files + JSON API)
static/             Frontend (HTML/CSS/vanilla JS, no build step)
cache/               Cached ESPN responses (gitignored, created at runtime)
```
