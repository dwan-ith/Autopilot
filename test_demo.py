import httpx, time, json

r = httpx.post("http://localhost:8090/demo/fire", timeout=10)
print("Demo response:", r.json())

print("\nWaiting 35s for full pipeline to complete...")
time.sleep(35)

missions = httpx.get("http://localhost:8090/api/missions", timeout=10).json()
print(f"\nMissions: {len(missions)}")

if not missions:
    print("NO MISSIONS FOUND")
    exit(1)

m = missions[0]
print(f"Title: {m.get('title', '?')}")
print(f"Status: {m.get('status', '?')}")
print(f"Severity: {m.get('severity', '?')}")
print(f"Confidence: {m.get('confidence', 0)}")
print(f"Agent runs: {len(m.get('agent_runs', []))}")
print(f"Evidence: {len(m.get('evidence', []))}")
print(f"Graph nodes: {len(m.get('graph', []))}")
print(f"Actions: {len(m.get('actions', []))}")
print(f"Policy decisions: {len(m.get('policy_decisions', []))}")
print(f"Approvals: {len(m.get('approvals', []))}")
print(f"Memory notes: {m.get('memory_notes', [])}")
print(f"Replans: {m.get('replans', 0)}")

print("\n--- Agent Runs ---")
for run in m.get("agent_runs", []):
    print(f"  [{run.get('role')}] {run.get('objective', '')[:60]} | tools={run.get('tool_calls',0)} conf={run.get('confidence',0):.2f}")

print("\n--- Actions ---")
for a in m.get("actions", []):
    print(f"  [{a.get('connector')}] {a.get('action')}: {a.get('status')} -- {a.get('summary', '')[:80]}")

print("\n--- Graph (last 10) ---")
for n in m.get("graph", [])[-10:]:
    print(f"  [{n.get('kind')}] {n.get('title', '')[:55]} -> {n.get('status')}")

print("\nDONE")
