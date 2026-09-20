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

  const cards = data.parlays.map((p, i) => {
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
          <div>#${i + 1} &middot; ${p.legs.length}-leg parlay ${corrBadge}</div>
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
  }).join("");

  const sourceSummary = data.legs_from_weekly_projection > 0
    ? `${data.legs_from_weekly_projection} live weekly-projection legs, ${data.legs_from_season_pace} season-pace-estimate legs`
    : `all ${data.legs_from_season_pace} legs are season-pace estimates — ESPN hasn't published Week ${data.week} projections yet`;
  const gamesNote = data.games_remaining != null
    ? ` &middot; ${data.games_remaining} game${data.games_remaining === 1 ? "" : "s"} left to play this week (finished games are excluded)`
    : "";

  const bookNote = data.book
    ? `<p style="color:var(--muted); font-size:0.85rem">Priced at <strong>${escapeHtml(data.book.title)}</strong>'s real lines and odds &middot; ${data.book.props_total - data.book.props_unmatched - data.book.props_ambiguous} of ${data.book.props_total} props matched to a player &middot; prop odds fetched ${data.book.props_age_hours}h ago${data.book.props_partial ? " (partial - some games failed to load)" : ""}. Risk level doesn't apply here: the book sets the lines. The model behind the hit probabilities is a season-pace average with no matchup information (injuries only remove players who are out, and weather only adds warnings - neither changes a probability), so treat this as a way to build a slip at real prices, not as a betting edge.</p>`
    : "";

  const excluded = data.injury_excluded || [];
  const injuryNote = excluded.length
    ? `<p style="color:var(--muted); font-size:0.85rem" title="${escapeHtml(excluded.map(x => `${x.name} (${x.team}, ${x.status})`).join(", "))}">${excluded.length} player${excluded.length === 1 ? "" : "s"} ruled out (out / injured reserve / suspended) ${excluded.length === 1 ? "is" : "are"} left out of the pool: ${escapeHtml(excluded.slice(0, 6).map(x => `${x.name} (${x.status})`).join(", "))}${excluded.length > 6 ? `, and ${excluded.length - 6} more` : ""}.</p>`
    : "";

  parlaysEl.innerHTML = bookNote + injuryNote + `<p style="color:var(--muted); font-size:0.85rem">Leg pool size: ${data.leg_pool_size} candidates (${sourceSummary})${gamesNote} &middot; showing top ${data.parlays.length}</p>` + cards;

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

bookSelect.addEventListener("change", () => {
  syncBookControls();
  generateParlays();
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
loadProjections().then(generateParlays);
