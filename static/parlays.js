const statusEl = document.getElementById("status");
const parlaysEl = document.getElementById("parlays");
const playerTableWrap = document.getElementById("playerTableWrap");
const playerSearch = document.getElementById("playerSearch");
const weekSelect = document.getElementById("week");
const bookSelect = document.getElementById("book");
const sortSelect = document.getElementById("sortBy");
const sortWrap = document.getElementById("sortWrap");
const riskSelect = document.getElementById("risk");
const bookStatusEl = document.getElementById("bookStatus");

let allPlayers = [];
let currentWeek = 1;

function populateWeekSelect(current) {
  currentWeek = current;
  weekSelect.innerHTML = "";
  for (let w = 1; w <= 18; w++) {
    const opt = document.createElement("option");
    opt.value = w;
    opt.textContent = w === current ? `Week ${w} (current)` : `Week ${w}`;
    if (w === current) opt.selected = true;
    weekSelect.appendChild(opt);
  }
}

function fmtLeg(leg) {
  const sourceBadge = leg.source === "weekly_projection"
    ? `<span class="badge badge-live" title="ESPN has published a real projection for this player for this specific week">live wk proj</span>`
    : `<span class="badge" title="No weekly projection published yet for this player/week — estimated from season projection ÷ 17">season-pace est.</span>`;
  const injuryBadge = leg.injury_status
    ? ` <span class="badge badge-out" title="${escapeHtml(leg.injury_note || `ESPN lists this player as ${leg.injury_status.toLowerCase()} - check his status before betting`)}">${escapeHtml(leg.injury_status.toLowerCase())}</span>`
    : "";
  const weatherNotes = (leg.weather_warnings || [])
    .map(w => `<span class="leg-warn">Weather: ${escapeHtml(w)}</span>`).join("");
  const price = leg.book_price != null ? ` @ ${fmtOdds(leg.book_price)}` : "";
  const bookLine = leg.book_price != null
    ? `<span class="leg-book" title="The model is a season-pace average with no matchup information, so a gap here mostly shows what it doesn't know - not that the sportsbook is wrong.">book implies ${fmtPct(leg.implied_probability, 0)} · model says ${fmtPct(leg.probability, 0)}</span>`
    : "";
  if (leg.stat === "anytime_td") {
    return `${escapeHtml(leg.player)} (${escapeHtml(leg.team)} ${escapeHtml(leg.position)}) — ${escapeHtml(leg.label)}${price} ${sourceBadge}${injuryBadge}${bookLine}${weatherNotes}`;
  }
  return `${escapeHtml(leg.player)} (${escapeHtml(leg.team)} ${escapeHtml(leg.position)}) — ${escapeHtml(leg.direction)} ${leg.line} ${escapeHtml(leg.label)}${price} ${sourceBadge}${injuryBadge}${bookLine}${weatherNotes}`;
}

// Registered by picks.js once it loads; kept optional here so parlays.js has
// no hard dependency on picks.js load order.
function trackLeg(leg) {
  if (window.prefillPickForm) {
    window.prefillPickForm(leg);
    switchTab("picks");
  }
}
window.trackLeg = trackLeg;

// One parlay card. Shared by the generated list and the build-your-own
// slip, so a hand-built parlay is displayed exactly like a generated one.
// `i` keys the Track buttons and numbers the card; pass a string (e.g.
// "manual") for a card rendered outside the generated list.
function renderParlayCard(p, i, data) {
  window._lastLegsById = window._lastLegsById || {};
  const legRows = p.legs.map((leg, li) => {
    const legKey = `${i}-${li}`;
    window._lastLegsById[legKey] = leg;
    return `
    <div class="leg-row">
      <span class="leg-desc">${fmtLeg(leg)}</span>
      <span class="leg-prob">${(leg.probability * 100).toFixed(0)}%</span>
      <button class="track-btn" data-leg-key="${legKey}" title="Track this leg with your own sportsbook odds">Track</button>
    </div>
  `;
  }).join("");

  const warnings = p.correlation_warnings || [];
  const corrBadge = warnings.length
    ? `<span class="badge" title="${escapeHtml(warnings.map(w => w.message).join(" / "))}">${warnings.length} correlation note${warnings.length > 1 ? "s" : ""}</span>`
    : "";

  const warningLines = warnings.map(w => `
    <div class="warning-line warning-${w.severity}">${escapeHtml(w.message)}</div>
  `).join("");

  const sameGameNote = p.same_game
    ? `<div class="warning-line warning-caution">Same-game legs: sportsbooks reprice these because they're correlated, so a real slip will likely pay less than the multiplied price shown.</div>`
    : "";

  const scriptLines = (p.game_script || []).map(g => `
    <div class="game-script-line">${escapeHtml(g.summary)}</div>
  `).join("");

  return `
    <div class="parlay-card">
      <div class="parlay-card__header">
        <div>${typeof i === "number" ? `#${i + 1} &middot; ` : ""}${p.legs.length}-leg parlay ${corrBadge}</div>
        <div class="parlay-card__odds">${p.book_decimal_odds != null
          ? `${escapeHtml(data.book.title)} pays: ${p.book_decimal_odds}x (${fmtOdds(p.book_american_odds)}) <span class="leg-book">model's fair price: ${p.estimated_decimal_odds}x (${fmtOdds(p.estimated_american_odds)})</span>`
          : `Fair odds: ${p.estimated_decimal_odds}x (${fmtOdds(p.estimated_american_odds)})`}</div>
      </div>
      <div class="parlay-card__prob">${p.book_implied_probability != null
        ? `${escapeHtml(data.book.title)}'s prices imply ${(p.book_implied_probability * 100).toFixed(1)}% to hit &middot; this app's model says ${(p.combined_probability * 100).toFixed(1)}% &middot; risk score ${p.risk_score}/100`
        : `Estimated combined hit probability: ${(p.combined_probability * 100).toFixed(1)}% &middot; risk score ${p.risk_score}/100`}</div>
      ${legRows}
      ${sameGameNote}
      ${warningLines}
      ${scriptLines}
    </div>
  `;
}

function renderParlays(data) {
  if (data.error) {
    parlaysEl.innerHTML = `<p style="color:#e0654e">Error: ${escapeHtml(data.error)}</p>`;
    return;
  }
  if (!data.parlays.length) {
    if (data.book && data.leg_pool_size === 0) {
      parlaysEl.innerHTML = `<p>No ${escapeHtml(data.book.title)} props matched this week's remaining games (${data.book.props_total} props loaded, ${data.book.props_unmatched} couldn't be matched to a player). Prop odds are fetched a few days before kickoff - try refreshing them, or pick another sportsbook.</p>`;
    } else if (data.leg_pool_size === 0 && data.week_all_completed) {
      parlaysEl.innerHTML = `<p>All of Week ${data.week}'s games have finished — nothing left to build a parlay from. Check back once Week ${data.week + 1} begins.</p>`;
    } else {
      parlaysEl.innerHTML = `<p>No parlays matched these filters (leg pool: ${data.leg_pool_size}). Try a different risk level or position.</p>`;
    }
    return;
  }

  window._lastLegsById = window._lastLegsById || {};

  const cards = data.parlays.map((p, i) => renderParlayCard(p, i, data)).join("");

  const sourceSummary = data.legs_from_weekly_projection > 0
    ? `${data.legs_from_weekly_projection} live weekly-projection legs, ${data.legs_from_season_pace} season-pace-estimate legs`
    : `all ${data.legs_from_season_pace} legs are season-pace estimates — ESPN hasn't published Week ${data.week} projections yet`;
  const gamesNote = data.games_remaining != null
    ? ` &middot; ${data.games_remaining} game${data.games_remaining === 1 ? "" : "s"} left to play this week (finished games are excluded)`
    : "";

  const bookNote = data.book
    ? `<p style="color:var(--muted); font-size:0.85rem">Priced at <strong>${escapeHtml(data.book.title)}</strong>'s real lines and odds &middot; ${data.book.props_total - data.book.props_unmatched - data.book.props_ambiguous} of ${data.book.props_total} props matched to a player &middot; prop odds cover ${data.book.props_games} game${data.book.props_games === 1 ? "" : "s"}${data.games_remaining != null && data.book.props_games < data.games_remaining ? ` of the ${data.games_remaining} still to play (use Refresh prop odds above to load the rest)` : ""}, fetched ${data.book.props_age_hours}h ago${data.book.props_partial ? " (partial - some games failed to load)" : ""}. Risk level doesn't apply here: the book sets the lines. The model behind the hit probabilities is a season-pace average with no matchup information (injuries only remove players who are out, and weather only adds warnings - neither changes a probability), so treat this as a way to build a slip at real prices, not as a betting edge.</p>`
    : "";

  const excluded = data.injury_excluded || [];
  const injuryNote = excluded.length
    ? `<p style="color:var(--muted); font-size:0.85rem" title="${escapeHtml(excluded.map(x => `${x.name} (${x.team}, ${x.status})`).join(", "))}">${excluded.length} player${excluded.length === 1 ? "" : "s"} ruled out (out / injured reserve / suspended) ${excluded.length === 1 ? "is" : "are"} left out of the pool: ${escapeHtml(excluded.slice(0, 6).map(x => `${x.name} (${x.status})`).join(", "))}${excluded.length > 6 ? `, and ${excluded.length - 6} more` : ""}.</p>`
    : "";

  parlaysEl.innerHTML = bookNote + injuryNote + `<p style="color:var(--muted); font-size:0.85rem">Leg pool size: ${data.leg_pool_size} candidates (${sourceSummary})${gamesNote} &middot; showing top ${data.parlays.length}${data.games_in_parlays ? ` spread across ${data.games_in_parlays} game${data.games_in_parlays === 1 ? "" : "s"}` : ""}</p>` + cards;

  parlaysEl.querySelectorAll(".track-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const leg = window._lastLegsById[btn.dataset.legKey];
      if (leg) trackLeg({ ...leg, week: Number(weekSelect.value || currentWeek), sportsbook: data.book ? data.book.title : "" });
    });
  });
}

async function generateParlays() {
  const legs = document.getElementById("numLegs").value;
  const risk = document.getElementById("risk").value;
  const position = document.getElementById("position").value;
  const week = weekSelect.value || currentWeek;
  const book = bookSelect.value;
  const sort = sortSelect.value;

  statusEl.textContent = "Generating...";
  parlaysEl.innerHTML = "";
  try {
    const res = await fetch(`/api/parlays?legs=${legs}&risk=${risk}&position=${position}&week=${week}&book=${encodeURIComponent(book)}&sort=${sort}`);
    const data = await res.json();
    if (data.current_week) populateWeekSelect(data.current_week);
    renderParlays(data);
    statusEl.textContent = data.fetched_at
      ? `Projections cached ${new Date(data.fetched_at * 1000).toLocaleString()}`
      : "";
  } catch (err) {
    parlaysEl.innerHTML = `<p style="color:#e0654e">Request failed: ${err}</p>`;
    statusEl.textContent = "";
  }
}

function renderPlayerTable(players) {
  const rows = players.slice(0, 200).map(p => `
    <tr>
      <td>${p.name}</td>
      <td>${p.position}</td>
      <td>${p.team}</td>
      <td>${p.projected_points_season.toFixed(1)}</td>
      <td>${p.projected_points_per_game.toFixed(1)}</td>
      <td>${p.stats_season.pass_yds.toFixed(0)}</td>
      <td>${p.stats_season.rush_yds.toFixed(0)}</td>
      <td>${p.stats_season.rec_yds.toFixed(0)}</td>
      <td>${p.stats_season.receptions.toFixed(0)}</td>
    </tr>
  `).join("");

  playerTableWrap.innerHTML = `
    <table>
      <thead>
        <tr>
          <th>Player</th><th>Pos</th><th>Team</th>
          <th>Proj Pts (season)</th><th>Proj Pts/Gm</th>
          <th>Pass Yds</th><th>Rush Yds</th><th>Rec Yds</th><th>Rec</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

async function loadProjections(refresh = false) {
  statusEl.textContent = refresh ? "Refreshing from ESPN..." : "Loading projections...";
  const res = await fetch(`/api/projections${refresh ? "?refresh=1" : ""}`);
  const data = await res.json();
  if (data.error) {
    statusEl.textContent = "";
    playerTableWrap.innerHTML = `<p style="color:#e0654e">Error: ${escapeHtml(data.error)}</p>`;
    return;
  }
  allPlayers = data.players;
  renderPlayerTable(allPlayers);
  if (data.current_week) populateWeekSelect(data.current_week);
  statusEl.textContent = `Projections cached ${new Date(data.fetched_at * 1000).toLocaleString()}`;
}

playerSearch.addEventListener("input", () => {
  const q = playerSearch.value.toLowerCase();
  renderPlayerTable(allPlayers.filter(p => p.name.toLowerCase().includes(q)));
});

// -- sportsbook odds ------------------------------------------------------
//
// Prop odds cost Odds API credits to fetch (one fetch covers every book), so
// nothing here fetches on load or on a timer - only the button below, and only
// after a second confirming click that states the cost.

let booksStatus = null;
let fetchConfirmTimer = null;

function syncBookControls() {
  const inBookMode = Boolean(bookSelect.value);
  sortWrap.hidden = !inBookMode;
  riskSelect.disabled = inBookMode;
  riskSelect.title = inBookMode
    ? "Risk level tunes this app's own model lines - with a sportsbook selected, the book sets the lines"
    : "";
}

function renderBookStatus() {
  const s = booksStatus;
  if (!s) {
    bookStatusEl.innerHTML = "";
    return;
  }
  if (!s.api_key_configured) {
    bookStatusEl.innerHTML = `<span>Sportsbook odds need an <code>ODDS_API_KEY</code> environment variable (free key from the-odds-api.com).</span>`;
    return;
  }
  const credits = s.credits_remaining != null
    ? `${s.credits_remaining} Odds API credits left`
    : "Odds API credit balance not known yet";
  const freshness = s.available
    ? `Prop odds fetched ${s.age_hours}h ago · ${s.books.length} sportsbooks · ${s.upcoming_events} upcoming game${s.upcoming_events === 1 ? "" : "s"}${s.partial ? " · partial" : ""}`
    : "No sportsbook prop odds loaded yet";
  const label = s.available ? "Refresh prop odds" : "Fetch prop odds";
  bookStatusEl.innerHTML = `
    <span>${escapeHtml(freshness)} · ${escapeHtml(credits)}</span>
    <button id="fetchProps" title="One fetch covers every sportsbook, so switching books afterwards is free">${label} (up to ${s.max_refresh_cost} credits)</button>
    <span id="fetchPropsMsg"></span>`;
  document.getElementById("fetchProps").addEventListener("click", onFetchPropsClick);
}

async function loadBooks() {
  try {
    booksStatus = await apiGet("/api/odds/books");
  } catch (err) {
    booksStatus = null;
  }
  const previous = bookSelect.value;
  const books = booksStatus ? booksStatus.books : [];
  bookSelect.innerHTML = `<option value="">Model (fair odds)</option>` +
    books.map(b => `<option value="${escapeHtml(b.key)}">${escapeHtml(b.title)}</option>`).join("");
  bookSelect.value = books.some(b => b.key === previous) ? previous : "";
  syncBookControls();
  renderBookStatus();
}

function onFetchPropsClick() {
  const btn = document.getElementById("fetchProps");
  if (btn.dataset.confirming !== "1") {
    btn.dataset.confirming = "1";
    btn.textContent = `Click again to spend up to ${booksStatus.max_refresh_cost} credits`;
    fetchConfirmTimer = setTimeout(renderBookStatus, 6000);
    return;
  }
  clearTimeout(fetchConfirmTimer);
  fetchProps();
}

async function fetchProps() {
  const btn = document.getElementById("fetchProps");
  const msg = document.getElementById("fetchPropsMsg");
  btn.disabled = true;
  msg.textContent = "Fetching from The Odds API (about 15 seconds)...";
  try {
    const result = await apiPost("/api/odds/props/refresh");
    await loadBooks();
    const note = result.trimmed ? " (fetched the soonest games your remaining credits allowed)" : "";
    document.getElementById("fetchPropsMsg").textContent =
      `Loaded ${result.events_fetched} game${result.events_fetched === 1 ? "" : "s"} from ${result.books.length} sportsbooks${note}.`;
    if (bookSelect.value) await generateParlays();
  } catch (err) {
    msg.textContent = err.message;
    btn.disabled = false;
  }
}

// -- build your own parlay ----------------------------------------------
//
// State: which games are open, and the slip (an ordered list of
// {player_id, player, team, position, stat, label, line, book_line}). Every
// change to the slip re-evaluates it via POST /api/builder/evaluate (cache
// reads only on the server - never spends Odds API credits).

const builderGamesEl = document.getElementById("builderGames");
const builderPlayersEl = document.getElementById("builderPlayers");
const builderSlipEl = document.getElementById("builderSlip");
const builderResultEl = document.getElementById("builderResult");
const builderMsgEl = document.getElementById("builderMsg");
const builderSaveBtn = document.getElementById("builderSave");
const builderSlipCount = document.getElementById("builderSlipCount");

let builderOptions = null;        // last /api/builder/options payload
let builderOpenGames = new Set(); // "AWAY@HOME" keys
let builderSlip = [];
let builderLastEval = null;
let builderEvalTimer = null;

function gameKey(g) { return `${g.away}@${g.home}`; }

async function loadBuilderOptions() {
  const week = weekSelect.value || currentWeek;
  const risk = riskSelect.value;
  const book = bookSelect.value;
  builderGamesEl.innerHTML = `<span class="section-hint">Loading games...</span>`;
  try {
    builderOptions = await apiGet(`/api/builder/options?week=${week}&risk=${risk}&book=${encodeURIComponent(book)}`);
  } catch (err) {
    builderOptions = null;
    builderGamesEl.innerHTML = `<span class="section-hint" style="color:#e0654e">${escapeHtml(err.message)}</span>`;
    return;
  }
  const available = new Set(builderOptions.games.map(gameKey));
  builderOpenGames = new Set([...builderOpenGames].filter(k => available.has(k)));
  const teamsAvailable = new Set(builderOptions.games.flatMap(g => [g.home, g.away]));
  const before = builderSlip.length;
  builderSlip = builderSlip.filter(l => teamsAvailable.has(l.team));
  renderBuilderGames();
  renderBuilderPlayers();
  renderBuilderSlip();
  if (builderSlip.length !== before) builderMsgEl.textContent = "Some legs were removed: their game is no longer available for this week.";
  scheduleBuilderEval();
}

function renderBuilderGames() {
  const games = builderOptions ? builderOptions.games : [];
  if (!games.length) {
    builderGamesEl.innerHTML = `<span class="section-hint">No games left to play this week.</span>`;
    return;
  }
  builderGamesEl.innerHTML = games.map(g => {
    const key = gameKey(g);
    const n = (g.players[g.home] || []).length + (g.players[g.away] || []).length;
    return `<button class="game-chip ${builderOpenGames.has(key) ? "selected" : ""}" data-game="${escapeHtml(key)}">
      ${escapeHtml(g.away)} @ ${escapeHtml(g.home)}<small>${new Date(g.kickoff).toLocaleString([], {weekday: "short", hour: "numeric", minute: "2-digit"})} · ${n} players</small></button>`;
  }).join("");
  builderGamesEl.querySelectorAll(".game-chip").forEach(btn => btn.addEventListener("click", () => {
    const key = btn.dataset.game;
    if (builderOpenGames.has(key)) builderOpenGames.delete(key); else builderOpenGames.add(key);
    renderBuilderGames();
    renderBuilderPlayers();
  }));
}

function propOptionLabel(o) {
  if (o.stat === "anytime_td") return o.book_price != null ? `${o.label} @ ${fmtOdds(o.book_price)}` : o.label;
  const line = o.book_line != null ? o.book_line : o.model_line;
  const price = o.book_price != null ? ` @ ${fmtOdds(o.book_price)}` : "";
  return `${o.direction} ${line} ${o.label}${price} (avg ${o.projected_avg})`;
}

function renderBuilderPlayers() {
  if (!builderOptions) { builderPlayersEl.innerHTML = ""; return; }
  const open = builderOptions.games.filter(g => builderOpenGames.has(gameKey(g)));
  if (!open.length) {
    builderPlayersEl.innerHTML = `<p class="section-hint">Select one or more games above to see their players.</p>`;
    return;
  }
  const inBook = Boolean(builderOptions.book);
  const inSlip = new Set(builderSlip.map(l => `${l.player_id}:${l.stat}`));
  builderPlayersEl.innerHTML = open.map(g => [g.away, g.home].map(team => {
    const rows = (g.players[team] || []).map(p => {
      // In sportsbook mode only props the book actually posts can be added.
      const props = inBook ? p.props.filter(o => o.book_price != null) : p.props;
      if (!props.length) return "";
      const opts = props.map(o => {
        const used = inSlip.has(`${p.id}:${o.stat}`);
        return `<option value="${escapeHtml(o.stat)}" ${used ? "disabled" : ""}>${escapeHtml(propOptionLabel(o))}${used ? " (in slip)" : ""}</option>`;
      }).join("");
      const injury = p.injury_status
        ? ` <span class="badge badge-out" title="${escapeHtml(p.injury_note || `ESPN lists this player as ${p.injury_status.toLowerCase()}`)}">${escapeHtml(p.injury_status.toLowerCase())}</span>`
        : "";
      return `<div class="player-row" data-player="${p.id}">
        <span class="player-name">${escapeHtml(p.name)} <span class="section-hint">${escapeHtml(p.position)} · ${p.projected_points_per_game} pts/g</span>${injury}</span>
        <select class="prop-select">${opts}</select>
        <button class="add-leg-btn">Add</button>
      </div>`;
    }).join("");
    return `<div class="builder-team"><h4>${escapeHtml(team)} · ${escapeHtml(g.away)} @ ${escapeHtml(g.home)}</h4>${rows || `<p class="section-hint">No props available${inBook ? " at this sportsbook" : ""} for this team.</p>`}</div>`;
  }).join("")).join("");

  builderPlayersEl.querySelectorAll(".player-row").forEach(row => {
    row.querySelector(".add-leg-btn").addEventListener("click", () => {
      const pid = Number(row.dataset.player);
      const stat = row.querySelector(".prop-select").value;
      const player = open.flatMap(g => Object.values(g.players).flat()).find(p => p.id === pid);
      const opt = player && player.props.find(o => o.stat === stat);
      if (!opt || builderSlip.some(l => l.player_id === pid && l.stat === stat)) return;
      if (builderSlip.length >= builderOptions.max_legs) {
        builderMsgEl.textContent = `A parlay can have at most ${builderOptions.max_legs} legs here.`;
        return;
      }
      builderSlip.push({
        player_id: pid, player: player.name, team: player.team, position: player.position,
        stat, label: opt.label, direction: opt.direction,
        line: opt.book_line != null ? opt.book_line : opt.model_line,
        book_line: opt.book_line != null ? opt.book_line : null,
      });
      builderMsgEl.textContent = "";
      renderBuilderPlayers();
      renderBuilderSlip();
      scheduleBuilderEval();
    });
  });
}

function renderBuilderSlip() {
  const n = builderSlip.length;
  const min = builderOptions ? builderOptions.min_legs : 2;
  builderSlipCount.textContent = n ? `${n} leg${n === 1 ? "" : "s"}${n < min ? ` (add ${min - n} more)` : ""}` : "";
  builderSaveBtn.disabled = !(builderLastEval && n >= min);
  if (!n) {
    builderSlipEl.innerHTML = `<p class="section-hint">Nothing yet - add players from the games you selected.</p>`;
    return;
  }
  builderSlipEl.innerHTML = builderSlip.map((l, idx) => {
    const lineCtl = l.stat === "anytime_td"
      ? ""
      : (l.book_line != null
          ? `<span class="section-hint" title="The sportsbook sets this line">${escapeHtml(l.direction)} ${l.line}</span>`
          : `<label class="section-hint">${escapeHtml(l.direction)} <input type="number" step="0.5" min="0.5" value="${l.line}" data-idx="${idx}" class="line-input" title="Edit the line - the probability updates"></label>`);
    return `<div class="slip-leg">
      <span class="slip-desc">${escapeHtml(l.player)} <span class="section-hint">(${escapeHtml(l.team)} ${escapeHtml(l.position)})</span> — ${escapeHtml(l.label)}</span>
      ${lineCtl}
      <button class="remove-btn" data-idx="${idx}" title="Remove this leg">✕</button>
    </div>`;
  }).join("");
  builderSlipEl.querySelectorAll(".remove-btn").forEach(btn => btn.addEventListener("click", () => {
    builderSlip.splice(Number(btn.dataset.idx), 1);
    renderBuilderPlayers();
    renderBuilderSlip();
    scheduleBuilderEval();
  }));
  builderSlipEl.querySelectorAll(".line-input").forEach(inp => inp.addEventListener("change", () => {
    const v = Number(inp.value);
    if (!(v > 0)) return;
    builderSlip[Number(inp.dataset.idx)].line = v;
    scheduleBuilderEval();
  }));
}

function slipPayload() {
  return {
    week: Number(weekSelect.value || currentWeek),
    book: bookSelect.value,
    legs: builderSlip.map(l => l.stat === "anytime_td"
      ? { player_id: l.player_id, stat: l.stat }
      : { player_id: l.player_id, stat: l.stat, line: l.line }),
  };
}

function scheduleBuilderEval() {
  clearTimeout(builderEvalTimer);
  builderEvalTimer = setTimeout(evaluateBuilderSlip, 250);
}

async function evaluateBuilderSlip() {
  builderLastEval = null;
  const min = builderOptions ? builderOptions.min_legs : 2;
  if (builderSlip.length < min) {
    builderResultEl.innerHTML = "";
    builderSaveBtn.disabled = true;
    return;
  }
  builderResultEl.innerHTML = `<p class="section-hint">Scoring...</p>`;
  try {
    const res = await apiPost("/api/builder/evaluate", slipPayload());
    builderLastEval = res;
    builderResultEl.innerHTML = renderParlayCard(res.parlay, "manual", { book: res.book });
    builderResultEl.querySelectorAll(".track-btn").forEach(btn => btn.addEventListener("click", () => {
      const leg = window._lastLegsById[btn.dataset.legKey];
      if (leg) trackLeg({ ...leg, week: res.week, sportsbook: res.book ? res.book.title : "" });
    }));
    builderSaveBtn.disabled = false;
    builderMsgEl.textContent = "";
  } catch (err) {
    builderResultEl.innerHTML = `<p style="color:#e0654e">${escapeHtml(err.message)}</p>`;
    builderSaveBtn.disabled = true;
  }
}

async function saveBuilderSlip() {
  const stakeRaw = document.getElementById("builderStake").value;
  const body = slipPayload();
  if (stakeRaw !== "") body.stake = Number(stakeRaw);
  builderSaveBtn.disabled = true;
  builderMsgEl.textContent = "Saving...";
  try {
    const res = await apiPost("/api/builder/save", body);
    const missingOdds = res.group.picks.filter(p => p.odds_american == null).length;
    builderMsgEl.textContent = `Saved as parlay group #${res.group_id} with ${res.pick_ids.length} picks on the My Picks tab` +
      (missingOdds ? ` - enter your sportsbook's odds on ${missingOdds} of them there to see the group's EV.` : ".");
  } catch (err) {
    builderMsgEl.textContent = err.message;
  } finally {
    builderSaveBtn.disabled = !builderLastEval;
  }
}

builderSaveBtn.addEventListener("click", saveBuilderSlip);
document.getElementById("builderClear").addEventListener("click", () => {
  builderSlip = [];
  builderLastEval = null;
  builderResultEl.innerHTML = "";
  builderMsgEl.textContent = "";
  renderBuilderPlayers();
  renderBuilderSlip();
});
weekSelect.addEventListener("change", loadBuilderOptions);
riskSelect.addEventListener("change", loadBuilderOptions);

bookSelect.addEventListener("change", () => {
  syncBookControls();
  generateParlays();
  loadBuilderOptions();
});
sortSelect.addEventListener("change", generateParlays);

document.getElementById("generate").addEventListener("click", generateParlays);
weekSelect.addEventListener("change", generateParlays);
document.getElementById("refreshData").addEventListener("click", async () => {
  await loadProjections(true);
  await generateParlays();
});

populateWeekSelect(1);
loadBooks();
loadProjections().then(generateParlays).then(loadBuilderOptions);
