"use client";

import { useMemo, useState } from "react";
import type { ElementType } from "react";
import {
  Activity,
  ArrowRight,
  Bot,
  CheckCircle2,
  Command,
  Database,
  FileText,
  Filter,
  GitBranch,
  Layers,
  LayoutDashboard,
  Lock,
  Play,
  Puzzle,
  ShieldCheck,
  Terminal,
  Wrench,
  X,
  Zap,
} from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { formatDistanceToNow } from "date-fns";
import { BackendConnection, ManualSignal, useAutopilot } from "@/hooks/useAutopilot";
import { cn } from "@/lib/utils";
import {
  ActionApproval,
  AgentRun,
  Connector,
  ConnectorDirectoryItem,
  Mission,
} from "@/types";

type View = "dashboard" | "connectors" | "approvals" | "traces";

export default function Dashboard() {
  const {
    missions,
    traces,
    connectors,
    connectorDirectory,
    approvals,
    provider,
    connection,
    runDemo,
    sendSignal,
    connectConnector,
    disconnectConnector,
    approveAction,
    rejectAction,
  } = useAutopilot();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [currentView, setCurrentView] = useState<View>("dashboard");
  const [isDemoRunning, setIsDemoRunning] = useState(false);
  const [isSignalModalOpen, setIsSignalModalOpen] = useState(false);

  const selectedMission = useMemo(
    () => missions.find((m) => m.id === selectedId) || missions[0],
    [missions, selectedId],
  );
  const readinessByName = useMemo(() => {
    const entries = connectors.map((connector) => [connector.name.toLowerCase(), connector] as const);
    return new Map(entries);
  }, [connectors]);

  const runningCount = missions.filter((m) => m.status === "running").length;
  const backendOffline = connection.status === "offline";

  const handleRunDemo = async () => {
    setIsDemoRunning(true);
    await runDemo();
    // Simulate a brief delay to show interaction feedback
    setTimeout(() => setIsDemoRunning(false), 2000);
  };

  return (
    <div className="flex h-screen bg-background text-[13px] font-medium leading-none text-foreground selection:bg-primary/10">
      {/* Sidebar Navigation */}
      <aside className="flex w-64 flex-col border-r border-border/50 bg-card/30 backdrop-blur-xl">
        <div className="flex h-14 items-center gap-2.5 border-b border-border/50 px-5">
          <div className="flex h-6 w-6 items-center justify-center rounded-md bg-foreground text-background">
            <Command className="h-3.5 w-3.5" />
          </div>
          <span className="font-bold tracking-tight text-[14px]">AUTOPILOT</span>
        </div>

        <div className="px-3 pt-3">
          <ConnectionCard connection={connection} />
        </div>

        <nav className="flex-1 space-y-1 p-3">
          <NavItem
            icon={LayoutDashboard}
            label="Missions"
            active={currentView === "dashboard"}
            onClick={() => setCurrentView("dashboard")}
            count={missions.length}
          />
          <NavItem
            icon={Puzzle}
            label="Connectors"
            active={currentView === "connectors"}
            onClick={() => setCurrentView("connectors")}
            count={connectors.length}
          />
          <NavItem
            icon={ShieldCheck}
            label="Approvals"
            active={currentView === "approvals"}
            onClick={() => setCurrentView("approvals")}
            count={approvals.length}
          />
          <NavItem
            icon={Activity}
            label="System Trace"
            active={currentView === "traces"}
            onClick={() => setCurrentView("traces")}
            count={traces.length}
          />
        </nav>

        <div className="border-t border-border/50 p-4">
          <div className="flex items-center gap-3 rounded-lg bg-secondary/50 p-3">
            <div className="flex h-8 w-8 items-center justify-center rounded-full bg-background border border-border/50 text-muted-foreground">
              <Bot className="h-4 w-4" />
            </div>
            <div className="min-w-0">
              <p className="text-[11px] text-muted-foreground">Runtime Provider</p>
              <p className="truncate text-[12px] font-semibold">{provider === "heuristic" ? "Heuristic fallback" : provider}</p>
            </div>
          </div>
        </div>
      </aside>

      {/* Main Content Area */}
      <main className="flex flex-1 flex-col overflow-hidden bg-background/50">
        <header className="flex h-14 items-center justify-between border-b border-border/50 px-6 backdrop-blur-md">
          <div className="flex items-center gap-4">
            <h2 className="text-[14px] font-bold capitalize">{currentView}</h2>
            {runningCount > 0 && (
              <span className="flex items-center gap-1.5 rounded-full bg-amber-500/10 px-2 py-0.5 text-[10px] font-bold text-amber-500 ring-1 ring-amber-500/20">
                <span className="h-1 w-1 animate-pulse rounded-full bg-amber-500" />
                {runningCount} ACTIVE
              </span>
            )}
          </div>

          <div className="flex items-center gap-3">
            <button
              onClick={() => setIsSignalModalOpen(true)}
              disabled={backendOffline}
              className="flex items-center gap-2 rounded-md border border-border/50 px-3 py-1.5 text-[12px] font-bold hover:bg-white/[0.05] transition-colors"
            >
              <Zap className="h-3.5 w-3.5 text-primary" />
              Manual Signal
            </button>
            <button
              onClick={handleRunDemo}
              disabled={isDemoRunning || backendOffline}
              className={cn(
                "group relative flex items-center gap-2 overflow-hidden rounded-md bg-foreground px-4 py-1.5 text-[12px] font-bold text-background transition-all hover:opacity-90 active:scale-95 disabled:opacity-50",
                isDemoRunning && "cursor-wait"
              )}
            >
              <AnimatePresence mode="wait">
                {isDemoRunning ? (
                  <motion.div
                    key="loading"
                    initial={{ y: 20 }}
                    animate={{ y: 0 }}
                    exit={{ y: -20 }}
                    className="flex items-center gap-2"
                  >
                    <Activity className="h-3.5 w-3.5 animate-spin" />
                    Simulating...
                  </motion.div>
                ) : (
                  <motion.div
                    key="idle"
                    initial={{ y: 20 }}
                    animate={{ y: 0 }}
                    exit={{ y: -20 }}
                    className="flex items-center gap-2"
                  >
                    <Play className="h-3.5 w-3.5 fill-current" />
                    Run Simulation
                  </motion.div>
                )}
              </AnimatePresence>
            </button>
          </div>
        </header>

        <div className="flex-1 overflow-y-auto">
          <AnimatePresence mode="wait">
            {currentView === "dashboard" && (
              <motion.div
                key="dashboard"
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -10 }}
                className="flex h-full divide-x divide-border/30"
              >
                {/* Mission List */}
                <div className="w-80 flex-shrink-0 flex flex-col">
                  <div className="p-4 flex items-center justify-between">
                    <span className="text-[11px] font-bold uppercase tracking-wider text-muted-foreground/60">Recent Missions</span>
                    <Filter className="h-3.5 w-3.5 text-muted-foreground/40 cursor-pointer hover:text-foreground transition-colors" />
                  </div>
                  <div className="flex-1 overflow-y-auto px-2 space-y-1">
                    {missions.map((m) => (
                      <MissionCard
                        key={m.id}
                        mission={m}
                        active={selectedMission?.id === m.id}
                        onClick={() => setSelectedId(m.id)}
                      />
                    ))}
                  </div>
                </div>

                {/* Mission Details */}
                <div className="flex-1 overflow-y-auto p-8">
                  {selectedMission ? (
                    <MissionDetail mission={selectedMission} />
                  ) : (
                    <div className="flex h-full flex-col items-center justify-center text-muted-foreground">
                      <Layers className="mb-4 h-12 w-12 opacity-10" />
                      <p className="text-[14px]">Select a mission to view details</p>
                    </div>
                  )}
                </div>
              </motion.div>
            )}

            {currentView === "connectors" && (
              <motion.div
                key="connectors"
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -10 }}
                className="p-8 max-w-6xl mx-auto"
              >
                <div className="mb-8">
                  <h1 className="text-2xl font-bold tracking-tight">Connector Hub</h1>
                  <p className="text-muted-foreground mt-2 text-[14px]">
                    Manage your service connections and capability permissions.
                  </p>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                  {connectorDirectory.map((c) => (
                    <ConnectorCard
                      key={c.id}
                      connector={c}
                      runtimeConnector={readinessByName.get(c.name.toLowerCase()) ?? readinessByName.get(c.id)}
                      onConnect={connectConnector}
                      onDisconnect={disconnectConnector}
                    />
                  ))}
                </div>
              </motion.div>
            )}

            {currentView === "approvals" && (
              <motion.div
                key="approvals"
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -10 }}
                className="p-8 max-w-5xl mx-auto"
              >
                <div className="mb-8">
                  <h1 className="text-2xl font-bold tracking-tight">Action Approvals</h1>
                  <p className="text-muted-foreground mt-2 text-[14px]">
                    Review policy-gated side effects before AUTOPILOT touches external systems.
                  </p>
                </div>
                <div className="space-y-3">
                  {approvals.length === 0 ? (
                    <div className="rounded-lg border border-border/40 bg-white/[0.01] p-8 text-center text-[13px] text-muted-foreground">
                      No pending approvals.
                    </div>
                  ) : (
                    approvals.map((approval) => (
                      <ApprovalCard
                        key={approval.id}
                        approval={approval}
                        onApprove={approveAction}
                        onReject={rejectAction}
                      />
                    ))
                  )}
                </div>
              </motion.div>
            )}

            {currentView === "traces" && (
              <motion.div
                key="traces"
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -10 }}
                className="p-8 max-w-4xl mx-auto"
              >
                <div className="mb-6 flex items-center justify-between">
                  <h1 className="text-xl font-bold tracking-tight">System Event Log</h1>
                  <span className="text-xs text-muted-foreground tabular-nums">{traces.length} events logged</span>
                </div>
                <div className="space-y-1 rounded-lg border border-border/50 bg-card/20 p-2 font-mono text-[12px]">
                  {traces.map((t) => (
                    <div key={t.id} className="group flex items-center gap-4 rounded-md px-3 py-2 hover:bg-white/[0.03] transition-colors">
                      <span className="w-20 flex-shrink-0 text-muted-foreground/40">
                        {new Date(t.created_at).toLocaleTimeString([], { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                      </span>
                      <span className={cn(
                        "w-24 font-bold text-[11px] uppercase tracking-wider px-1.5 py-0.5 rounded border border-current/20 bg-current/5",
                        t.status === "failed" ? "text-red-500" : t.status === "complete" ? "text-emerald-500" : "text-amber-500"
                      )}>
                        {t.status}
                      </span>
                      <span className="flex-1 truncate text-muted-foreground group-hover:text-foreground transition-colors">{t.name}</span>
                    </div>
                  ))}
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </main>

      <SignalModal
        isOpen={isSignalModalOpen}
        onClose={() => setIsSignalModalOpen(false)}
        onSubmit={async (signal) => {
          try {
            await sendSignal(signal);
            setIsSignalModalOpen(false);
          } catch (error) {
            console.error("Failed to send signal:", error);
          }
        }}
      />
    </div>
  );
}

function ApprovalCard({
  approval,
  onApprove,
  onReject,
}: {
  approval: ActionApproval;
  onApprove: (approvalId: string) => Promise<void>;
  onReject: (approvalId: string) => Promise<void>;
}) {
  const [busy, setBusy] = useState<"approve" | "reject" | null>(null);

  const submit = async (kind: "approve" | "reject") => {
    setBusy(kind);
    try {
      if (kind === "approve") {
        await onApprove(approval.id);
      } else {
        await onReject(approval.id);
      }
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="rounded-lg border border-border/40 bg-white/[0.01] p-5">
      <div className="mb-4 flex items-start justify-between gap-4">
        <div className="min-w-0 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded border border-amber-500/20 bg-amber-500/5 px-2 py-0.5 text-[10px] font-black uppercase tracking-wider text-amber-500">
              {approval.risk}
            </span>
            <span className="font-mono text-[11px] text-muted-foreground">{approval.connector}.{approval.action}</span>
          </div>
          <h3 className="truncate text-[15px] font-bold">{approval.mission_title || approval.mission_id}</h3>
          <p className="text-[12px] leading-relaxed text-muted-foreground">{approval.reason}</p>
        </div>
        <span className="rounded border border-border/50 px-2 py-1 text-[10px] font-black uppercase text-muted-foreground">
          {approval.status}
        </span>
      </div>

      <div className="mb-4 rounded-md border border-white/5 bg-black/20 p-3 font-mono text-[11px] leading-relaxed text-muted-foreground">
        {String(approval.payload.title || approval.payload.summary || approval.id)}
      </div>

      <div className="flex items-center justify-end gap-2">
        <button
          onClick={() => submit("reject")}
          disabled={busy !== null}
          className="flex items-center gap-2 rounded-md border border-border/50 px-3 py-1.5 text-[12px] font-bold text-muted-foreground hover:bg-white/[0.04] disabled:opacity-50"
        >
          <X className="h-3.5 w-3.5" />
          {busy === "reject" ? "Rejecting..." : "Reject"}
        </button>
        <button
          onClick={() => submit("approve")}
          disabled={busy !== null}
          className="flex items-center gap-2 rounded-md bg-foreground px-3 py-1.5 text-[12px] font-bold text-background hover:opacity-90 disabled:opacity-50"
        >
          <CheckCircle2 className="h-3.5 w-3.5" />
          {busy === "approve" ? "Approving..." : "Approve"}
        </button>
      </div>
    </div>
  );
}

function SignalModal({ isOpen, onClose, onSubmit }: { isOpen: boolean; onClose: () => void; onSubmit: (s: ManualSignal) => Promise<void> }) {
  const [type, setType] = useState("support_escalation");
  const [summary, setSummary] = useState("");
  const [entities, setEntities] = useState("");
  const [urgency, setUrgency] = useState("high");

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-background/80 backdrop-blur-sm p-4 text-sm font-medium leading-none">
      <motion.div
        initial={{ opacity: 0, scale: 0.95 }}
        animate={{ opacity: 1, scale: 1 }}
        className="w-full max-w-md rounded-2xl border border-border bg-card p-6 shadow-2xl"
      >
        <div className="mb-6 flex items-center justify-between">
          <h3 className="text-lg font-bold">New Manual Signal</h3>
          <button onClick={onClose} className="rounded-full p-1 hover:bg-white/10">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="space-y-4">
          <div className="space-y-1.5">
            <label className="text-[11px] font-bold uppercase tracking-wider text-muted-foreground">Signal Type</label>
            <select
              value={type}
              onChange={(e) => setType(e.target.value)}
              className="w-full rounded-lg border border-border bg-background px-3 py-2 outline-none focus:ring-1 focus:ring-primary appearance-none"
            >
              <option value="support_escalation">Support Escalation</option>
              <option value="monitoring_error">Monitoring Error</option>
              <option value="security_alert">Security Alert</option>
              <option value="code_review">Code Review Request</option>
              <option value="custom">Custom Signal</option>
            </select>
          </div>

          <div className="space-y-1.5">
            <label className="text-[11px] font-bold uppercase tracking-wider text-muted-foreground">Urgency</label>
            <div className="flex gap-2">
              {["low", "medium", "high", "critical"].map((u) => (
                <button
                  key={u}
                  onClick={() => setUrgency(u)}
                  className={cn(
                    "flex-1 rounded-md border py-1.5 text-[11px] font-bold uppercase transition-all",
                    urgency === u ? "border-primary bg-primary/10 text-primary" : "border-border hover:bg-white/5"
                  )}
                >
                  {u}
                </button>
              ))}
            </div>
          </div>

          <div className="space-y-1.5">
            <label className="text-[11px] font-bold uppercase tracking-wider text-muted-foreground">Summary</label>
            <textarea
              value={summary}
              onChange={(e) => setSummary(e.target.value)}
              placeholder="Describe the event..."
              className="h-24 w-full resize-none rounded-lg border border-border bg-background px-3 py-2 text-[13px] outline-none focus:ring-1 focus:ring-primary leading-relaxed"
            />
          </div>

          <div className="space-y-1.5">
            <label className="text-[11px] font-bold uppercase tracking-wider text-muted-foreground">Entities</label>
            <input
              value={entities}
              onChange={(e) => setEntities(e.target.value)}
              placeholder="export service, checkout, rollout"
              className="w-full rounded-lg border border-border bg-background px-3 py-2 text-[13px] outline-none focus:ring-1 focus:ring-primary"
            />
          </div>

          <button
            onClick={() => onSubmit({
              type,
              summary,
              urgency,
              source: "manual_ui",
              entities: entities
                .split(",")
                .map((entity) => entity.trim())
                .filter(Boolean),
              payload: {},
            })}
            disabled={!summary}
            className="flex w-full items-center justify-center gap-2 rounded-lg bg-foreground py-2.5 text-[13px] font-bold text-background transition-all hover:opacity-90 disabled:opacity-50"
          >
            <Zap className="h-4 w-4" />
            Ingest Signal
          </button>
        </div>
      </motion.div>
    </div>
  );
}

function ConnectionCard({ connection }: { connection: BackendConnection }) {
  const isConnected = connection.status === "connected";
  const isOffline = connection.status === "offline";
  const isChecking = connection.status === "checking";

  return (
    <div
      className={cn(
        "rounded-lg border p-2.5",
        isConnected
          ? "border-emerald-500/20 bg-emerald-500/5"
          : isOffline
            ? "border-red-500/25 bg-red-500/5"
            : "border-amber-500/25 bg-amber-500/5",
      )}
    >
      <div
        className={cn(
          "mb-1 flex items-center gap-2",
          isConnected ? "text-emerald-500" : isOffline ? "text-red-500" : "text-amber-500",
        )}
      >
        <span className={cn("h-1.5 w-1.5 rounded-full", isChecking && "animate-pulse", isConnected ? "bg-emerald-500" : isOffline ? "bg-red-500" : "bg-amber-500")} />
        <span className="text-[11px] font-black uppercase tracking-wider">
          Backend {connection.status}
        </span>
      </div>
      <p className="text-[10px] font-medium leading-relaxed text-muted-foreground/80">{connection.message}</p>
      <div className="mt-2 space-y-1 border-t border-current/10 pt-2 text-[9px] font-bold uppercase tracking-wider text-muted-foreground/50">
        <div className="flex justify-between gap-2">
          <span>API</span>
          <span className="truncate normal-case">{connection.apiBase}</span>
        </div>
        <div className="flex justify-between gap-2">
          <span>Events</span>
          <span>{connection.sseConnected ? "streaming" : "disconnected"}</span>
        </div>
      </div>
    </div>
  );
}

function NavItem({ icon: Icon, label, active, onClick, count }: { icon: ElementType; label: string; active: boolean; onClick: () => void; count?: number }) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "flex w-full items-center gap-3 rounded-md px-3 py-2 text-left transition-all duration-200",
        active ? "bg-primary text-primary-foreground shadow-sm shadow-primary/20" : "text-muted-foreground hover:bg-white/[0.05] hover:text-foreground"
      )}
    >
      <Icon className="h-4 w-4" />
      <span className="flex-1 font-semibold">{label}</span>
      {count !== undefined && (
        <span className={cn("text-[10px] font-bold tabular-nums", active ? "text-primary-foreground/70" : "text-muted-foreground/40")}>
          {count}
        </span>
      )}
    </button>
  );
}

function MissionCard({ mission, active, onClick }: { mission: Mission; active: boolean; onClick: () => void }) {
  const isHigh = ["high", "critical", "p0"].includes(mission.severity.toLowerCase());
  
  return (
    <button
      onClick={onClick}
      className={cn(
        "group w-full rounded-lg border p-3.5 text-left transition-all duration-200",
        active 
          ? "border-primary/50 bg-primary/5 ring-1 ring-primary/20" 
          : "border-transparent hover:border-border/50 hover:bg-white/[0.02]"
      )}
    >
      <div className="mb-2 flex items-start justify-between gap-2">
        <span className={cn(
          "line-clamp-2 text-[13px] font-bold leading-tight",
          active ? "text-foreground" : "text-muted-foreground group-hover:text-foreground"
        )}>
          {mission.title}
        </span>
        <div className={cn(
          "h-1.5 w-1.5 rounded-full flex-shrink-0 mt-1",
          mission.status === "complete" ? "bg-emerald-500" : mission.status === "failed" ? "bg-red-500" : "bg-amber-500"
        )} />
      </div>
      <div className="flex items-center gap-3 text-[11px] text-muted-foreground/60">
        <span className={cn(
          "font-bold uppercase tracking-wider px-1.5 py-0.5 rounded border border-current/20 bg-current/5",
          isHigh ? "text-red-500" : "text-muted-foreground"
        )}>
          {mission.severity}
        </span>
        <span className="flex-1 tabular-nums italic">
          {formatDistanceToNow(new Date(mission.updated_at), { addSuffix: true })}
        </span>
      </div>
    </button>
  );
}

function MissionDetail({ mission }: { mission: Mission }) {
  return (
    <div className="max-w-4xl mx-auto space-y-12">
      {/* Detail Header */}
      <div className="space-y-6">
        <div className="flex items-start justify-between gap-6">
          <div className="space-y-2">
            <div className="flex items-center gap-3 text-muted-foreground/60 text-[12px] font-bold uppercase tracking-widest">
              <GitBranch className="h-3.5 w-3.5" />
              Mission Trace
              <span className="h-1 w-1 rounded-full bg-muted-foreground/20" />
              ID: {mission.id.replace(/^mission_/, "")}
            </div>
            <h1 className="text-3xl font-extrabold tracking-tight leading-tight">{mission.title}</h1>
          </div>
          <div className={cn(
            "rounded-lg border px-4 py-2 text-center",
            mission.status === "complete" ? "border-emerald-500/20 bg-emerald-500/5 text-emerald-500" : "border-amber-500/20 bg-amber-500/5 text-amber-500"
          )}>
            <div className="text-[10px] font-black uppercase tracking-tighter opacity-60">Status</div>
            <div className="text-[14px] font-black uppercase">{mission.status}</div>
          </div>
        </div>
        
        <p className="text-[16px] leading-relaxed text-muted-foreground/80 font-medium">
          {mission.summary || "No summary provided for this mission."}
        </p>

        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <DetailStat icon={ShieldCheck} label="Confidence" value={`${Math.round(mission.confidence * 100)}%`} />
          <DetailStat icon={Bot} label="Subagents" value={mission.agent_runs?.length.toString() || "0"} />
          <DetailStat icon={Wrench} label="Tool Calls" value={mission.agent_runs?.reduce((sum, r) => sum + r.tool_calls, 0).toString() || "0"} />
          <DetailStat icon={Layers} label="Graph Nodes" value={mission.graph?.length.toString() || "0"} />
        </div>
      </div>

      {/* Swarm / Subagents */}
      <section className="space-y-6">
        <div className="flex items-center gap-3 border-b border-border/30 pb-4">
          <Bot className="h-5 w-5 text-muted-foreground/40" />
          <h3 className="text-lg font-bold tracking-tight">Agent Swarm</h3>
          <span className="text-xs text-muted-foreground font-bold opacity-40">{mission.agent_runs?.length} OPERATORS</span>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {mission.agent_runs?.map((run) => (
            <AgentCard key={run.id} run={run} />
          ))}
        </div>
      </section>

      {/* Graph Flow */}
      <section className="space-y-6">
        <div className="flex items-center gap-3 border-b border-border/30 pb-4">
          <GitBranch className="h-5 w-5 text-muted-foreground/40" />
          <h3 className="text-lg font-bold tracking-tight">Execution Graph</h3>
          <span className="text-xs text-muted-foreground font-bold opacity-40">{mission.graph?.length} NODES</span>
        </div>
        <div className="relative space-y-4 pl-6 before:absolute before:left-[11px] before:top-2 before:bottom-2 before:w-px before:bg-gradient-to-b before:from-primary/20 before:via-border before:to-transparent">
          {mission.graph?.map((node) => (
            <div key={node.id} className="relative group">
              <div className={cn(
                "absolute -left-[21px] top-3 h-3 w-3 rounded-full border-2 border-background z-10",
                node.status === "complete" ? "bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.4)]" : "bg-amber-400"
              )} />
              <div className="rounded-xl border border-border/40 bg-white/[0.01] p-4 transition-all group-hover:border-border/80 group-hover:bg-white/[0.03]">
                <div className="flex items-center gap-3 mb-2">
                  <span className="text-[10px] font-black uppercase tracking-widest text-muted-foreground/40">{node.kind}</span>
                  <h4 className="text-[14px] font-bold">{node.title}</h4>
                </div>
                <p className="text-[13px] leading-relaxed text-muted-foreground/60">{node.summary}</p>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* Final Action / Outcome */}
      {mission.actions?.length > 0 && (
        <section className="space-y-6 rounded-2xl border border-primary/20 bg-primary/[0.02] p-8 ring-1 ring-primary/10">
          <div className="flex items-center gap-3">
            <Zap className="h-5 w-5 text-primary" />
            <h3 className="text-lg font-extrabold tracking-tight text-primary">Final Outcomes</h3>
          </div>
          <div className="space-y-4">
            {mission.actions.map((action) => (
              <div key={action.id} className="flex gap-4">
                <div className="flex-shrink-0 flex h-6 w-6 items-center justify-center rounded bg-primary/10 text-primary">
                  <CheckCircle2 className="h-4 w-4" />
                </div>
                <div className="space-y-1">
                  <p className="text-[14px] font-bold">{action.action}</p>
                  <p className="text-[13px] text-muted-foreground/80 leading-relaxed">{action.summary}</p>
                  {action.artifact_path && (
                    <div className="mt-3 inline-flex items-center gap-2 rounded bg-black/40 px-3 py-1.5 font-mono text-[11px] text-muted-foreground/80 border border-white/5">
                      <FileText className="h-3 w-3" />
                      {action.artifact_path}
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

function DetailStat({ icon: Icon, label, value }: { icon: ElementType; label: string; value: string }) {
  return (
    <div className="rounded-xl border border-border/40 bg-white/[0.01] p-4">
      <div className="flex items-center gap-2 text-muted-foreground/40 mb-1">
        <Icon className="h-3.5 w-3.5" />
        <span className="text-[10px] font-black uppercase tracking-widest">{label}</span>
      </div>
      <div className="text-[20px] font-black tracking-tight">{value}</div>
    </div>
  );
}

function AgentCard({ run }: { run: AgentRun }) {
  return (
    <div className="rounded-xl border border-border/40 bg-white/[0.01] p-4 space-y-4">
      <div className="flex items-start justify-between">
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <Terminal className="h-3.5 w-3.5 text-muted-foreground/40" />
            <h4 className="text-[13px] font-bold">{run.objective || run.role}</h4>
          </div>
          <p className="text-[10px] font-mono text-muted-foreground opacity-40">{run.id}</p>
        </div>
        <span className={cn(
          "text-[9px] font-black uppercase tracking-tighter px-1.5 py-0.5 rounded border",
          run.status === "complete" ? "border-emerald-500/20 text-emerald-500" : "border-amber-500/20 text-amber-500"
        )}>
          {run.status}
        </span>
      </div>
      
      <p className="text-[12px] leading-relaxed text-muted-foreground/60">{run.output_summary}</p>

      <div className="flex items-center gap-4 text-[11px]">
        <span className="flex items-center gap-1.5 text-muted-foreground/40 font-bold">
          <Wrench className="h-3 w-3" /> {run.tool_calls} CALLS
        </span>
        <span className="flex items-center gap-1.5 text-muted-foreground/40 font-bold">
          <ShieldCheck className="h-3 w-3" /> {Math.round(run.confidence * 100)}% CONF
        </span>
      </div>

      <div className="flex flex-wrap gap-1.5">
        {run.tools.map((t, i) => (
          <span key={i} className="rounded-md border border-white/5 bg-white/[0.02] px-2 py-1 font-mono text-[9px] text-muted-foreground/80 uppercase">
            {t}
          </span>
        ))}
      </div>
    </div>
  );
}

function ConnectorCard({
  connector,
  runtimeConnector,
  onConnect,
  onDisconnect,
}: {
  connector: ConnectorDirectoryItem;
  runtimeConnector?: Connector;
  onConnect: (connectorId: string) => Promise<void>;
  onDisconnect: (connectorId: string) => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const connected = connector.status === "connected";
  const canDemoConnect = connector.demo_available || connector.auth_mode === "none";
  const readiness = runtimeConnector?.readiness;

  const handleToggle = async () => {
    if (busy) return;
    setBusy(true);
    try {
      if (connected) {
        await onDisconnect(connector.id);
      } else if (canDemoConnect) {
        await onConnect(connector.id);
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={cn(
      "group relative flex flex-col rounded-2xl border border-border/40 bg-white/[0.01] p-5 transition-all hover:border-primary/40 hover:bg-white/[0.03] overflow-hidden",
      !connector.implemented && "opacity-75"
    )}>
      <div className="mb-6 flex items-start justify-between">
        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-white/[0.03] border border-white/5 group-hover:scale-110 transition-transform">
          <Database className="h-5 w-5 text-muted-foreground group-hover:text-primary transition-colors" />
        </div>
        <div className={cn(
          "flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[10px] font-black uppercase tracking-tighter ring-1 ring-inset",
          connected
            ? "bg-emerald-500/5 text-emerald-500 ring-emerald-500/20" 
            : connector.implemented
              ? "bg-blue-500/5 text-blue-500 ring-blue-500/20"
              : "bg-white/5 text-muted-foreground ring-white/10"
        )}>
          {connected ? "CONNECTED" : connector.implemented ? "ADAPTER" : "CATALOG"}
        </div>
      </div>

      <div className="space-y-1 mb-4 flex-1">
        <h3 className="text-[15px] font-bold group-hover:text-primary transition-colors">{connector.name}</h3>
        <p className="text-[12px] text-muted-foreground leading-relaxed line-clamp-2">{connector.description}</p>
      </div>

      <div className="space-y-4">
        {readiness && (
          <div className="rounded-md border border-white/5 bg-black/20 p-2 text-[11px] leading-relaxed text-muted-foreground">
            <div className="flex items-center justify-between gap-2">
              <span>{readiness.detail}</span>
              <span className={cn(
                "rounded border px-1.5 py-0.5 text-[9px] font-black uppercase",
                readiness.configured ? "border-emerald-500/20 text-emerald-500" : "border-amber-500/20 text-amber-500"
              )}>
                {readiness.mode}
              </span>
            </div>
            {readiness.missing.length > 0 && (
              <div className="mt-1 font-mono text-[10px] text-amber-500/80">
                Missing: {readiness.missing.join(", ")}
              </div>
            )}
          </div>
        )}

        <div className="flex flex-wrap gap-1">
          {connector.capabilities.map(cap => (
            <span key={cap} className="rounded-md border border-white/5 bg-white/[0.02] px-2 py-1 text-[9px] font-black uppercase text-muted-foreground/40 tracking-wider">
              {cap}
            </span>
          ))}
        </div>
        
        <button
          onClick={handleToggle}
          disabled={busy || (!connected && !canDemoConnect)}
          className="flex w-full items-center justify-between rounded-lg bg-secondary/50 px-4 py-2.5 text-[12px] font-bold transition-all hover:bg-secondary disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy
            ? "Updating..."
            : connected
              ? "Disconnect Demo"
              : canDemoConnect
                ? "Connect Demo"
                : "OAuth Required"}
          <ArrowRight className="h-3.5 w-3.5 opacity-40 group-hover:translate-x-0.5 transition-transform" />
        </button>
      </div>

      {!connector.implemented && !canDemoConnect && (
        <div className="absolute inset-0 z-10 flex flex-col items-center justify-center bg-background/60 opacity-0 group-hover:opacity-100 backdrop-blur-[2px] transition-opacity">
          <Lock className="h-6 w-6 text-muted-foreground/40 mb-2" />
          <p className="text-[11px] font-bold text-muted-foreground uppercase tracking-widest">OAuth adapter pending</p>
        </div>
      )}
    </div>
  );
}
