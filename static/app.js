const statusEl = document.getElementById("status");
const parlaysEl = document.getElementById("parlays");
const playerTableWrap = document.getElementById("playerTableWrap");
const playerSearch = document.getElementById("playerSearch");
const weekSelect = document.getElementById("week");

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
  if (leg.stat === "anytime_td") {
    return `${leg.player} (${leg.team} ${leg.position}) — ${leg.label} ${sourceBadge}`;
  }
  return `${leg.player} (${leg.team} ${leg.position}) — ${leg.direction} ${leg.line} ${leg.label} ${sourceBadge}`;
}

function renderParlays(data) {
  if (data.error) {
    parlaysEl.innerHTML = `<p style="color:#e0654e">Error: ${data.error}</p>`;
    return;
  }
  if (!data.parlays.length) {
    parlaysEl.innerHTML = `<p>No parlays matched these filters (leg pool: ${data.leg_pool_size}). Try a different risk level or position.</p>`;
    return;
  }

  const cards = data.parlays.map((p, i) => {
    const legRows = p.legs.map(leg => `
      <div class="leg-row">
        <span class="leg-desc">${fmtLeg(leg)}</span>
        <span class="leg-prob">${(leg.probability * 100).toFixed(0)}%</span>
      </div>
    `).join("");

    const corrBadge = p.has_same_team_correlation
      ? `<span class="badge" title="Two legs share the same NFL team — their outcomes may be correlated, not independent as the math assumes">correlated legs</span>`
      : "";

    return `
      <div class="parlay-card">
        <div class="parlay-card__header">
          <div>#${i + 1} &middot; ${p.legs.length}-leg parlay ${corrBadge}</div>
          <div class="parlay-card__odds">Fair odds: ${p.estimated_decimal_odds}x (${p.estimated_american_odds > 0 ? "+" : ""}${p.estimated_american_odds})</div>
        </div>
        <div class="parlay-card__prob">Estimated combined hit probability: ${(p.combined_probability * 100).toFixed(1)}%</div>
        ${legRows}
      </div>
    `;
  }).join("");

  const sourceSummary = data.legs_from_weekly_projection > 0
    ? `${data.legs_from_weekly_projection} live weekly-projection legs, ${data.legs_from_season_pace} season-pace-estimate legs`
    : `all ${data.legs_from_season_pace} legs are season-pace estimates — ESPN hasn't published Week ${data.week} projections yet`;

  parlaysEl.innerHTML = `<p style="color:var(--muted); font-size:0.85rem">Leg pool size: ${data.leg_pool_size} candidates (${sourceSummary}) &middot; showing top ${data.parlays.length}</p>` + cards;
}

async function generateParlays() {
  const legs = document.getElementById("numLegs").value;
  const risk = document.getElementById("risk").value;
  const position = document.getElementById("position").value;
  const week = weekSelect.value || currentWeek;

  statusEl.textContent = "Generating...";
  parlaysEl.innerHTML = "";
  try {
    const res = await fetch(`/api/parlays?legs=${legs}&risk=${risk}&position=${position}&week=${week}`);
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
    playerTableWrap.innerHTML = `<p style="color:#e0654e">Error: ${data.error}</p>`;
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

document.getElementById("generate").addEventListener("click", generateParlays);
weekSelect.addEventListener("change", generateParlays);
document.getElementById("refreshData").addEventListener("click", async () => {
  await loadProjections(true);
  await generateParlays();
});

populateWeekSelect(1);
loadProjections().then(generateParlays);
