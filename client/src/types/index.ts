export type MissionStatus = "queued" | "running" | "waiting" | "complete" | "failed" | "canceled";
export type StepStatus = "started" | "complete" | "failed" | "canceled";

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
  capabilities: string[];
  safe_actions: string[];
  configured?: boolean;
  tool_count?: number;
  auth_required?: boolean;
}
