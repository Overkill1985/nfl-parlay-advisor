// Build Your Own Parlay tab. Owns its own Week / Odds / Risk controls, and
// shares the card renderer (renderParlayCard), leg formatting and Track
// wiring with parlays.js, which loads first. Loads its data the first time
// the tab is shown, like the other tabs.

(() => {
const weekSelect = document.getElementById("builderWeek");
const bookSelect = document.getElementById("builderBook");
const riskSelect = document.getElementById("builderRisk");
const builderStatusEl = document.getElementById("builderStatus");
let currentWeek = 1;

function populateBuilderWeekSelect(current) {
  const previous = weekSelect.value;
  currentWeek = current;
  weekSelect.innerHTML = "";
  for (let w = 1; w <= 18; w++) {
    const opt = document.createElement("option");
    opt.value = w;
    opt.textContent = w === current ? `Week ${w} (current)` : `Week ${w}`;
    weekSelect.appendChild(opt);
  }
  weekSelect.value = previous && [...weekSelect.options].some(o => o.value === previous) ? previous : String(current);
}

async function loadBuilderBooks() {
  // Which sportsbooks have prop odds cached (fetched on the Parlays tab -
  // this tab never spends credits). Local status only.
  let status = null;
  try { status = await apiGet("/api/odds/books"); } catch (err) { status = null; }
  const previous = bookSelect.value;
  const books = status ? status.books : [];
  bookSelect.innerHTML = `<option value="">Model (fair odds)</option>` +
    books.map(b => `<option value="${escapeHtml(b.key)}">${escapeHtml(b.title)}</option>`).join("");
  bookSelect.value = books.some(b => b.key === previous) ? previous : "";
  if (status && status.api_key_configured && !status.available) {
    builderStatusEl.textContent = "No sportsbook prop odds loaded yet - fetch them on the Parlays tab to build at a book's real lines.";
  } else if (status && status.available) {
    builderStatusEl.textContent = `Prop odds fetched ${status.age_hours}h ago cover ${status.upcoming_events} upcoming game${status.upcoming_events === 1 ? "" : "s"} (refresh them on the Parlays tab).`;
  } else {
    builderStatusEl.textContent = "";
  }
  riskSelect.disabled = Boolean(bookSelect.value);
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

let builderLoaded = false;
async function showBuilderTab() {
  if (!builderLoaded) {
    builderLoaded = true;
    try {
      const proj = await apiGet("/api/projections");
      populateBuilderWeekSelect(proj.current_week);
    } catch (err) { populateBuilderWeekSelect(1); }
  }
  await loadBuilderBooks();
  await loadBuilderOptions();
}

weekSelect.addEventListener("change", loadBuilderOptions);
riskSelect.addEventListener("change", loadBuilderOptions);
bookSelect.addEventListener("change", () => {
  riskSelect.disabled = Boolean(bookSelect.value);
  riskSelect.title = bookSelect.value ? "The sportsbook sets the lines" : "";
  loadBuilderOptions();
});
window.addEventListener("tabshown", (e) => {
  if (e.detail.tab === "builder") showBuilderTab();
});
})();
