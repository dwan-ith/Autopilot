# AUTOPILOT — Autonomous Operator Runtime

**AUTOPILOT** is a production-grade, multi-agent autonomous operator that ingests operational signals, investigates them in parallel across 13+ connected systems, and executes policy-gated actions — without human intervention in the critical path.

---

## Architecture

```
Inbound Signal (webhook / API / manual)
        │
        ▼
┌─────────────────────────────────────────────────────────────────┐
│                       RUNTIME KERNEL                            │
│                                                                 │
│  ① CORRELATOR ──► deduplicate, classify, link related signals  │
│  ② MEMORY     ──► recall patterns from past missions           │
│  ③ PLANNER    ──► generate investigation hypotheses (DAG)      │
│  ④ INVESTIGATOR ► parallel evidence gathering (SubAgents)      │
│  ⑤ VERIFIER   ──► confidence scoring gate                      │
│  ⑥ GOVERNOR   ──► policy + risk decision (allow / block)       │
│  ⑦ EXECUTOR   ──► bounded side-effect execution                │
│  ⑧ VALIDATOR  ──► post-action validation + monitoring          │
│  ⑨ ORCHESTRATOR ► full lifecycle coordination + replanning     │
└─────────────────────────────────────────────────────────────────┘
        │
        ▼
Connected Systems (13 connectors)
```

### 9-Agent Swarm

| Agent | Role | Type |
|---|---|---|
| **Correlator** | Signal dedup + classification + mission linking | Persistent |
| **Memory** | Cross-mission pattern recall | Persistent |
| **Governor** | Risk-based policy enforcement | Persistent |
| **Planner** | Hypothesis generation + branch DAG | Mission |
| **Investigator** | Parallel evidence gathering via tools | Mission |
| **Verifier** | Evidence confidence scoring gate | Mission |
| **Executor** | Bounded action execution | Mission |
| **Validator** | Post-action monitoring | Mission |
| **Orchestrator** | Full lifecycle + replanning | Runtime |

### LLM Provider Pool

- **6 slots** across OpenRouter (×3) and Groq (×3)
- **Role-pinned routing** — each agent maps to a fixed slot to avoid rate-limit contention
- **Heuristic fallback** — all agents operate without LLM if provider pool is exhausted

---

## Connector Matrix

| Connector | Type | Auth | Tools | Status |
|---|---|---|---|---|
| **GitHub** | Engineering | API Key | `github_search_issues`, `github_read_issue` | ✅ Live |
| **Knowledge** | Runbooks | Local + Tavily | `knowledge_search` | ✅ Live |
| **Tavily** | Web Search | API Key | `tavily_web_search` | ✅ Live |
| **Notion** | Knowledge | API Key | `notion_search` | ✅ Live |
| **Weather** | Observability | API Key | `weather_search` | ✅ Live |
| **Gmail** | Communication | OAuth2 | `gmail_search_threads` | ✅ Live |
| **Google Drive** | Knowledge | OAuth2 | `drive_search_files` | ✅ Live |
| **Artifact** | System | None | — | ✅ Always ready |
| **Notification** | Communication | Webhook | — | ✅ Slack + fallback |
| **Linear** | Engineering | API Key | — | ✅ Live |
| **CloudInfra** | Deployment | Webhook | — | ✅ Live |
| **PagerDuty** | Observability | Webhook | — | ✅ Live |
| **Sentry** | Observability | Webhook | — | ✅ Live |

---

## Setup

### Prerequisites
- Python 3.11+
- Node.js 18+
- (Optional) Docker

### 1. Backend

```bash
# Install dependencies
pip install -e ".[dev]"

# Configure environment
cp .env.example .env
# Edit .env with your API keys (see Environment Variables below)

# Run the API
python run_server.py
# → http://localhost:8090
```

### 2. Frontend

```bash
cd client
npm install
npm run dev
# → http://localhost:3000
```

### 3. Docker (production)

```bash
docker compose up --build
```

---

## Environment Variables

```bash
# ── LLM Providers (at least one required) ──────────────────────
OPENROUTER_API_KEY=sk-or-...          # Primary reasoning
OPENROUTER_API_KEY_2=sk-or-...        # Parallel slot 2
OPENROUTER_API_KEY_3=sk-or-...        # Parallel slot 3
GROQ_API_KEY=gsk_...                  # Fast inference slot 1
GROQ_API_KEY_2=gsk_...                # Fast inference slot 2
GROQ_API_KEY_3=gsk_...                # Fast inference slot 3

# ── Google OAuth2 (for Gmail + Drive) ──────────────────────────
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
AUTOPILOT_OAUTH_REDIRECT_URI=http://localhost:8090/oauth/callback/google

# ── Connectors (optional — graceful degradation if missing) ────
NOTION_API_KEY=secret_...
TAVILY_API_KEY=tvly-...
OPENWEATHER_API_KEY=...
GITHUB_TOKEN=ghp_...
GITHUB_REPO=owner/repo                # Default repo for issue creation
LINEAR_API_KEY=lin_api_...
LINEAR_TEAM_ID=...
SLACK_WEBHOOK_URL=https://hooks.slack.com/...

# ── Security ───────────────────────────────────────────────────
AUTOPILOT_API_KEY=your-secret-key     # Required for write endpoints
AUTOPILOT_AUTO_APPROVE_ACTIONS=false  # Set to 1 only for demos

# ── Deployment ─────────────────────────────────────────────────
DEPLOYMENT_WEBHOOK_URL=...            # Or use GitHub Actions vars below
GITHUB_REPOSITORY=owner/repo
GITHUB_WORKFLOW_ID=deploy.yml
```

---

## API Reference

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Runtime health + provider name |
| `POST` | `/api/signals` | Ingest a signal and spawn a mission |
| `POST` | `/demo/fire` | Fire the 3-signal demo scenario |
| `GET` | `/api/missions` | List all missions |
| `GET` | `/api/missions/{id}` | Mission detail + graph + actions |
| `GET` | `/api/connectors` | Live connector readiness |
| `GET` | `/api/connector-directory` | Full connector catalog |
| `POST` | `/api/approvals/{id}/approve` | Approve a policy-gated action |
| `POST` | `/api/approvals/{id}/reject` | Reject a policy-gated action |
| `GET` | `/api/analytics/missions` | Mission performance stats |
| `GET` | `/api/analytics/agents` | Per-agent performance |
| `GET` | `/api/analytics/connectors` | Connector health stats |
| `GET` | `/oauth/authorize/{connector_id}` | Start OAuth flow (gmail, google_drive) |
| `GET` | `/oauth/status` | OAuth authorization status |
| `POST` | `/webhooks/{connector_name}` | Inbound webhook receiver |

### Ingest a signal

```bash
curl -X POST http://localhost:8090/api/signals \
  -H "Content-Type: application/json" \
  -H "x-autopilot-key: your-secret-key" \
  -d '{
    "source": "sentry",
    "type": "error.spike",
    "summary": "API error rate jumped from 1% to 38%",
    "entities": ["checkout-service", "payments"],
    "urgency": "high"
  }'
```

---

## Policy Engine

Every action is risk-classified before execution:

| Action | Risk | Confidence Required | Validation Required |
|---|---|---|---|
| `write_report` | LOW | 0% | No |
| `write_action_packet` | LOW | 0% | No |
| `notify_ops` | MEDIUM | 55% | No |
| `webhook_callback` | MEDIUM | 60% | No |
| `create_issue` | MEDIUM | 72% | Yes |
| `post_message` | MEDIUM | 65% | No |
| `trigger_deployment` | HIGH | 85% | Yes + human |

HIGH-risk actions always require explicit human approval via the dashboard.

---

## Tests

```bash
pytest tests/ -v
# 8/8 pass
```

---

## License

MIT
