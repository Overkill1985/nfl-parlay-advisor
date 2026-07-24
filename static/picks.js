const pickForm = document.getElementById("pickForm");
const pickFormStatus = document.getElementById("pickFormStatus");
const picksTableWrap = document.getElementById("picksTableWrap");

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

function pickRowActions(pick) {
  return `
    <select class="grade-select" data-id="${pick.id}">
      ${["considered", "placed", "graded_win", "graded_loss", "graded_push"].map(s =>
        `<option value="${s}" ${s === pick.status ? "selected" : ""}>${s.replace("_", " ")}</option>`
      ).join("")}
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
          <th>Book</th><th>Odds</th><th>Closing</th><th>Model %</th><th>Status / Actions</th>
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
      const fields = { status };
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

window.addEventListener("tabshown", (e) => {
  if (e.detail.tab === "picks") loadPicks();
});
