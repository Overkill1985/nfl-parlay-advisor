const pickForm = document.getElementById("pickForm");
const pickFormStatus = document.getElementById("pickFormStatus");
const picksTableWrap = document.getElementById("picksTableWrap");
const groupForm = document.getElementById("groupForm");
const groupsWrap = document.getElementById("groupsWrap");

let cachedGroups = [];

// Called by parlays.js's "Track" button to carry a model-generated leg's
// numbers over into the manual-entry form, so the user only has to add the
// sportsbook + real odds rather than retyping player/stat/line by hand.
function prefillPickForm(leg) {
  const form = pickForm;
  form.market_type.value = "player_prop";
  form.player_name.value = leg.player || "";
  form.team.value = leg.team || "";
  form.week.value = leg.week || "";
  form.label.value = leg.label || "";
  form.direction.value = leg.direction === "Yes" ? "Yes" : (leg.direction || "Over");
  form.line_entered.value = leg.line ?? "";
  form.model_line.value = leg.line ?? "";
  form.model_probability.value = leg.probability ?? "";
  form.stat_hidden = leg.stat || "";
  pickFormStatus.textContent = `Prefilled from model leg: ${leg.player || ""}`;
}
window.prefillPickForm = prefillPickForm;

function groupOptions(selectedId) {
  const none = `<option value="" ${!selectedId ? "selected" : ""}>— none —</option>`;
  const opts = cachedGroups.map(g =>
    `<option value="${g.id}" ${g.id === selectedId ? "selected" : ""}>#${g.id} ${escapeHtml(g.sportsbook || "")}</option>`
  ).join("");
  return none + opts;
}

function pickRowActions(pick) {
  return `
    <select class="grade-select" data-id="${pick.id}">
      ${["considered", "placed", "graded_win", "graded_loss", "graded_push"].map(s =>
        `<option value="${s}" ${s === pick.status ? "selected" : ""}>${s.replace("_", " ")}</option>`
      ).join("")}
    </select>
    <select class="group-select" data-id="${pick.id}" title="Assign to a parlay group">
      ${groupOptions(pick.parlay_group_id)}
    </select>
    <input type="number" class="closing-input" data-id="${pick.id}" placeholder="closing odds"
           value="${pick.closing_odds_american ?? ""}" step="1" title="Closing line odds (American)">
    <button class="save-btn" data-id="${pick.id}">Save</button>
    <button class="delete-btn" data-id="${pick.id}">Delete</button>
  `;
}

function renderPicksTable(picks) {
  if (!picks.length) {
    picksTableWrap.innerHTML = "<p>No picks tracked yet. Add one above, or click \"Track\" on a leg in the Parlays tab.</p>";
    return;
  }

  const rows = picks.map(p => `
    <tr>
      <td>${escapeHtml(p.market_type)}</td>
      <td>${escapeHtml(p.player_name || (p.team && p.opponent ? `${p.team} vs ${p.opponent}` : p.team) || "—")}</td>
      <td>${p.week ?? "—"}</td>
      <td>${escapeHtml(p.direction || "")} ${p.line_entered ?? ""} ${escapeHtml(p.label || "")}</td>
      <td>${escapeHtml(p.sportsbook || "—")}</td>
      <td>${fmtOdds(p.odds_american)}</td>
      <td>${fmtOdds(p.closing_odds_american)}</td>
      <td>${p.model_probability != null ? fmtPct(p.model_probability) : "—"}</td>
      <td>${pickRowActions(p)}</td>
    </tr>
  `).join("");

  picksTableWrap.innerHTML = `
    <table>
      <thead>
        <tr>
          <th>Market</th><th>Player / Game</th><th>Wk</th><th>Pick</th>
          <th>Book</th><th>Odds</th><th>Closing</th><th>Model %</th><th>Status / Group / Actions</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
  `;

  picksTableWrap.querySelectorAll(".save-btn").forEach(btn => {
    btn.addEventListener("click", async () => {
      const id = btn.dataset.id;
      const status = picksTableWrap.querySelector(`.grade-select[data-id="${id}"]`).value;
      const closingRaw = picksTableWrap.querySelector(`.closing-input[data-id="${id}"]`).value;
      const groupRaw = picksTableWrap.querySelector(`.group-select[data-id="${id}"]`).value;
      const fields = { status, parlay_group_id: groupRaw ? Number(groupRaw) : null };
      if (closingRaw !== "") fields.closing_odds_american = Number(closingRaw);
      try {
        await apiPut(`/api/picks/${id}`, fields);
        await loadPicks();
      } catch (err) {
        alert(`Failed to save: ${err.message}`);
      }
    });
  });

  picksTableWrap.querySelectorAll(".delete-btn").forEach(btn => {
    btn.addEventListener("click", async () => {
      if (!confirm("Delete this tracked pick?")) return;
      try {
        await apiDelete(`/api/picks/${btn.dataset.id}`);
        await loadPicks();
      } catch (err) {
        alert(`Failed to delete: ${err.message}`);
      }
    });
  });
}

async function loadPicks() {
  try {
    const data = await apiGet("/api/picks");
    renderPicksTable(data.picks);
  } catch (err) {
    picksTableWrap.innerHTML = `<p style="color:#e0654e">Failed to load picks: ${escapeHtml(err.message)}</p>`;
  }
}

pickForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(pickForm);
  const fields = {};
  for (const [key, value] of fd.entries()) {
    if (value === "" || key === "source_snapshot_leg") continue;
    if (["week", "odds_american"].includes(key)) fields[key] = parseInt(value, 10);
    else if (["line_entered", "model_probability", "model_line"].includes(key)) fields[key] = parseFloat(value);
    else fields[key] = value;
  }
  if (pickForm.stat_hidden) fields.stat = pickForm.stat_hidden;

  pickFormStatus.textContent = "Saving...";
  try {
    await apiPost("/api/picks", fields);
    pickForm.reset();
    pickFormStatus.textContent = "Saved.";
    await loadPicks();
  } catch (err) {
    pickFormStatus.textContent = `Error: ${err.message}`;
  }
});

// -- parlay groups --------------------------------------------------------

function renderEvPanel(container, ev) {
  if (ev.error) {
    container.innerHTML = `<p class="ev-panel">${escapeHtml(ev.error)}</p>`;
    return;
  }
  const edgeColor = ev.edge >= 0 ? "var(--good)" : "#e0654e";
  const warningLines = (ev.correlation_warnings || []).map(w => `
    <div class="warning-line warning-${w.severity}">${escapeHtml(w.message)}</div>
  `).join("");
  const scriptLines = (ev.game_script || []).map(g => `
    <div class="game-script-line">${escapeHtml(g.summary)}</div>
  `).join("");

  container.innerHTML = `
    <div class="ev-panel">
      <div><strong>${ev.num_legs}</strong> leg(s) with odds entered
        ${ev.excluded_legs.length ? `(${ev.excluded_legs.length} excluded — no odds yet)` : ""}</div>
      <div>Combined odds: ${ev.combined_decimal_odds}x (${fmtOdds(ev.combined_american_odds)})</div>
      <div>Implied probability (from your odds): ${fmtPct(ev.combined_implied_probability)}</div>
      <div>Model probability: ${fmtPct(ev.combined_model_probability)}
        ${!ev.all_legs_have_model_estimate ? '<span class="badge" title="Some legs have no model estimate — their implied probability was used as a no-edge assumption">partial model</span>' : ""}</div>
      <div>Edge (model − implied): <strong style="color:${edgeColor}">${ev.edge >= 0 ? "+" : ""}${fmtPct(ev.edge)}</strong></div>
      <div>Stake: ${fmtMoney(ev.stake)} &rarr; Potential payout: ${fmtMoney(ev.potential_payout)} (profit ${fmtMoney(ev.potential_profit)})</div>
      <div>Expected value: <strong style="color:${ev.expected_value >= 0 ? "var(--good)" : "#e0654e"}">${fmtMoney(ev.expected_value)}</strong> (${fmtPct(ev.expected_value_pct)} of stake)</div>
      <div>Risk score: ${ev.risk_score}/100</div>
      ${warningLines}
      ${scriptLines}
    </div>
  `;
}

function renderGroups(groups) {
  if (!groups.length) {
    groupsWrap.innerHTML = "<p>No parlay groups yet. Create one above, then assign tracked picks to it.</p>";
    return;
  }

  groupsWrap.innerHTML = groups.map(g => `
    <div class="parlay-card" id="group-${g.id}">
      <div class="parlay-card__header">
        <div>#${g.id} &middot; ${escapeHtml(g.sportsbook || "no sportsbook set")} &middot; stake ${fmtMoney(g.total_stake)}</div>
        <div>
          <button class="calc-ev-btn" data-id="${g.id}">Calculate EV</button>
          <button class="delete-group-btn" data-id="${g.id}">Delete</button>
        </div>
      </div>
      <div class="parlay-card__prob">${g.picks.length} pick(s): ${g.picks.map(p => escapeHtml(p.player_name || p.market_type)).join(", ") || "none assigned"}</div>
      ${escapeHtml(g.notes || "")}
      <div class="ev-container" data-id="${g.id}"></div>
    </div>
  `).join("");

  groupsWrap.querySelectorAll(".calc-ev-btn").forEach(btn => {
    btn.addEventListener("click", async () => {
      const container = groupsWrap.querySelector(`.ev-container[data-id="${btn.dataset.id}"]`);
      container.innerHTML = "Calculating...";
      try {
        const ev = await apiGet(`/api/parlay-groups/${btn.dataset.id}/ev`);
        renderEvPanel(container, ev);
      } catch (err) {
        container.innerHTML = `<p style="color:#e0654e">${escapeHtml(err.message)}</p>`;
      }
    });
  });

  groupsWrap.querySelectorAll(".delete-group-btn").forEach(btn => {
    btn.addEventListener("click", async () => {
      if (!confirm("Delete this parlay group? Picks in it will be unassigned, not deleted.")) return;
      try {
        await apiDelete(`/api/parlay-groups/${btn.dataset.id}`);
        await loadGroups();
        await loadPicks();
      } catch (err) {
        alert(`Failed to delete: ${err.message}`);
      }
    });
  });
}

async function loadGroups() {
  try {
    const data = await apiGet("/api/parlay-groups");
    cachedGroups = data.parlay_groups;
    // Groups list doesn't include picks by default - fetch each one's detail
    // so the summary line and EV panel have something to show immediately.
    const detailed = await Promise.all(cachedGroups.map(g => apiGet(`/api/parlay-groups/${g.id}`)));
    renderGroups(detailed);
  } catch (err) {
    groupsWrap.innerHTML = `<p style="color:#e0654e">Failed to load parlay groups: ${escapeHtml(err.message)}</p>`;
  }
}

groupForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(groupForm);
  const fields = {};
  for (const [key, value] of fd.entries()) {
    if (value === "") continue;
    fields[key] = key === "total_stake" ? parseFloat(value) : value;
  }
  try {
    await apiPost("/api/parlay-groups", fields);
    groupForm.reset();
    await loadGroups();
    await loadPicks(); // group dropdown options changed
  } catch (err) {
    alert(`Failed to create group: ${err.message}`);
  }
});

window.addEventListener("tabshown", (e) => {
  if (e.detail.tab === "picks") {
    loadGroups().then(loadPicks);
  }
});
