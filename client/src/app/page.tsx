"use client";

import { useState, useMemo, useEffect } from "react";
import {
  Play,
  Activity,
  TerminalSquare,
  ShieldCheck,
  Search,
  Database,
  Cpu,
  CheckCircle2,
  AlertCircle,
  Clock,
  XCircle,
  Wrench,
  ChevronDown,
  ChevronRight,
  GitBranch,
  Zap,
  ExternalLink,
} from "lucide-react";
import { formatDistanceToNow } from "date-fns";
import { useAutopilot } from "@/hooks/useAutopilot";
import { cn } from "@/lib/utils";
import { Mission, MissionStatus, StepStatus } from "@/types";

// ── Types ─────────────────────────────────────────────────────────────────────

interface ConnectorFull {
  name: string;
  description: string;
  capabilities: string[];
  safe_actions: string[];
  configured: boolean;
  tool_count: number;
  auth_required: boolean;
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function Dashboard() {
  const { missions, traces, connectors, provider, runDemo } = useAutopilot();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [mounted, setMounted] = useState(false);
  const [activeTab, setActiveTab] = useState<"graph" | "evidence" | "actions">("graph");

  useEffect(() => { setMounted(true); }, []);

  const selectedMission = useMemo(
    () => missions.find((m) => m.id === selectedId) || missions[0],
    [missions, selectedId]
  );

  useEffect(() => {
    if (!selectedId && missions.length > 0) setSelectedId(missions[0].id);
  }, [missions, selectedId]);

  if (!mounted) return null;

  const connectorsFull = connectors as unknown as ConnectorFull[];
  const configuredCount = connectorsFull.filter((c) => c.configured).length;
  const toolCount = connectorsFull.reduce((s, c) => s + (c.tool_count || 0), 0);
  const runningCount = missions.filter((m) => m.status === "running").length;

  return (
    <div className="min-h-screen bg-background flex flex-col text-sm">
      {/* ── Header ── */}
      <header className="border-b bg-background sticky top-0 z-50">
        <div className="max-w-[1600px] mx-auto px-4 h-12 flex items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-2">
              <div className="bg-foreground text-background p-1 rounded">
                <TerminalSquare className="h-3.5 w-3.5" />
              </div>
              <span className="font-bold tracking-tight">AUTOPILOT</span>
            </div>
            <div className="h-4 w-px bg-border hidden sm:block" />
            <div className="hidden sm:flex items-center gap-1.5 text-xs text-muted-foreground">
              <Cpu className="h-3 w-3" />
              <span>{provider}</span>
            </div>
            <div className="hidden md:flex items-center gap-1.5 text-xs text-muted-foreground">
              <Wrench className="h-3 w-3" />
              <span>{toolCount} tools</span>
            </div>
            <div className="hidden md:flex items-center gap-1.5 text-xs text-muted-foreground">
              <Database className="h-3 w-3" />
              <span>{configuredCount}/{connectorsFull.length} connectors active</span>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <span className={cn(
              "flex items-center gap-1.5 text-xs font-medium px-2 py-0.5 rounded-md border",
              runningCount > 0
                ? "bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-950/20 dark:text-amber-400 dark:border-amber-800"
                : "bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-950/20 dark:text-emerald-400 dark:border-emerald-800"
            )}>
              <span className={cn("h-1.5 w-1.5 rounded-full", runningCount > 0 ? "bg-amber-500 animate-pulse" : "bg-emerald-500")} />
              {runningCount > 0 ? `${runningCount} running` : "ready"}
            </span>
            <button
              onClick={runDemo}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-foreground text-background text-xs font-semibold hover:opacity-90 transition-opacity"
            >
              <Play className="h-3 w-3 fill-current" />
              Run Demo
            </button>
          </div>
        </div>
      </header>

      {/* ── Main ── */}
      <div className="flex-1 max-w-[1600px] mx-auto w-full grid grid-cols-1 lg:grid-cols-[280px_1fr_260px] divide-x">

        {/* ── Left: Mission List ── */}
        <nav className="overflow-y-auto max-h-[calc(100vh-48px)] sticky top-12">
          <div className="px-3 pt-4 pb-2 flex items-center justify-between">
            <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Missions</span>
            <span className="text-xs text-muted-foreground tabular-nums">{missions.length}</span>
          </div>
          <div className="px-2 pb-4 space-y-1">
            {missions.length === 0 ? (
              <div className="mx-1 mt-2 rounded-lg border border-dashed p-6 text-center text-xs text-muted-foreground">
                No missions yet.<br />Click "Run Demo" to start.
              </div>
            ) : (
              missions.map((m) => (
                <MissionRow
                  key={m.id}
                  mission={m}
                  selected={selectedId === m.id}
                  onClick={() => setSelectedId(m.id)}
                />
              ))
            )}
          </div>
        </nav>

        {/* ── Center: Detail ── */}
        <main className="overflow-y-auto max-h-[calc(100vh-48px)] p-6">
          {selectedMission ? (
            <div className="max-w-3xl mx-auto space-y-6">
              {/* Mission header */}
              <div className="space-y-3">
                <div className="flex items-start justify-between gap-4">
                  <h1 className="text-lg font-semibold leading-snug">{selectedMission.title}</h1>
                  <StatusBadge status={selectedMission.status} />
                </div>
                <div className="flex flex-wrap gap-2">
                  <SeverityBadge severity={selectedMission.severity} />
                  <span className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-md border bg-background">
                    <ShieldCheck className="h-3 w-3" />
                    {(selectedMission.confidence * 100).toFixed(0)}% confidence
                  </span>
                  {selectedMission.replans > 0 && (
                    <span className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-md border bg-background text-muted-foreground">
                      <GitBranch className="h-3 w-3" />
                      {selectedMission.replans} replan{selectedMission.replans > 1 ? "s" : ""}
                    </span>
                  )}
                </div>
                {selectedMission.summary && (
                  <p className="text-xs text-muted-foreground leading-relaxed">{selectedMission.summary}</p>
                )}
              </div>

              {/* Tabs */}
              <div className="border-b flex gap-0">
                {(["graph", "evidence", "actions"] as const).map((tab) => (
                  <button
                    key={tab}
                    onClick={() => setActiveTab(tab)}
                    className={cn(
                      "px-4 py-2 text-xs font-medium border-b-2 transition-colors capitalize",
                      activeTab === tab
                        ? "border-foreground text-foreground"
                        : "border-transparent text-muted-foreground hover:text-foreground"
                    )}
                  >
                    {tab === "graph" ? "Execution Graph" : tab === "evidence" ? `Evidence (${selectedMission.evidence?.length || 0})` : `Actions (${selectedMission.actions?.length || 0})`}
                  </button>
                ))}
              </div>

              {/* Tab content */}
              {activeTab === "graph" && (
                <ExecutionGraph mission={selectedMission} />
              )}
              {activeTab === "evidence" && (
                <EvidencePanel mission={selectedMission} />
              )}
              {activeTab === "actions" && (
                <ActionsPanel mission={selectedMission} />
              )}
            </div>
          ) : (
            <div className="h-full flex items-center justify-center text-muted-foreground text-sm">
              Select a mission from the sidebar.
            </div>
          )}
        </main>

        {/* ── Right: Connectors + Traces ── */}
        <aside className="overflow-y-auto max-h-[calc(100vh-48px)] divide-y sticky top-12">
          {/* Connectors */}
          <div className="p-3">
            <div className="flex items-center justify-between mb-3">
              <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Connectors</span>
              <span className="text-xs text-muted-foreground">{configuredCount} active</span>
            </div>
            <div className="space-y-1.5">
              {connectorsFull.map((c) => (
                <ConnectorCard key={c.name} connector={c} />
              ))}
            </div>
          </div>

          {/* Live Trace */}
          <div className="p-3">
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wider flex items-center gap-1.5">
                <Activity className="h-3 w-3" />
                Live Trace
              </span>
              <span className="text-xs text-muted-foreground tabular-nums">{traces.length}</span>
            </div>
            <div className="space-y-0 font-mono text-[10px]">
              {traces.length === 0 ? (
                <p className="text-muted-foreground italic py-2 text-center">No trace events yet.</p>
              ) : (
                traces.map((t) => (
                  <div key={t.id} className="flex items-start gap-2 py-0.5 hover:bg-accent px-1 rounded transition-colors group">
                    <span className="text-muted-foreground/40 group-hover:text-muted-foreground/70 shrink-0 pt-px">
                      {new Date(t.created_at).toLocaleTimeString([], { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" })}
                    </span>
                    <span className={cn(
                      "truncate",
                      t.status === "failed" ? "text-red-500" :
                      t.status === "complete" ? "text-emerald-600 dark:text-emerald-400" : "text-amber-600 dark:text-amber-400"
                    )}>
                      {t.name}
                    </span>
                  </div>
                ))
              )}
            </div>
          </div>
        </aside>
      </div>
    </div>
  );
}

// ── Mission Row ───────────────────────────────────────────────────────────────

function MissionRow({ mission, selected, onClick }: { mission: Mission; selected: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "w-full text-left px-3 py-2.5 rounded-lg border transition-colors",
        selected
          ? "bg-accent border-border shadow-sm"
          : "bg-transparent border-transparent hover:bg-accent/50"
      )}
    >
      <div className="flex items-start justify-between gap-2 mb-1">
        <span className="text-xs font-medium leading-snug line-clamp-2 flex-1">{mission.title}</span>
        <StatusBadge status={mission.status} />
      </div>
      <div className="flex items-center justify-between text-[10px] text-muted-foreground mt-1.5">
        <SeverityBadge severity={mission.severity} />
        <span className="tabular-nums">{formatDistanceToNow(new Date(mission.updated_at), { addSuffix: true })}</span>
      </div>
    </button>
  );
}

// ── Execution Graph ───────────────────────────────────────────────────────────

function ExecutionGraph({ mission }: { mission: Mission }) {
  const steps = [
    { name: "Signal Ingested", desc: `${mission.signals?.length || 1} signal(s) received`, status: "complete" as StepStatus },
    { name: "Signal Evaluator", desc: mission.summary || "Evaluating severity and impact...", status: mission.status !== "queued" ? "complete" as StepStatus : "started" as StepStatus },
    ...(mission.hypotheses?.length
      ? [{ name: `Mission Planner → ${mission.hypotheses.length} hypotheses`, desc: mission.hypotheses.map(h => h.title).join(" · "), status: "complete" as StepStatus }]
      : []),
    ...(mission.evidence?.length
      ? [{ name: `Parallel Investigator Sub-Agents`, desc: `${mission.evidence.length} evidence item(s) gathered across ${new Set(mission.evidence.map(e => e.source)).size} source(s)`, status: "complete" as StepStatus }]
      : []),
    ...(mission.status === "running"
      ? [{ name: "In progress...", desc: "Sub-agents running", status: "started" as StepStatus }]
      : []),
    ...(mission.status === "complete"
      ? [{ name: "Synthesis & Action Publisher", desc: `${mission.actions?.length || 0} bounded action(s) executed`, status: "complete" as StepStatus }]
      : []),
    ...(mission.status === "failed"
      ? [{ name: "Mission Failed", desc: mission.summary || "Runtime error", status: "failed" as StepStatus }]
      : []),
  ];

  return (
    <div className="space-y-1 relative pl-5 before:absolute before:left-[9px] before:top-2 before:bottom-2 before:w-px before:bg-border">
      {steps.map((step, i) => (
        <div key={i} className="relative pb-3 last:pb-0">
          <div className={cn(
            "absolute -left-5 top-[3px] h-3 w-3 rounded-full border-2 border-background",
            step.status === "complete" ? "bg-emerald-500" :
            step.status === "failed" ? "bg-red-500" : "bg-amber-400 animate-pulse"
          )} />
          <p className="text-xs font-medium">{step.name}</p>
          {step.desc && <p className="text-[11px] text-muted-foreground mt-0.5 leading-relaxed">{step.desc}</p>}
        </div>
      ))}
    </div>
  );
}

// ── Evidence Panel ────────────────────────────────────────────────────────────

function EvidencePanel({ mission }: { mission: Mission }) {
  if (!mission.evidence?.length) {
    return <p className="text-sm text-muted-foreground">No evidence gathered yet.</p>;
  }
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
      {mission.evidence.map((ev) => (
        <div key={ev.id} className="rounded-lg border bg-card p-3 space-y-1.5">
          <div className="flex items-center justify-between">
            <span className="text-[10px] font-mono text-muted-foreground uppercase">{ev.source}</span>
            <span className="text-[10px] font-semibold">{(ev.confidence * 100).toFixed(0)}%</span>
          </div>
          <p className="text-xs font-medium leading-snug">{ev.title}</p>
          <p className="text-[11px] text-muted-foreground leading-relaxed line-clamp-3">{ev.summary}</p>
          {ev.url && (
            <a href={ev.url} target="_blank" rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-[10px] text-blue-600 dark:text-blue-400 hover:underline">
              <ExternalLink className="h-2.5 w-2.5" />
              View source
            </a>
          )}
        </div>
      ))}
    </div>
  );
}

// ── Actions Panel ─────────────────────────────────────────────────────────────

function ActionsPanel({ mission }: { mission: Mission }) {
  if (!mission.actions?.length) {
    return <p className="text-sm text-muted-foreground">No actions executed yet.</p>;
  }
  return (
    <div className="space-y-3">
      {mission.actions.map((act) => (
        <div key={act.id} className={cn(
          "flex items-start gap-3 rounded-lg border p-3",
          act.status === "complete" ? "border-emerald-200 bg-emerald-50 dark:border-emerald-900 dark:bg-emerald-950/20" :
          act.status === "skipped" ? "border-amber-200 bg-amber-50 dark:border-amber-900 dark:bg-amber-950/20" :
          act.status === "blocked" ? "border-red-200 bg-red-50 dark:border-red-900 dark:bg-red-950/20" :
          "border-border"
        )}>
          {act.status === "complete" ? <CheckCircle2 className="h-4 w-4 text-emerald-500 shrink-0 mt-0.5" /> :
           act.status === "blocked" ? <XCircle className="h-4 w-4 text-red-500 shrink-0 mt-0.5" /> :
           <AlertCircle className="h-4 w-4 text-amber-500 shrink-0 mt-0.5" />}
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 mb-0.5">
              <span className="text-xs font-semibold">{act.action}</span>
              <span className="text-[10px] font-mono text-muted-foreground">{act.connector}</span>
              <span className={cn(
                "text-[9px] font-bold uppercase px-1.5 py-0.5 rounded-full border",
                act.status === "complete" ? "text-emerald-600 border-emerald-300 bg-emerald-50 dark:bg-emerald-950/30 dark:border-emerald-800 dark:text-emerald-400" :
                act.status === "skipped" ? "text-amber-600 border-amber-300 bg-amber-50 dark:bg-amber-950/30 dark:border-amber-800 dark:text-amber-400" :
                "text-red-600 border-red-300 bg-red-50 dark:bg-red-950/30 dark:border-red-800 dark:text-red-400"
              )}>
                {act.status}
              </span>
            </div>
            <p className="text-[11px] text-muted-foreground leading-relaxed">{act.summary}</p>
            {act.artifact_path && (
              <code className="mt-1 text-[10px] bg-black/5 dark:bg-white/5 px-1.5 py-0.5 rounded block truncate">
                {act.artifact_path}
              </code>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

// ── Connector Card ────────────────────────────────────────────────────────────

function ConnectorCard({ connector }: { connector: ConnectorFull }) {
  return (
    <div className={cn(
      "rounded-lg border px-3 py-2 transition-colors",
      connector.configured ? "border-border bg-card" : "border-border/50 bg-muted/30 opacity-60"
    )}>
      <div className="flex items-center justify-between mb-1">
        <span className="text-xs font-semibold">{connector.name}</span>
        <div className="flex items-center gap-1.5">
          {connector.tool_count > 0 && (
            <span className="text-[9px] text-muted-foreground flex items-center gap-0.5">
              <Zap className="h-2.5 w-2.5" />{connector.tool_count}
            </span>
          )}
          <span className={cn(
            "h-1.5 w-1.5 rounded-full",
            connector.configured ? "bg-emerald-500" : "bg-muted-foreground"
          )} />
        </div>
      </div>
      <p className="text-[10px] text-muted-foreground leading-tight line-clamp-2 mb-1.5">{connector.description}</p>
      <div className="flex flex-wrap gap-1">
        {connector.capabilities.map((cap) => (
          <span key={cap} className="text-[9px] font-mono bg-secondary text-secondary-foreground px-1 py-0.5 rounded uppercase">
            {cap}
          </span>
        ))}
      </div>
      {!connector.configured && connector.auth_required && (
        <p className="text-[9px] text-amber-600 dark:text-amber-400 mt-1.5 flex items-center gap-1">
          <AlertCircle className="h-2.5 w-2.5" />
          Credentials required
        </p>
      )}
    </div>
  );
}

// ── Shared badges ─────────────────────────────────────────────────────────────

function StatusBadge({ status }: { status: MissionStatus | StepStatus }) {
  const config: Record<string, { color: string; icon: React.ElementType }> = {
    complete:  { color: "text-emerald-600 bg-emerald-50 border-emerald-200 dark:bg-emerald-950/30 dark:border-emerald-800 dark:text-emerald-400", icon: CheckCircle2 },
    running:   { color: "text-amber-600 bg-amber-50 border-amber-200 dark:bg-amber-950/30 dark:border-amber-800 dark:text-amber-400", icon: Activity },
    queued:    { color: "text-blue-600 bg-blue-50 border-blue-200 dark:bg-blue-950/30 dark:border-blue-800 dark:text-blue-400", icon: Clock },
    failed:    { color: "text-red-600 bg-red-50 border-red-200 dark:bg-red-950/30 dark:border-red-800 dark:text-red-400", icon: XCircle },
    waiting:   { color: "text-slate-600 bg-slate-50 border-slate-200 dark:bg-slate-950/30 dark:border-slate-800 dark:text-slate-400", icon: Clock },
    started:   { color: "text-amber-600 bg-amber-50 border-amber-200 dark:bg-amber-950/30 dark:border-amber-800 dark:text-amber-400", icon: Activity },
    canceled:  { color: "text-slate-600 bg-slate-50 border-slate-200 dark:bg-slate-950/30 dark:border-slate-800 dark:text-slate-400", icon: XCircle },
  };
  const { color, icon: Icon } = config[status] || config.queued;
  return (
    <span className={cn("inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[9px] font-bold uppercase shrink-0", color)}>
      <Icon className="h-2.5 w-2.5" />
      {status}
    </span>
  );
}

function SeverityBadge({ severity }: { severity: string }) {
  const isHigh = ["high", "critical", "p0", "p1"].includes(severity?.toLowerCase());
  return (
    <span className={cn(
      "inline-flex items-center rounded-md border px-1.5 py-0.5 text-[9px] font-bold uppercase",
      isHigh
        ? "text-red-600 bg-red-50 border-red-200 dark:bg-red-950/30 dark:border-red-800 dark:text-red-400"
        : "text-slate-600 bg-slate-50 border-slate-200 dark:bg-slate-900 dark:border-slate-700 dark:text-slate-400"
    )}>
      {severity}
    </span>
  );
}
