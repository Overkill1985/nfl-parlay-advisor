// Shared helpers used across tabs: API calls, tab switching, small formatters.

async function apiGet(path) {
  const res = await fetch(path);
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

async function apiSend(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

const apiPost = (path, body) => apiSend("POST", path, body);
const apiPut = (path, body) => apiSend("PUT", path, body);
const apiDelete = (path) => apiSend("DELETE", path);

function escapeHtml(str) {
  if (str === null || str === undefined) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function fmtOdds(american) {
  if (american === null || american === undefined || Number.isNaN(american)) return "—";
  return american > 0 ? `+${american}` : `${american}`;
}

function fmtPct(fraction, digits = 1) {
  if (fraction === null || fraction === undefined) return "—";
  return `${(fraction * 100).toFixed(digits)}%`;
}

function fmtMoney(value) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const sign = value < 0 ? "-" : "";
  return `${sign}$${Math.abs(value).toFixed(2)}`;
}

// -- tab navigation -----------------------------------------------------

function initTabs() {
  const buttons = document.querySelectorAll(".tab-nav button");
  buttons.forEach(btn => {
    btn.addEventListener("click", () => switchTab(btn.dataset.tab));
  });
}

function switchTab(name) {
  document.querySelectorAll(".tab-nav button").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.tab === name);
  });
  document.querySelectorAll(".tab-panel").forEach(panel => {
    panel.classList.toggle("hidden", panel.id !== `tab-${name}`);
  });
  window.dispatchEvent(new CustomEvent("tabshown", { detail: { tab: name } }));
}

document.addEventListener("DOMContentLoaded", initTabs);
