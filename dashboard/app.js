let selectedMission = null;

const $ = (id) => document.getElementById(id);

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

function badge(text, cls = "") {
  return `<span class="badge ${cls}">${text}</span>`;
}

function renderMissions(missions) {
  $("missionCount").textContent = missions.length;
  $("missions").innerHTML = missions.map((mission) => `
    <div class="mission-card" data-id="${mission.id}">
      <div class="mission-title">${mission.title}</div>
      <div>${badge(mission.status, mission.status)}${badge(mission.severity, mission.severity)}${badge(`confidence ${Number(mission.confidence).toFixed(2)}`)}</div>
      <div class="meta">${mission.summary || ""}</div>
      <div class="meta">replans: ${mission.replans} · updated ${new Date(mission.updated_at).toLocaleTimeString()}</div>
    </div>
  `).join("") || `<p class="empty">No missions yet.</p>`;

  document.querySelectorAll(".mission-card").forEach((card) => {
    card.addEventListener("click", () => selectMission(card.dataset.id));
  });

  if (!selectedMission && missions[0]) selectMission(missions[0].id);
}

async function renderConnectors() {
  const connectors = await fetchJson("/api/connectors");
  $("connectors").innerHTML = connectors.map((connector) => `
    <div class="connector">
      <div class="mission-title">${connector.name}</div>
      <div class="meta">${connector.description}</div>
      <div class="meta">capabilities: ${connector.capabilities.join(", ")}</div>
      <div class="meta">safe actions: ${(connector.safe_actions || []).join(", ") || "none"}</div>
    </div>
  `).join("");
}

async function selectMission(id) {
  selectedMission = id;
  const data = await fetchJson(`/api/missions/${id}`);
  const mission = data.mission;
  $("selectedStatus").textContent = mission.status;
  $("missionDetail").innerHTML = `
    <h3>${mission.title}</h3>
    <div>${badge(mission.status, mission.status)}${badge(mission.severity, mission.severity)}${badge(`confidence ${mission.confidence.toFixed(2)}`)}${badge(`replans ${mission.replans}`)}</div>
    <p class="meta">${mission.summary}</p>

    <div class="section-title">Signals</div>
    ${mission.signals.map((signal) => `<div class="evidence"><strong>${signal.source}/${signal.type}</strong><p>${signal.summary}</p><div class="meta">${signal.entities.join(", ")}</div></div>`).join("")}

    <div class="section-title">Mission Graph</div>
    ${data.steps.map((step) => `<div class="step"><strong>${step.name}</strong> ${badge(step.status, step.status)}<p>${step.output_summary || step.input_summary}</p><div class="meta">${step.role}</div></div>`).join("")}

    <div class="section-title">Hypotheses</div>
    ${mission.hypotheses.map((hyp) => `<div class="hypothesis"><strong>${hyp.title}</strong> ${badge(hyp.confidence.toFixed(2))}<p>${hyp.rationale}</p></div>`).join("")}

    <div class="section-title">Evidence</div>
    ${mission.evidence.map((ev) => `<div class="evidence"><strong>${ev.title}</strong> ${badge(ev.confidence.toFixed(2))}<p>${ev.summary}</p><div class="meta">${ev.source}${ev.url ? ` · ${ev.url}` : ""}</div></div>`).join("") || `<p class="empty">No evidence yet.</p>`}

    <div class="section-title">Actions</div>
    ${mission.actions.map((action) => `<div class="action-card"><strong>${action.action}</strong> ${badge(action.status, action.status)}<p>${action.summary}</p><div class="meta">${action.artifact_path || ""}</div></div>`).join("") || `<p class="empty">No actions yet.</p>`}
  `;
}

async function refresh() {
  const missions = await fetchJson("/api/missions");
  renderMissions(missions);
  if (selectedMission) {
    try { await selectMission(selectedMission); } catch (_) {}
  }
}

$("demoBtn").addEventListener("click", async () => {
  await fetchJson("/demo/fire", { method: "POST" });
  await refresh();
});

$("refreshBtn").addEventListener("click", refresh);

renderConnectors();
refresh();

const events = new EventSource("/api/events");
events.onmessage = (event) => {
  const payload = JSON.parse(event.data);
  renderMissions(payload.missions);
  if (selectedMission) selectMission(selectedMission).catch(() => {});
};
