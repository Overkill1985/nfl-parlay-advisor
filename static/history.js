const gradeNowBtn = document.getElementById("gradeNowBtn");
const gradeStatus = document.getElementById("gradeStatus");
const picksPerformanceWrap = document.getElementById("picksPerformanceWrap");
const calibrationWrap = document.getElementById("calibrationWrap");
const backtestForm = document.getElementById("backtestForm");
const backtestResultWrap = document.getElementById("backtestResultWrap");
const exportBtn = document.getElementById("exportBtn");

function bucketTable(title, rows, keyLabel) {
  if (!rows.length) return `<p>${escapeHtml(title)}: no graded picks yet.</p>`;
  const body = rows.map(r => `
    <tr>
      <td>${escapeHtml(String(r.key))}</td>
      <td>${r.total}</td>
      <td>${r.wins}</td>
      <td>${r.losses}</td>
      <td>${r.pushes}</td>
      <td>${r.win_rate != null ? fmtPct(r.win_rate) : "—"}</td>
    </tr>
  `).join("");
  return `
    <h3 style="font-size:0.9rem">${escapeHtml(title)}</h3>
    <table>
      <thead><tr><th>${escapeHtml(keyLabel)}</th><th>Total</th><th>W</th><th>L</th><th>Push</th><th>Win %</th></tr></thead>
      <tbody>${body}</tbody>
    </table>
  `;
}

function renderPicksPerformance(perf) {
  const legCountRows = perf.by_leg_count.map(r => ({
    key: `${r.leg_count}-leg`, total: r.total, wins: r.wins, losses: r.losses, pushes: 0, win_rate: r.win_rate,
  }));

  picksPerformanceWrap.innerHTML = `
    <div class="ev-panel">
      <div>Graded picks: <strong>${perf.graded_picks_count}</strong> &middot; Graded parlay groups: <strong>${perf.graded_groups_count}</strong></div>
      <div>Total staked: ${fmtMoney(perf.total_staked)} &middot; Total profit: <strong style="color:${perf.total_profit >= 0 ? "var(--good)" : "#e0654e"}">${fmtMoney(perf.total_profit)}</strong>
        ${perf.roi_pct != null ? `&middot; ROI ${fmtPct(perf.roi_pct)}` : ""}</div>
    </div>
    ${bucketTable("Win rate by number of legs", legCountRows, "Legs")}
    ${bucketTable("Win rate by market type", perf.by_market_type, "Market")}
    ${bucketTable("Win rate by direction", perf.by_direction, "Direction")}
    ${bucketTable("Win rate by team", perf.by_team, "Team")}
  `;
}

function renderCalibration(buckets) {
  if (!buckets.length) {
    calibrationWrap.innerHTML = "<p>No graded model snapshots yet. This fills in automatically as weeks are played and graded.</p>";
    return;
  }
  const rows = buckets.map(b => `
    <tr>
      <td>${b.bucket_low}–${b.bucket_high}%</td>
      <td>${b.total}</td>
      <td>${b.hits}</td>
      <td>${b.realized_hit_rate != null ? fmtPct(b.realized_hit_rate) : "—"}</td>
    </tr>
  `).join("");
  calibrationWrap.innerHTML = `
    <table>
      <thead><tr><th>Model confidence bucket</th><th>Legs</th><th>Hits</th><th>Realized hit rate</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

async function loadHistoryStats() {
  try {
    const data = await apiGet("/api/history/stats");
    renderPicksPerformance(data.picks_performance);
    renderCalibration(data.calibration);
  } catch (err) {
    picksPerformanceWrap.innerHTML = `<p style="color:#e0654e">Failed to load: ${escapeHtml(err.message)}</p>`;
  }
}

gradeNowBtn.addEventListener("click", async () => {
  gradeStatus.textContent = "Grading...";
  try {
    const result = await apiPost("/api/history/grade", {});
    gradeStatus.textContent = `Graded ${result.graded_picks} pick(s), ${result.graded_snapshots} model snapshot(s).`;
    await loadHistoryStats();
  } catch (err) {
    gradeStatus.textContent = `Error: ${err.message}`;
  }
});

backtestForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(backtestForm);
  const params = new URLSearchParams();
  for (const [key, value] of fd.entries()) params.set(key, value);

  backtestResultWrap.innerHTML = "Running...";
  try {
    const result = await apiGet(`/api/backtest/mechanical?${params.toString()}`);
    if (result.sample_size === 0) {
      backtestResultWrap.innerHTML = "<p>No qualifying player-weeks found for these filters.</p>";
      return;
    }
    backtestResultWrap.innerHTML = `
      <div class="ev-panel">
        <div>${escapeHtml(result.position || "All positions")} &middot; ${escapeHtml(result.stat)} &middot;
          ${escapeHtml(result.direction)} at ${fmtPct(result.threshold_pct, 0)} of trailing average &middot;
          ${result.season} weeks ${result.week_range[0]}-${result.week_range[1]}</div>
        <div>Sample size: <strong>${result.sample_size}</strong> player-weeks</div>
        <div>Hits: ${result.hits} &middot; Misses: ${result.misses} &middot; Pushes: ${result.pushes}</div>
        <div>Hit rate: <strong>${fmtPct(result.hit_rate)}</strong></div>
      </div>
    `;
  } catch (err) {
    backtestResultWrap.innerHTML = `<p style="color:#e0654e">${escapeHtml(err.message)}</p>`;
  }
});

exportBtn.addEventListener("click", async () => {
  try {
    const data = await apiGet("/api/history/export");
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `parlay-advisor-export-${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    URL.revokeObjectURL(url);
  } catch (err) {
    alert(`Export failed: ${err.message}`);
  }
});

window.addEventListener("tabshown", (e) => {
  if (e.detail.tab === "history") loadHistoryStats();
});
