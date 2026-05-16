export type MissionStatus = "queued" | "running" | "waiting" | "complete" | "failed" | "canceled";
export type StepStatus = "started" | "complete" | "failed" | "canceled";
export type GraphNodeKind = "signal" | "hypothesis" | "branch" | "subagent" | "operator" | "replan" | "action" | "policy" | "validation";

export interface Signal {
  id: string;
  source: string;
  type: string;
  summary: string;
  entities: string[];
  urgency: string;
  received_at: string;
}

export interface Hypothesis {
  id: string;
  title: string;
  rationale: string;
  confidence: number;
  evidence_ids: string[];
}

export interface Evidence {
  id: string;
  source: string;
  title: string;
  summary: string;
  url?: string;
  confidence: number;
}

export interface ActionResult {
  id: string;
  connector: string;
  action: string;
  status: string;
  summary: string;
  artifact_path?: string;
}

export interface ActionApproval {
  id: string;
  mission_id: string;
  mission_title?: string;
  connector: string;
  action: string;
  payload: Record<string, unknown>;
  risk: string;
  reason: string;
  status: "pending" | "executed" | "rejected" | "failed";
  requested_at: string;
  decided_at?: string;
  decided_by?: string;
  result?: ActionResult;
  error?: string;
  confidence?: number;
}

export interface MissionGraphNode {
  id: string;
  kind: GraphNodeKind;
  title: string;
  status: StepStatus;
  parent_ids: string[];
  branch_id?: string;
  summary: string;
  ref_id?: string;
  metadata: Record<string, unknown>;
  created_at: string;
  completed_at?: string;
}

export interface AgentRun {
  id: string;
  role: string;
  status: StepStatus;
  hypothesis_id?: string;
  objective: string;
  tools: string[];
  tool_calls: number;
  output_summary: string;
  confidence: number;
  duration_ms: number;
  error?: string;
  metadata: Record<string, unknown>;
  created_at: string;
  completed_at?: string;
}

export interface Mission {
  id: string;
  title: string;
  status: MissionStatus;
  severity: string;
  summary: string;
  confidence: number;
  replans: number;
  created_at: string;
  updated_at: string;
  signals: Signal[];
  hypotheses: Hypothesis[];
  evidence: Evidence[];
  actions: ActionResult[];
  approvals: ActionApproval[];
  graph: MissionGraphNode[];
  agent_runs: AgentRun[];
}

export interface Trace {
  id: string;
  name: string;
  status: string;
  created_at: string;
  payload: Record<string, unknown>;
}

export interface Connector {
  name: string;
  description: string;
  category?: string;
  auth_mode?: string;
  capabilities: string[];
  scopes?: string[];
  objects?: string[];
  safe_actions: string[];
  tools?: ConnectorToolSpec[];
  configured?: boolean;
  readiness?: ConnectorReadiness;
  tool_count?: number;
  auth_required?: boolean;
}

export interface ConnectorReadiness {
  configured: boolean;
  action_ready: boolean;
  missing: string[];
  mode: string;
  detail: string;
  action?: string;
  /** True when outbound calls use real credentials (OAuth/token/env), not anonymous/fallback tiers */
  integration_live?: boolean;
}

export interface ConnectorToolSpec {
  name: string;
  description: string;
  capability: string;
  input_schema: Record<string, string>;
  output: string;
  risk: string;
  requires_confirmation: boolean;
  mcp_tool: boolean;
  read_only_hint: boolean;
  destructive_hint: boolean;
}

export interface ConnectorDirectoryItem {
  id: string;
  name: string;
  category: string;
  description: string;
  icon: string;
  auth_mode: string;
  capabilities: string[];
  event_types: string[];
  scopes: string[];
  safe_actions: string[];
  implemented_actions: string[];
  objects: string[];
  tools: ConnectorToolSpec[];
  demo_available: boolean;
  implemented: boolean;
  status: string;
  connected_at?: string;
  granted_scopes: string[];
  connection_auth_mode?: string;
  credentials_ref?: string;
  connection_metadata?: Record<string, unknown>;
  /** True when a usable OAuth access token exists in SQLite for this connector id */
  oauth_token_present?: boolean;
  /** Directory row reflects non-demo credentials (OAuth token store or saved refs); webhook/API rows may still require runtime env */
  live_connected?: boolean;
  /** Persisted demo / simulated connection row */
  is_demo_connection?: boolean;
}
