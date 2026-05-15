"use strict";

let selectedMissionId = null;
let eventSource = null;

const $ = (id) => document.getElementById(id);

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) throw new Error(`${response.status} ${await response.text()}`);
  return response.json();
}

async function init() {
  await renderConnectors();
  await loadProvider();
  await refresh();
  startSse();
}

async function loadProvider() {
  try {
    const data = await fetchJson("/api/provider");
    $("providerBadge").textContent = data.provider || "heuristic";
  } catch {
    $("providerBadge").textContent = "heuristic";
  }
}

async function renderConnectors() {
  const connectors = await fetchJson("/api/connectors");
  $("connectors").innerHTML = connectors.map((connector) => `
    <div class="connector">
      <div class="connector-name">${esc(connector.name)}</div>
      <div class="muted">${esc(connector.description)}</div>
      <div class="chips">
        ${(connector.capabilities || []).map((cap) => `<span>${esc(cap)}</span>`).join("")}
      </div>
      <div class="muted">safe: ${(connector.safe_actions || []).map(esc).join(", ") || "none"}</div>
    </div>
  `).join("");
}

function renderMissions(missions) {
  $("missionCount").textContent = missions.length;
  $("missions").innerHTML = missions.map((mission) => `
    <button class="mission-card ${selectedMissionId === mission.id ? "selected" : ""}" data-id="${mission.id}">
      <span class="mission-title">${esc(mission.title)}</span>
      <span class="row">
        ${badge(mission.status, mission.status)}
        ${badge(mission.severity, mission.severity)}
        ${badge(`${Math.round(Number(mission.confidence) * 100)}%`)}
      </span>
      <span class="muted">${esc(shorten(mission.summary || "", 110))}</span>
      <span class="muted">replans ${mission.replans} | ${relativeTime(mission.updated_at)}</span>
    </button>
  `).join("") || `<p class="muted pad">No missions yet.</p>`;

  document.querySelectorAll(".mission-card").forEach((card) => {
    card.addEventListener("click", () => selectMission(card.dataset.id));
  });

  if (!selectedMissionId && missions.length) selectMission(missions[0].id);
  updateRuntimeStatus(missions);
}

async function selectMission(id) {
  selectedMissionId = id;
  document.querySelectorAll(".mission-card").forEach((card) => {
    card.classList.toggle("selected", card.dataset.id === id);
  });

  const data = await fetchJson(`/api/missions/${id}`);
  const mission = data.mission;
  $("selectedStatus").textContent = mission.status;
  $("selectedStatus").className = `status-pill ${mission.status}`;

  $("missionDetail").innerHTML = `
    <div class="mission-header">
      <h3>${esc(mission.title)}</h3>
      <div class="row">
        ${badge(mission.status, mission.status)}
        ${badge(mission.severity, mission.severity)}
        ${badge(`confidence ${Math.round(mission.confidence * 100)}%`)}
        ${badge(`${mission.signals.length} signals`)}
        ${badge(`${mission.replans} replans`)}
      </div>
      <p class="muted">${esc(mission.summary)}</p>
    </div>

    ${section("Mission Graph", renderGraph(mission.graph || [], data.steps || []))}
    ${section("Signals", renderSignals(mission.signals || []))}
    ${section("Hypotheses", renderHypotheses(mission.hypotheses || []))}
    ${section("Evidence", renderEvidence(mission.evidence || []))}
    ${section("Policy", renderPolicy(mission.policy_decisions || []))}
    ${section("Actions", renderActions(mission.actions || []))}
  `;

  renderTraces(data.traces || []);
}

function renderGraph(graph, steps) {
  const nodes = graph.length ? graph : steps.map((step) => ({
    title: step.name,
    kind: "operator",
    status: step.status,
    summary: step.output_summary || step.role,
  }));
  return `<div class="timeline">${nodes.map((node) => `
    <article class="timeline-node ${node.status}">
      <div class="node-top">
        <strong>${esc(node.title)}</strong>
        ${badge(node.kind || "operator")}
        ${badge(node.status, node.status)}
      </div>
      <p>${esc(node.summary || "")}</p>
    </article>
  `).join("")}</div>`;
}

function renderSignals(signals) {
  return signals.map((signal) => card(
    `${signal.source} / ${signal.type}`,
    signal.summary,
    signal.entities.join(", ")
  )).join("") || empty("No signals.");
}

function renderHypotheses(hypotheses) {
  return hypotheses.map((hypothesis) => card(
    `${hypothesis.title} (${Math.round(hypothesis.confidence * 100)}%)`,
    hypothesis.rationale,
    progress(hypothesis.confidence)
  )).join("") || empty("No hypotheses.");
}

function renderEvidence(evidence) {
  return evidence.map((item) => card(
    `${item.title} (${Math.round(item.confidence * 100)}%)`,
    item.summary,
    `${esc(item.source)}${item.url ? ` | ${esc(item.url)}` : ""}${progress(item.confidence)}`
  )).join("") || empty("No evidence.");
}

function renderPolicy(decisions) {
  return decisions.map((decision) => card(
    `${decision.allowed ? "allowed" : "blocked"}: ${decision.connector}.${decision.action}`,
    decision.reason,
    `required ${decision.confidence_required.toFixed(2)} | observed ${decision.confidence_observed.toFixed(2)}`
  )).join("") || empty("No policy decisions yet.");
}

function renderActions(actions) {
  return actions.map((action) => card(
    `${action.connector}.${action.action}`,
    action.summary,
    esc(action.artifact_path || action.status)
  )).join("") || empty("No actions yet.");
}

function renderTraces(traces) {
  $("traceCount").textContent = traces.length;
  $("traceList").innerHTML = traces.slice(0, 40).map((trace) => `
    <div class="trace-item ${trace.status}">
      <span>${esc(trace.name)}</span>
      <span class="muted">${esc(trace.status)} | ${shortTime(trace.created_at)}</span>
    </div>
  `).join("") || empty("No traces.");
}

function startSse() {
  if (eventSource) eventSource.close();
  eventSource = new EventSource("/api/events");
  eventSource.onmessage = (event) => {
    const payload = JSON.parse(event.data);
    if (payload.missions) renderMissions(payload.missions);
    if (selectedMissionId) selectMission(selectedMissionId).catch(() => {});
  };
  eventSource.onerror = () => setTimeout(startSse, 2500);
}

async function refresh() {
  const missions = await fetchJson("/api/missions");
  renderMissions(missions);
}

$("demoBtn").addEventListener("click", async () => {
  $("demoBtn").disabled = true;
  try {
    await fetchJson("/demo/fire", { method: "POST" });
    await refresh();
  } catch (error) {
    alert(`Demo failed: ${error.message}`);
  } finally {
    setTimeout(() => { $("demoBtn").disabled = false; }, 1200);
  }
});

$("refreshBtn").addEventListener("click", refresh);

function updateRuntimeStatus(missions) {
  const active = missions.some((mission) => ["queued", "running", "waiting"].includes(mission.status));
  $("statusLabel").textContent = active ? "running" : missions.length ? "ready" : "idle";
  $("statusDot").className = `status-dot ${active ? "running" : missions.length ? "ready" : ""}`;
}

function section(title, body) {
  return `<section class="detail-section"><h4>${title}</h4>${body}</section>`;
}

function card(title, body, footer = "") {
  return `<article class="info-card"><strong>${esc(title)}</strong><p>${esc(body)}</p>${footer ? `<div class="muted">${footer}</div>` : ""}</article>`;
}

function badge(text, cls = "") {
  return `<span class="badge ${esc(cls)}">${esc(String(text))}</span>`;
}

function progress(value) {
  const width = Math.max(0, Math.min(100, Math.round(Number(value) * 100)));
  return `<div class="bar"><span style="width:${width}%"></span></div>`;
}

function empty(text) {
  return `<p class="muted pad">${esc(text)}</p>`;
}

function esc(value = "") {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function shorten(text, max) {
  return text.length > max ? `${text.slice(0, max - 3)}...` : text;
}

function relativeTime(iso) {
  if (!iso) return "unknown";
  const seconds = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  return `${Math.floor(seconds / 3600)}h ago`;
}

function shortTime(iso) {
  return iso ? new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "";
}

init();
