const dashWeekSelect = document.getElementById("dashWeek");
const dashPositionSelect = document.getElementById("dashPosition");
const dashStatus = document.getElementById("dashStatus");
const injuryTableWrap = document.getElementById("injuryTableWrap");
const formTableWrap = document.getElementById("formTableWrap");
const weatherTableWrap = document.getElementById("weatherTableWrap");

let dashWeekInitialized = false;

function populateDashWeekSelect(current) {
  const previousValue = dashWeekSelect.value;
  dashWeekSelect.innerHTML = "";
  for (let w = 1; w <= 18; w++) {
    const opt = document.createElement("option");
    opt.value = w;
    opt.textContent = w === current ? `Week ${w} (current)` : `Week ${w}`;
    dashWeekSelect.appendChild(opt);
  }
  dashWeekSelect.value = dashWeekInitialized ? previousValue : String(current);
  dashWeekInitialized = true;
}

// Practice participation arrives as full sentences ("Did Not Participate In
// Practice"); the table only has room for the distinction that matters.
function shortPracticeStatus(status) {
  if (!status) return "—";
  const s = status.toLowerCase();
  if (s.includes("did not participate")) return "DNP";
  if (s.includes("limited")) return "Limited";
  if (s.includes("full")) return "Full";
  return status;
}

function renderInjuryFeedNote(feed) {
  if (!feed) return "";
  const parts = [];
  if (feed.last_fetched_at) {
    parts.push(`Updated ${new Date(feed.last_fetched_at * 1000).toLocaleString()}`);
  }
  if (feed.next_checkpoint) {
    parts.push(`next check ${new Date(feed.next_checkpoint).toLocaleString()}`);
  }
  parts.push(escapeHtml(feed.schedule || ""));
  let note = `<p class="section-hint">${parts.filter(Boolean).join(" · ")}</p>`;
  if (!feed.nflverse_available) {
    note += `<p class="section-hint">Practice participation (DNP/Limited/Full) comes from nflverse's official injury report, not available for this season yet${feed.nflverse_reason ? ` (${escapeHtml(feed.nflverse_reason)})` : ""} — showing ESPN's live feed only.</p>`;
  }
  if (!feed.espn_available) {
    note += `<p class="section-hint" style="color:#e0654e">ESPN's live injury feed is unavailable${feed.espn_reason ? ` (${escapeHtml(feed.espn_reason)})` : ""}.</p>`;
  }
  return note;
}

function renderInjuries(injuries, feed) {
  const note = renderInjuryFeedNote(feed);
  if (!injuries.length) {
    injuryTableWrap.innerHTML = note + "<p>No non-active injury designations reported for this filter.</p>";
    return;
  }
  const rows = injuries.map(p => {
    const severe = p.status === "OUT" || p.status === "IR" || p.status === "SUSPENDED";
    const injuryLabel = [p.injury, p.secondary_injury].filter(Boolean).join(" / ") || "—";
    return `
    <tr>
      <td>${escapeHtml(p.name)}</td>
      <td>${escapeHtml(p.team)}</td>
      <td>${escapeHtml(p.position)}</td>
      <td><span class="badge ${severe ? "badge-out" : ""}">${escapeHtml(p.status)}</span></td>
      <td>${escapeHtml(injuryLabel)}</td>
      <td title="${escapeHtml(p.practice_status || "")}">${escapeHtml(shortPracticeStatus(p.practice_status))}</td>
      <td title="${escapeHtml((p.sources || []).join(", "))}">${escapeHtml((p.sources || []).length > 1 ? "2 sources" : (p.sources || ["—"])[0])}</td>
    </tr>
    ${p.comment ? `<tr><td colspan="7" class="section-hint">${escapeHtml(p.comment)}</td></tr>` : ""}
  `;
  }).join("");
  injuryTableWrap.innerHTML = `
    ${note}
    <table>
      <thead>
        <tr>
          <th>Player</th><th>Team</th><th>Pos</th><th>Status</th>
          <th>Injury</th><th>Practice</th><th>Source</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

function fmtUsagePct(value) {
  return value != null ? `${(value * 100).toFixed(0)}%` : "—";
}

function renderForm(recentForm, usageAvailable, usageReason) {
  if (!recentForm.length) {
    formTableWrap.innerHTML = "<p>No games played yet this season for these players — check back once Week 1 is underway.</p>";
    return;
  }
  const usageNote = !usageAvailable
    ? `<p class="section-hint">Real usage data (snap %, target share, WOPR) from nflverse isn't available yet${usageReason ? ` (${escapeHtml(usageReason)})` : ""} — showing trailing-stat trend only.</p>`
    : "";
  const rows = recentForm.map(p => {
    const usage = p.nflverse_usage;
    return `
    <tr>
      <td>${escapeHtml(p.name)}</td>
      <td>${escapeHtml(p.team)}</td>
      <td>${escapeHtml(p.position)}</td>
      <td>${p.games_available} (wks ${p.weeks_used.join(", ")})</td>
      <td>${p.avg_points}</td>
      <td>${p.avg_stats.rush_att}</td>
      <td>${p.avg_stats.rush_yds}</td>
      <td>${p.avg_stats.receptions}</td>
      <td>${p.avg_stats.rec_yds}</td>
      <td title="Real per-game snap share from nflverse">${usage ? fmtUsagePct(usage.offense_pct) : "—"}</td>
      <td title="Real per-game target share from nflverse">${usage ? fmtUsagePct(usage.target_share) : "—"}</td>
      <td title="Weighted Opportunity Rating (target share + air yards share) from nflverse">${usage && usage.wopr != null ? usage.wopr.toFixed(2) : "—"}</td>
    </tr>
  `;
  }).join("");
  formTableWrap.innerHTML = `
    ${usageNote}
    <table>
      <thead>
        <tr>
          <th>Player</th><th>Team</th><th>Pos</th><th>Games</th><th>Avg Pts</th>
          <th>Rush Att</th><th>Rush Yds</th><th>Rec</th><th>Rec Yds</th>
          <th>Snap %</th><th>Tgt Share</th><th>WOPR</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

function renderWeather(weatherReports) {
  if (!weatherReports.length) {
    weatherTableWrap.innerHTML = "<p>No schedule data for this week.</p>";
    return;
  }
  const rows = weatherReports.map(w => {
    const conditionsLabel = w.conditions === "controlled"
      ? `${w.roof} (no weather risk)`
      : w.forecast_reliable
        ? (w.conditions === "forecast" ? "forecast available" : "fetch failed")
        : "too far out to forecast";
    const flags = (w.impact_flags || []).map(f => `<div class="warning-line warning-caution">${escapeHtml(f)}</div>`).join("");
    return `
      <tr>
        <td>${escapeHtml(w.away_team)} @ ${escapeHtml(w.home_team)}</td>
        <td>${w.game_date ? new Date(w.game_date).toLocaleString() : "—"}</td>
        <td>${escapeHtml(conditionsLabel)}</td>
        <td>${w.temp_f != null ? w.temp_f + "°F" : "—"}</td>
        <td>${w.wind_mph != null ? w.wind_mph + " mph" : "—"}</td>
        <td>${w.precip_in != null ? w.precip_in + " in" : "—"}</td>
      </tr>
      ${flags ? `<tr><td colspan="6">${flags}</td></tr>` : ""}
    `;
  }).join("");
  weatherTableWrap.innerHTML = `
    <table>
      <thead><tr><th>Game</th><th>Kickoff (local browser time)</th><th>Conditions</th><th>Temp</th><th>Wind</th><th>Precip</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

async function loadDashboard() {
  // On the very first load we don't know the real current week yet - omit
  // it so the server picks its own default instead of us guessing "1".
  const weekParam = dashWeekInitialized ? `week=${dashWeekSelect.value}&` : "";
  const position = dashPositionSelect.value;
  dashStatus.textContent = "Loading...";
  try {
    const data = await apiGet(`/api/dashboard?${weekParam}position=${position}`);
    populateDashWeekSelect(data.current_week);
    renderInjuries(data.injuries, data.injury_feed);
    renderForm(data.recent_form, data.nflverse_usage_available, data.nflverse_usage_reason);
    renderWeather(data.weather);
    dashStatus.textContent = `Week ${data.week} · ${data.injuries.length} injury note(s) · ${data.weather.length} game(s)`;
  } catch (err) {
    dashStatus.textContent = "";
    injuryTableWrap.innerHTML = `<p style="color:#e0654e">Failed to load dashboard: ${escapeHtml(err.message)}</p>`;
  }
}

populateDashWeekSelect(1);
document.getElementById("dashRefresh").addEventListener("click", loadDashboard);
dashWeekSelect.addEventListener("change", loadDashboard);
dashPositionSelect.addEventListener("change", loadDashboard);

window.addEventListener("tabshown", (e) => {
  if (e.detail.tab === "dashboard") loadDashboard();
});
