/* AUTOPILOT Dashboard — app.js
   Real-time mission control with SSE streaming and animated UI. */

"use strict";

// ── State ───────────────────────────────────────────────────────────────
let selectedMissionId = null;
let allMissions = [];
let eventSource = null;

// ── DOM helpers ─────────────────────────────────────────────────────────
const $ = (id) => document.getElementById(id);

async function fetchJson(url, options = {}) {
  const res = await fetch(url, options);
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

// ── Bootstrap ────────────────────────────────────────────────────────────
async function init() {
  await renderConnectors();
  await refresh();
  await loadProvider();
  startSSE();
}

// ── Provider badge ───────────────────────────────────────────────────────
async function loadProvider() {
  try {
    const data = await fetchJson("/api/provider");
    $("providerBadge").textContent = data.provider || "—";
  } catch {
    $("providerBadge").textContent = "heuristic";
  }
}

// ── Connectors ───────────────────────────────────────────────────────────
async function renderConnectors() {
  const connectors = await fetchJson("/api/connectors");
  $("connectors").innerHTML = connectors.map((c) => `
    <div class="connector">
      <div class="connector-name">${c.name}</div>
      <div class="meta">${c.description}</div>
      <div class="cap-chips">
        ${(c.capabilities || []).map((cap) => `<span class="cap-chip">${cap}</span>`).join("")}
      </div>
    </div>
  `).join("") || `<p class="meta" style="padding:8px">No connectors registered.</p>`;
}

// ── Mission list ─────────────────────────────────────────────────────────
function renderMissions(missions) {
  allMissions = missions;
  $("missionCount").textContent = missions.length;

  $("missions").innerHTML = missions.map((m) => `
    <div class="mission-card${selectedMissionId === m.id ? " selected" : ""}" data-id="${m.id}">
      <div class="mission-title">${escHtml(m.title)}</div>
      <div class="badges">
        ${pill(m.status, m.status)}
        ${pill(m.severity, m.severity)}
        ${pill(`${(+m.confidence * 100).toFixed(0)}%`, "")}
      </div>
      <div class="meta" style="margin-bottom:4px">${escHtml(m.summary || "").slice(0, 90)}${(m.summary || "").length > 90 ? "…" : ""}</div>
      <div class="meta">replans ${m.replans} · ${relativeTime(m.updated_at)}</div>
    </div>
  `).join("") || `<p class="meta" style="padding:8px 4px">No missions yet.</p>`;

  document.querySelectorAll(".mission-card").forEach((card) => {
    card.addEventListener("click", () => selectMission(card.dataset.id));
  });

  // Auto-select first running mission or first mission
  if (!selectedMissionId && missions.length > 0) {
    const running = missions.find((m) => m.status === "running" || m.status === "queued");
    selectMission((running || missions[0]).id);
  } else if (selectedMissionId) {
    // Re-select to refresh detail
    selectMission(selectedMissionId, { silent: true });
  }

  // Update header status dot
  const hasActive = missions.some((m) => m.status === "running" || m.status === "queued");
  const dot = $("statusDot");
  const label = $("statusLabel");
  dot.className = "status-dot" + (hasActive ? " running" : missions.length ? " active" : "");
  label.textContent = hasActive ? "running" : missions.length ? "ready" : "idle";
}

// ── Mission detail ────────────────────────────────────────────────────────
async function selectMission(id, opts = {}) {
  selectedMissionId = id;

  // Mark selected card
  document.querySelectorAll(".mission-card").forEach((c) => {
    c.classList.toggle("selected", c.dataset.id === id);
  });

  let data;
  try {
    data = await fetchJson(`/api/missions/${id}`);
  } catch {
    return;
  }

  const m = data.mission;
  const steps = data.steps || [];
  const traces = data.traces || [];

  $("selectedStatus").textContent = m.status;
  $("selectedStatus").className = `status-pill ${m.status}`;

  $("missionDetail").innerHTML = `
    <h3 style="font-size:15px;font-weight:800;margin-bottom:8px">${escHtml(m.title)}</h3>
    <div class="badges" style="margin-bottom:10px">
      ${pill(m.status, m.status)}
      ${pill(m.severity, m.severity)}
      ${pill(`confidence ${(m.confidence * 100).toFixed(0)}%`, "")}
      ${pill(`replans ${m.replans}`, "")}
      ${pill(`${m.signals?.length || 0} signals`, "")}
    </div>
    <p class="meta" style="margin-bottom:4px">${escHtml(m.summary || "")}</p>

    ${m.signals?.length ? `
      <div class="section-title">Signals</div>
      ${m.signals.map((s) => `
        <div class="signal-card">
          <div class="card-title">${escHtml(s.source)} / <span style="color:var(--muted)">${escHtml(s.type)}</span></div>
          <div class="card-body">${escHtml(s.summary)}</div>
          ${s.entities?.length ? `<div class="meta" style="margin-top:4px">${s.entities.map(escHtml).join(" · ")}</div>` : ""}
        </div>
      `).join("")}
    ` : ""}

    ${steps.length ? `
      <div class="section-title">Execution Graph</div>
      <div class="timeline">
        ${steps.map((s) => `
          <div class="step ${s.status}">
            <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
              <div class="step-name">${escHtml(s.name)}</div>
              ${pill(s.status, s.status)}
            </div>
            <div class="step-role">${escHtml(s.role)}</div>
            ${s.output_summary ? `<div class="step-output" style="margin-top:6px">${escHtml(s.output_summary)}</div>` : ""}
          </div>
        `).join("")}
      </div>
    ` : ""}

    ${m.hypotheses?.length ? `
      <div class="section-title">Hypotheses</div>
      ${m.hypotheses.map((h) => `
        <div class="hyp-card">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
            <div class="card-title">${escHtml(h.title)}</div>
            ${pill((h.confidence * 100).toFixed(0) + "%", "")}
          </div>
          <div class="card-body">${escHtml(h.rationale)}</div>
          <div class="conf-bar-wrap"><div class="conf-bar" style="width:${Math.round(h.confidence * 100)}%"></div></div>
        </div>
      `).join("")}
    ` : ""}

    ${m.evidence?.length ? `
      <div class="section-title">Evidence · ${m.evidence.length} item${m.evidence.length !== 1 ? "s" : ""}</div>
      ${m.evidence.map((ev) => `
        <div class="evidence-card">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
            <div class="card-title">${escHtml(ev.title)}</div>
            ${pill((ev.confidence * 100).toFixed(0) + "%", "")}
          </div>
          <div class="card-body">${escHtml(ev.summary)}</div>
          <div class="meta" style="margin-top:4px">${escHtml(ev.source)}${ev.url ? ` · <a href="${escHtml(ev.url)}" target="_blank" rel="noopener" style="color:var(--accent)">${escHtml(ev.url.replace(/^https?:\/\//, "").slice(0, 50))}</a>` : ""}</div>
          <div class="conf-bar-wrap"><div class="conf-bar" style="width:${Math.round(ev.confidence * 100)}%"></div></div>
        </div>
      `).join("")}
    ` : `<div class="section-title">Evidence</div><p class="meta" style="padding:4px 0">Collecting…</p>`}

    ${m.actions?.length ? `
      <div class="section-title">Actions Taken</div>
      ${m.actions.map((a) => `
        <div class="action-card">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
            <div class="card-title">${escHtml(a.action)}</div>
            ${pill(a.status, a.status)}
          </div>
          <div class="card-body">${escHtml(a.summary)}</div>
          ${a.artifact_path ? `<div class="meta" style="margin-top:4px;font-family:var(--mono);font-size:11px">${escHtml(a.artifact_path)}</div>` : ""}
        </div>
      `).join("")}
    ` : ""}
  `;

  // Render trace in right panel
  renderTraces(traces);
}

// ── Trace panel ──────────────────────────────────────────────────────────
function renderTraces(traces) {
  $("traceCount").textContent = traces.length;
  $("traceList").innerHTML = traces.slice(0, 30).map((t) => `
    <div class="trace-item ${t.status}">
      <span style="color:var(--text-2)">${escHtml(t.name)}</span>
      <span style="opacity:0.5"> · ${t.status}</span>
      <span style="opacity:0.35;float:right">${shortTime(t.created_at)}</span>
    </div>
  `).join("") || `<p class="meta" style="padding:4px 8px">No traces yet.</p>`;
}

// ── SSE real-time updates ────────────────────────────────────────────────
function startSSE() {
  if (eventSource) eventSource.close();
  eventSource = new EventSource("/api/events");
  eventSource.onmessage = (ev) => {
    try {
      const data = JSON.parse(ev.data);
      if (data.missions) renderMissions(data.missions);
      if (data.traces) renderTraces(data.traces);
    } catch { /* ignore parse errors */ }
  };
  eventSource.onerror = () => {
    // Reconnect after 3s
    setTimeout(startSSE, 3000);
  };
}

// ── Full refresh ─────────────────────────────────────────────────────────
async function refresh() {
  try {
    const missions = await fetchJson("/api/missions");
    renderMissions(missions);
  } catch (err) {
    console.error("Refresh failed:", err);
  }
}

// ── Demo button ───────────────────────────────────────────────────────────
$("demoBtn").addEventListener("click", async () => {
  $("demoBtn").disabled = true;
  $("demoBtn").textContent = "Firing…";
  try {
    await fetchJson("/demo/fire", { method: "POST" });
    await refresh();
  } catch (err) {
    alert("Demo failed: " + err.message);
  } finally {
    setTimeout(() => {
      $("demoBtn").disabled = false;
      $("demoBtn").innerHTML = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg> Run Demo`;
    }, 2000);
  }
});

$("refreshBtn").addEventListener("click", refresh);

// ── Helpers ──────────────────────────────────────────────────────────────
function escHtml(str = "") {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function pill(text, cls = "") {
  return `<span class="badge ${escHtml(cls)}">${escHtml(String(text))}</span>`;
}

function relativeTime(iso) {
  if (!iso) return "—";
  const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  return `${Math.floor(diff / 3600)}h ago`;
}

function shortTime(iso) {
  if (!iso) return "";
  try { return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }); }
  catch { return ""; }
}

// ── Start ────────────────────────────────────────────────────────────────
init();
