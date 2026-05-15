export type MissionStatus = "queued" | "running" | "waiting" | "complete" | "failed";
export type StepStatus = "started" | "complete" | "failed";

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

export interface OperatorStep {
  id: string;
  mission_id: string;
  name: string;
  role: string;
  status: StepStatus;
  input_summary: string;
  output_summary: string;
  created_at: string;
}

export interface Trace {
  id: string;
  name: string;
  status: string;
  created_at: string;
  payload: any;
}

export interface Connector {
  name: string;
  description: string;
  capabilities: string[];
  safe_actions: string[];
}
