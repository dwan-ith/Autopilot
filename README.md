# AUTOPILOT

**Autonomous Operator Runtime** — turns events from connected systems into verified, policy-bounded actions.

```
connected surface → normalized signal → mission graph → dynamic operators → capability router → bounded action → trace + memory
```

## What it does

AUTOPILOT ingests operational signals from any webhook-compatible source, correlates related events into a unified **Mission**, then autonomously runs a pipeline of **LLM-powered operators** to investigate, reason, and act — without human steering.

Each operator is a distinct AI agent with its own system prompt and cognitive role:

| Operator | Role |
|---|---|
| Signal Evaluator | Triage severity and blast radius |
| Mission Planner | Decompose incident into competing hypotheses |
| Investigator | Generate search queries, gather evidence via connectors |
| Verification Gate | Critically evaluate evidence, score confidence |
| Adaptive Replanner | Fill evidence gaps when confidence is low |
| Synthesis Operator | Write the actionable mission brief |
| Action Publisher | Execute bounded writes and notifications |

## Quickstart

**Prerequisites:** Python 3.11+, an OpenRouter or Groq API key.

```bash
git clone https://github.com/dwan-ith/Autopilot
cd Autopilot
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env            # Fill in OPENROUTER_API_KEY or GROQ_API_KEY
python run_server.py
```

Open **http://127.0.0.1:8080** and click **Run Demo**.

AUTOPILOT will:
1. Fire three async operational signals (support escalation, error spike, rollout status)
2. Correlate them into a single mission via entity matching
3. Run the full operator pipeline with real LLM reasoning
4. Write an artifact report and send a notification
5. Display the live execution graph in the dashboard

## Webhook

Send any operational event to AUTOPILOT:

```bash
curl -X POST http://127.0.0.1:8080/webhooks/custom \
  -H "Content-Type: application/json" \
  -d '{"type":"support_escalation","summary":"Enterprise customer reports failed exports after rollout.","entities":["export service","rollout"],"urgency":"high"}'
```

## LLM Providers

AUTOPILOT auto-detects the first available provider:

| Provider | Key | Default model |
|---|---|---|
| **OpenRouter** (recommended) | `OPENROUTER_API_KEY` | `google/gemini-2.5-flash` |
| **Groq** | `GROQ_API_KEY` | `llama-3.3-70b-versatile` |

Without any key, all operators fall back to deterministic heuristics (useful for offline demo).

## Connectors

| Connector | Capabilities | Notes |
|---|---|---|
| `webhook` | read | Generic inbound event normalizer |
| `knowledge` | search, read | Local runbooks + optional Tavily live search |
| `artifact` | write, action | Writes durable markdown reports to `artifacts/` |
| `notification` | notify, action | Slack webhook or local file fallback |

## Architecture

```
Connector Registry          declares capabilities per connector
Normalized Object Model     Signal → Mission → Evidence → ActionResult
Runtime Kernel              crash-safe async task loop with SQLite persistence
Operator Suite              6 LLM-powered agents + action publisher
Capability Router           matches mission needs to connector methods
Policy Layer                only safe_actions are permitted per connector
Trace Sink                  every step emitted to SQLite + Omium-ready hook
```

## Tests

```bash
$env:PYTHONPATH = "src"   # PowerShell
# or
export PYTHONPATH=src     # bash

python -m unittest discover -s tests -v
```

## Dependencies

```
fastapi, uvicorn, pydantic, python-dotenv, httpx, python-multipart
```

No LLM SDK required — calls are made directly to the provider's OpenAI-compatible REST API via `httpx`.

## Omium Tracing

Set `OMIUM_API_KEY` in `.env`. Every operator step, webhook receipt, and action is emitted to the local `TraceSink` which is wired to emit to Omium when the key is present.

## License

MIT
