"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import type { ElementType } from "react";
import {
  Activity,
  ArrowRight,
  BarChart3,
  Bot,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Command,
  Database,
  FileText,
  Filter,
  GitBranch,
  Layers,
  LayoutDashboard,
  Lock,
  Puzzle,
  ShieldCheck,
  Terminal,
  Wrench,
  X,
  Zap,
} from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { formatDistanceToNow } from "date-fns";
import axios from "axios";
import { BackendConnection, ManualSignal, useAutopilot } from "@/hooks/useAutopilot";
import { cn } from "@/lib/utils";
import {
  ActionApproval,
  AgentRun,
  Connector,
  ConnectorDirectoryItem,
  Mission,
  MissionGraphNode,
  ProviderHealth,
  TracingStatus,
} from "@/types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";
const API_KEY = process.env.NEXT_PUBLIC_AUTOPILOT_API_KEY || "";
const READ_CONFIG = API_KEY ? { headers: { "x-autopilot-key": API_KEY } } : undefined;

/** Catalog ids vs manifest names returned by GET /api/connectors */
const CATALOG_RUNTIME_ALIASES: Record<string, string> = {
  web_search: "knowledge",
  local_artifacts: "artifact",
  slack: "notification",
};

type View = "dashboard" | "connectors" | "approvals" | "traces" | "analytics";

function BackgroundGrid() {
  return (
    <div className="fixed inset-0 -z-10 overflow-hidden pointer-events-none">
      <div className="absolute inset-0 bg-[#030303]" />
      <div 
        className="absolute inset-0 opacity-[0.15]"
        style={{
          backgroundImage: `radial-gradient(circle at 2px 2px, rgba(255,255,255,0.15) 1px, transparent 0)`,
          backgroundSize: '32px 32px'
        }}
      />
      <motion.div 
        animate={{ 
          scale: [1, 1.2, 1],
          opacity: [0.3, 0.5, 0.3],
        }}
        transition={{ duration: 10, repeat: Infinity, ease: "linear" }}
        className="absolute -top-[20%] -left-[10%] w-[70%] h-[70%] rounded-full bg-blue-500/10 blur-[120px]" 
      />
      <motion.div 
        animate={{ 
          scale: [1.2, 1, 1.2],
          opacity: [0.2, 0.4, 0.2],
        }}
        transition={{ duration: 15, repeat: Infinity, ease: "linear" }}
        className="absolute -bottom-[20%] -right-[10%] w-[60%] h-[60%] rounded-full bg-purple-500/10 blur-[120px]" 
      />
    </div>
  );
}

export default function Dashboard() {
  const {
    missions,
    traces,
    connectors,
    connectorDirectory,
    providerHealth,
    tracingStatus,
    approvals,
    provider,
    connection,
    monitorConnectedServices,
    sendSignal,
    connectConnector,
    disconnectConnector,
    approveAction,
    rejectAction,
    refresh,
    totals,
  } = useAutopilot();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [currentView, setCurrentView] = useState<View>("dashboard");
  const [isMonitoring, setIsMonitoring] = useState(false);
  const [isSignalModalOpen, setIsSignalModalOpen] = useState(false);

  const selectedMission = useMemo(
    () => missions.find((m) => m.id === selectedId) || missions[0],
    [missions, selectedId],
  );
  const runtimeConnectorByKey = useMemo(() => {
    const m = new Map<string, Connector>();
    for (const c of connectors) {
      m.set(c.name.toLowerCase(), c);
    }
    for (const [catalogId, runtimeName] of Object.entries(CATALOG_RUNTIME_ALIASES)) {
      const rc = m.get(runtimeName.toLowerCase());
      if (rc) {
        m.set(catalogId.toLowerCase(), rc);
      }
    }
    return m;
  }, [connectors]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    const oauthOk = params.get("oauth_success");
    const oauthErr = params.get("oauth_error") ?? params.get("error");
    if (!oauthOk && !oauthErr) return;
    void refresh().finally(() => {
      window.history.replaceState({}, "", `${window.location.pathname}${window.location.hash}`);
    });
  }, [refresh]);

  useEffect(() => {
    if (currentView !== "connectors") return;
    void refresh();
  }, [currentView, refresh]);

  useEffect(() => {
    if (currentView !== "connectors") return;
    const onVis = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    document.addEventListener("visibilitychange", onVis);
    return () => document.removeEventListener("visibilitychange", onVis);
  }, [currentView, refresh]);

  const runningCount = missions.filter((m) => m.status === "running").length;
  const backendOffline = connection.status === "offline";

  const handleMonitorConnectedServices = async () => {
    setIsMonitoring(true);
    try {
      await monitorConnectedServices();
    } finally {
      setIsMonitoring(false);
    }
  };

  return (
    <div className="flex h-screen bg-transparent text-[13px] font-medium leading-none text-foreground selection:bg-primary/10">
      <BackgroundGrid />
      {/* Sidebar Navigation */}
      <aside className="flex w-64 flex-col border-r border-border/20 bg-black/40 backdrop-blur-3xl shadow-2xl">
        <div className="flex h-14 items-center gap-2.5 border-b border-border/50 px-5">
          <div className="flex h-6 w-6 items-center justify-center rounded-md bg-foreground text-background">
            <Command className="h-3.5 w-3.5" />
          </div>
          <span className="font-bold tracking-tight text-[14px]">AUTOPILOT</span>
        </div>


        <nav className="flex-1 space-y-1 p-3">
          <NavItem
            icon={LayoutDashboard}
            label="Missions"
            active={currentView === "dashboard"}
            onClick={() => setCurrentView("dashboard")}
            count={totals.missions || missions.length}
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
            count={totals.approvals || approvals.length}
          />
          <NavItem
            icon={Activity}
            label="System Trace"
            active={currentView === "traces"}
            onClick={() => setCurrentView("traces")}
            count={totals.traces || traces.length}
          />
          <NavItem
            icon={BarChart3}
            label="Analytics"
            active={currentView === "analytics"}
            onClick={() => setCurrentView("analytics")}
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
              onClick={handleMonitorConnectedServices}
              disabled={isMonitoring || backendOffline}
              className={cn(
                "group relative flex items-center gap-2 overflow-hidden rounded-md bg-foreground px-4 py-1.5 text-[12px] font-bold text-background transition-all hover:opacity-90 active:scale-95 disabled:opacity-50",
                isMonitoring && "cursor-wait"
              )}
            >
              <AnimatePresence mode="wait">
                {isMonitoring ? (
                  <motion.div
                    key="loading"
                    initial={{ y: 20 }}
                    animate={{ y: 0 }}
                    exit={{ y: -20 }}
                    className="flex items-center gap-2"
                  >
                    <Activity className="h-3.5 w-3.5 animate-spin" />
                    Checking...
                  </motion.div>
                ) : (
                  <motion.div
                    key="idle"
                    initial={{ y: 20 }}
                    animate={{ y: 0 }}
                    exit={{ y: -20 }}
                    className="flex items-center gap-2"
                  >
                    <Activity className="h-3.5 w-3.5" />
                    Check Services
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
                      runtimeConnector={
                        runtimeConnectorByKey.get(c.id.toLowerCase()) ??
                        runtimeConnectorByKey.get(c.name.toLowerCase())
                      }
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

            {currentView === "analytics" && (
              <motion.div
                key="analytics"
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -10 }}
                className="p-8 max-w-5xl mx-auto"
              >
                <AnalyticsView />
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

  const presets: Array<{
    label: string;
    type: string;
    summary: string;
    entities: string;
    urgency: string;
  }> = [];

  const applyPreset = (p: typeof presets[0]) => {
    setType(p.type);
    setSummary(p.summary);
    setEntities(p.entities);
    setUrgency(p.urgency);
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-md p-4">
      <motion.div
        initial={{ opacity: 0, scale: 0.9, y: 20 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        className="w-full max-w-xl rounded-3xl border border-white/10 bg-[#0A0A0A] p-8 shadow-2xl relative overflow-hidden"
      >
        {/* Glow effect */}
        <div className="absolute -top-24 -right-24 h-48 w-48 bg-primary/10 blur-[80px] rounded-full" />

        <div className="mb-8 flex items-center justify-between relative">
          <div>
            <h3 className="text-xl font-bold tracking-tight">Ingest Manual Signal</h3>
            <p className="text-[12px] text-muted-foreground mt-1">Simulate an external event to trigger an autonomous mission.</p>
          </div>
          <button onClick={onClose} className="rounded-full h-8 w-8 flex items-center justify-center hover:bg-white/5 text-muted-foreground transition-colors">
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="space-y-6 relative">
          {/* Presets */}
          <div className="space-y-2">
            <label className="text-[10px] font-black uppercase tracking-[0.2em] text-muted-foreground/40">Presets</label>
            <div className="flex gap-2">
              {presets.map((p) => (
                <button
                  key={p.label}
                  onClick={() => applyPreset(p)}
                  className="flex-1 rounded-xl border border-white/5 bg-white/[0.02] py-2.5 text-[11px] font-bold hover:bg-white/[0.05] hover:border-white/10 transition-all active:scale-95"
                >
                  {p.label}
                </button>
              ))}
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4">
             <div className="space-y-2">
              <label className="text-[10px] font-black uppercase tracking-[0.2em] text-muted-foreground/40">Type</label>
              <div className="relative">
                <select
                  value={type}
                  onChange={(e) => setType(e.target.value)}
                  className="w-full rounded-xl border border-white/5 bg-black px-4 py-2.5 text-[12px] outline-none focus:ring-1 focus:ring-primary/50 appearance-none font-bold"
                >
                  <option value="support_escalation">Support Escalation</option>
                  <option value="monitoring_error">Monitoring Error</option>
                  <option value="security_alert">Security Alert</option>
                  <option value="code_review">Code Review</option>
                  <option value="custom">Custom Signal</option>
                </select>
                <ChevronDown className="absolute right-4 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground/40 pointer-events-none" />
              </div>
            </div>
            <div className="space-y-2">
              <label className="text-[10px] font-black uppercase tracking-[0.2em] text-muted-foreground/40">Urgency</label>
              <div className="flex gap-1 bg-black p-1 rounded-xl border border-white/5">
                {["low", "med", "high", "crit"].map((u) => {
                  const val = u === "med" ? "medium" : u === "crit" ? "critical" : u;
                  return (
                    <button
                      key={u}
                      onClick={() => setUrgency(val)}
                      className={cn(
                        "flex-1 rounded-lg py-1.5 text-[10px] font-black uppercase transition-all",
                        urgency === val ? "bg-white/10 text-white shadow-sm" : "text-muted-foreground/40 hover:text-muted-foreground"
                      )}
                    >
                      {u}
                    </button>
                  );
                })}
              </div>
            </div>
          </div>

          <div className="space-y-2">
            <label className="text-[10px] font-black uppercase tracking-[0.2em] text-muted-foreground/40">Summary</label>
            <textarea
              value={summary}
              onChange={(e) => setSummary(e.target.value)}
              placeholder="What happened? E.g. 'Customer reported issue with billing...'"
              className="h-24 w-full resize-none rounded-2xl border border-white/5 bg-black px-4 py-3 text-[13px] outline-none focus:ring-1 focus:ring-primary/50 leading-relaxed placeholder:text-muted-foreground/20 font-medium"
            />
          </div>

          <div className="space-y-2">
            <label className="text-[10px] font-black uppercase tracking-[0.2em] text-muted-foreground/40">Involved Entities (Comma separated)</label>
            <div className="relative">
               <input
                value={entities}
                onChange={(e) => setEntities(e.target.value)}
                placeholder="checkout-service, acme-corp, v1.2.0"
                className="w-full rounded-xl border border-white/5 bg-black px-4 py-3 text-[13px] outline-none focus:ring-1 focus:ring-primary/50 font-mono"
              />
              <Layers className="absolute right-4 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground/20" />
            </div>
          </div>

          <button
            onClick={() => onSubmit({
              type,
              summary,
              urgency,
              source: "manual_ui",
              entities: entities.split(",").map(e => e.trim()).filter(Boolean),
              payload: {},
            })}
            disabled={!summary}
            className="group relative flex w-full items-center justify-center gap-2 overflow-hidden rounded-2xl bg-white px-6 py-4 text-[14px] font-black uppercase tracking-widest text-black transition-all hover:scale-[1.01] active:scale-[0.98] disabled:opacity-30 disabled:grayscale"
          >
            <Zap className="h-4 w-4 fill-current" />
            Ingest Into Runtime
          </button>
        </div>
      </motion.div>
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

      {/* Graph Flow — DAG Visualization */}
      <section className="space-y-6">
        <div className="flex items-center gap-3 border-b border-border/30 pb-4">
          <GitBranch className="h-5 w-5 text-muted-foreground/40" />
          <h3 className="text-lg font-bold tracking-tight">Execution Graph</h3>
          <span className="text-xs text-muted-foreground font-bold opacity-40">{mission.graph?.length} NODES</span>
        </div>
        <MissionDAG nodes={mission.graph || []} />
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

type AgentTraceStep = {
  step_number?: number;
  thought?: string;
  tool_call?: string;
  tool_result?: {
    success?: boolean;
    output?: string;
  };
};

function AgentCard({ run }: { run: AgentRun }) {
  const [showTrace, setShowTrace] = useState(false);
  const steps: AgentTraceStep[] = Array.isArray(run.metadata.steps) ? (run.metadata.steps as AgentTraceStep[]) : [];

  return (
    <div className="rounded-xl border border-border/40 bg-white/[0.01] p-4 space-y-4 shadow-sm hover:border-primary/20 transition-all">
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

      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4 text-[11px]">
          <span className="flex items-center gap-1.5 text-muted-foreground/40 font-bold">
            <Wrench className="h-3 w-3" /> {run.tool_calls} CALLS
          </span>
          <span className="flex items-center gap-1.5 text-muted-foreground/40 font-bold">
            <ShieldCheck className="h-3 w-3" /> {Math.round(run.confidence * 100)}% CONF
          </span>
        </div>

        {steps && steps.length > 0 && (
          <button 
            onClick={() => setShowTrace(!showTrace)}
            className="flex items-center gap-1 text-[10px] font-bold uppercase tracking-widest text-primary/60 hover:text-primary transition-colors"
          >
            {showTrace ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
            {showTrace ? "Hide Trace" : "View Trace"}
          </button>
        )}
      </div>

      <AnimatePresence>
        {showTrace && steps && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="overflow-hidden"
          >
            <div className="mt-2 space-y-4 pl-3 border-l-2 border-primary/10 py-1">
              {steps.map((step, idx) => (
                <div key={idx} className="space-y-2">
                  <div className="flex items-center gap-2">
                    <span className="text-[9px] font-black uppercase tracking-widest text-muted-foreground/30">Step {step.step_number}</span>
                    <div className="h-px flex-1 bg-border/10" />
                  </div>
                  <p className="text-[11px] text-muted-foreground/80 italic leading-relaxed">
                    {step.thought}
                  </p>
                  {step.tool_call && (
                    <div className="rounded-lg bg-black/40 p-2.5 border border-white/5 space-y-2">
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-2 text-[10px] font-bold text-amber-500/80">
                          <Wrench className="h-3 w-3" />
                          {step.tool_call}
                        </div>
                        <span className={cn(
                          "text-[8px] font-black uppercase px-1 rounded",
                          step.tool_result?.success ? "bg-emerald-500/10 text-emerald-500/60" : "bg-red-500/10 text-red-500/60"
                        )}>
                          {step.tool_result?.success ? "SUCCESS" : "FAILED"}
                        </span>
                      </div>
                      {step.tool_result?.output && (
                        <pre className="text-[9px] text-muted-foreground/40 font-mono bg-black/20 p-1.5 rounded overflow-x-auto whitespace-pre-wrap max-h-24">
                          {step.tool_result.output}
                        </pre>
                      )}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>

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

/** Normalize catalog auth_mode from JSON (string or rare enum-shaped object). */
function catalogAuthMode(c: ConnectorDirectoryItem): string {
  const raw = c.auth_mode as unknown;
  if (typeof raw === "string") return raw;
  if (raw && typeof raw === "object" && "value" in raw) {
    const v = (raw as { value: unknown }).value;
    if (typeof v === "string") return v;
  }
  return "";
}

const READINESS_NON_LIVE_MODES = new Set([
  "anonymous_public",
  "local_fallback",
  "webhook_only",
  "duckduckgo+hackernews",
  "open_meteo_fallback",
  "local_only",
  "missing_credentials",
  "missing_callback",
  "catalog_only",
]);

/**
 * Prefer readiness.integration_live when present (current backends).
 * Older stacks omit it — infer from configured + mode so CONNECTED matches reality.
 */
function integrationLiveFromReadiness(
  readiness?:
    | (Record<string, unknown> & {
        configured?: boolean;
        mode?: string;
        integration_live?: boolean;
      })
    | undefined,
): boolean {
  if (!readiness) return false;
  if (readiness.integration_live === true) return true;
  if (readiness.integration_live === false) return false;
  const mode = typeof readiness.mode === "string" ? readiness.mode : "";
  return Boolean(readiness.configured && mode && !READINESS_NON_LIVE_MODES.has(mode));
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
  const authMode = catalogAuthMode(connector);
  const canDemoConnect = connector.demo_available || authMode === "none";
  const readiness = runtimeConnector?.readiness as
    | (Record<string, unknown> & {
        configured?: boolean;
        detail?: string;
        mode?: string;
        missing?: string[];
        auth_url?: string;
        integration_live?: boolean;
      })
    | undefined;
  const oauthUrl = readiness?.auth_url as string | undefined;
  const oauthGateActive = Boolean(oauthUrl);
  const isOAuthConnector = authMode === "oauth" || oauthGateActive;
  const toolCount = runtimeConnector?.tool_count || connector.tools?.length || 0;

  const runtimeIntegration = integrationLiveFromReadiness(readiness);
  const oauthLive =
    Boolean(connector.oauth_token_present) ||
    Boolean(
      connector.live_connected && (authMode === "oauth" || authMode === "api_key"),
    );

  const trulyConnected = runtimeIntegration || oauthLive;

  const showDemoBadge =
    Boolean(connector.is_demo_connection) && !runtimeIntegration && !oauthLive;

  const showConnectedBadge = trulyConnected && !showDemoBadge;

  const oauthAuthorized =
    Boolean(connector.oauth_token_present) ||
    runtimeIntegration ||
    (!connector.is_demo_connection &&
      connected &&
      connector.connection_auth_mode === "oauth");

  const handleToggle = async () => {
    if (busy) return;
    setBusy(true);
    try {
      if (connected || trulyConnected) {
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
      "group relative flex flex-col rounded-2xl border border-border/40 bg-white/[0.01] p-6 transition-all hover:border-primary/40 hover:bg-white/[0.03] overflow-hidden shadow-sm hover:shadow-primary/5",
      !connector.implemented && "opacity-75"
    )}>
      {/* Background Glow */}
      <div className={cn(
        "absolute -right-4 -top-4 h-24 w-24 rounded-full blur-[40px] opacity-0 group-hover:opacity-100 transition-opacity duration-700",
        showConnectedBadge ? "bg-emerald-500/10" : showDemoBadge ? "bg-amber-500/10" : "bg-primary/10"
      )} />

      <div className="mb-6 flex items-start justify-between relative">
        <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-white/[0.03] border border-white/5 group-hover:scale-110 transition-all duration-500 group-hover:border-primary/20 shadow-inner">
          <Database className={cn(
            "h-6 w-6 transition-colors duration-500",
            showConnectedBadge ? "text-emerald-500" : showDemoBadge ? "text-amber-500" : "text-muted-foreground group-hover:text-primary"
          )} />
          {(showConnectedBadge || showDemoBadge) && (
            <div className={cn(
              "absolute inset-0 rounded-2xl border-2 animate-pulse",
              showConnectedBadge ? "border-emerald-500/20" : "border-amber-500/20"
            )} />
          )}
        </div>
        
        <div className="flex flex-col items-end gap-2">
          <div className={cn(
            "flex items-center gap-1.5 rounded-full px-3 py-1 text-[10px] font-black uppercase tracking-tighter ring-1 ring-inset shadow-sm",
            showConnectedBadge
              ? "bg-emerald-500/10 text-emerald-500 ring-emerald-500/20"
              : showDemoBadge
                ? "bg-amber-500/10 text-amber-500 ring-amber-500/20"
              : readiness
                ? "bg-amber-500/10 text-amber-500 ring-amber-500/20"
                : connector.implemented
                  ? "bg-blue-500/10 text-blue-500 ring-blue-500/20"
                  : "bg-white/5 text-muted-foreground ring-white/10"
          )}>
            <div className={cn("h-1.5 w-1.5 rounded-full",
              showConnectedBadge ? "bg-emerald-500 animate-pulse"
              : showDemoBadge ? "bg-amber-500 animate-pulse"
              : readiness ? "bg-amber-500"
              : "bg-current opacity-40"
            )} />
            {showConnectedBadge
              ? "CONNECTED"
              : showDemoBadge
                ? "DEMO"
              : readiness
                ? "CONFIG REQUIRED"
                : connector.implemented ? "ADAPTER" : "CATALOG"}
          </div>
          
          {toolCount > 0 && (
            <div className="flex items-center gap-1 text-[9px] font-black text-muted-foreground/30 uppercase tracking-widest">
              <Wrench className="h-3 w-3" />
              {toolCount} Tools
            </div>
          )}
        </div>
      </div>

      <div className="space-y-1.5 mb-6 flex-1 relative">
        <h3 className="text-[16px] font-bold group-hover:text-primary transition-colors">{connector.name}</h3>
        <p className="text-[12px] text-muted-foreground leading-relaxed line-clamp-2 opacity-70 group-hover:opacity-100 transition-opacity">{connector.description}</p>
      </div>

      <div className="space-y-5 relative">
        {readiness && (
          <div className="rounded-xl border border-white/5 bg-black/40 p-3 text-[11px] leading-relaxed text-muted-foreground/80 shadow-inner">
            <div className="flex items-center justify-between gap-2 mb-1">
              <span className="truncate">{readiness.detail as string}</span>
              <span className={cn(
                "flex-shrink-0 rounded-md border px-1.5 py-0.5 text-[9px] font-black uppercase tracking-wider",
                readiness.configured ? "border-emerald-500/20 text-emerald-500 bg-emerald-500/5" : "border-amber-500/20 text-amber-500 bg-amber-500/5"
              )}>
                {readiness.mode as string}
              </span>
            </div>
            {(readiness.missing as string[] | undefined)?.length ? (
              <div className="mt-2 font-mono text-[10px] text-amber-500/80 bg-amber-500/5 rounded p-1.5 border border-amber-500/10">
                Missing: {(readiness.missing as string[]).join(", ")}
              </div>
            ) : null}
          </div>
        )}

        <div className="flex flex-wrap gap-1">
          {connector.capabilities.map(cap => (
            <span key={cap} className="rounded-md border border-white/5 bg-white/[0.02] px-2 py-1 text-[9px] font-black uppercase text-muted-foreground/40 tracking-wider">
              {cap}
            </span>
          ))}
        </div>

        {/* Real OAuth button — redirects to Google consent screen */}
        {oauthGateActive && !oauthAuthorized ? (
          <a
            href={oauthUrl}
            className={cn(
              "flex w-full items-center justify-between rounded-lg border px-4 py-2.5 text-[12px] font-bold transition-all",
              connector.id === "github" 
                ? "border-slate-500/30 bg-slate-600/10 text-slate-300 hover:bg-slate-600/20 hover:border-slate-400/50"
                : "border-blue-500/30 bg-blue-600/10 text-blue-400 hover:bg-blue-600/20 hover:border-blue-400/50"
            )}
          >
            <span className="flex items-center gap-2">
              <Lock className="h-3.5 w-3.5" />
              Connect {connector.name}
            </span>
            <ArrowRight className="h-3.5 w-3.5 opacity-70" />
          </a>
        ) : oauthGateActive && oauthAuthorized ? (
          <div className="space-y-2">
            <div className="flex w-full items-center justify-between rounded-lg border border-emerald-500/20 bg-emerald-500/5 px-4 py-2.5 text-[12px] font-bold text-emerald-400">
              <span className="flex items-center gap-2">
                <CheckCircle2 className="h-3.5 w-3.5" />
                Authorized
              </span>
              <span className="text-[10px] opacity-60 uppercase tracking-wider">
                {(readiness?.mode as string | undefined) ||
                  connector.connection_auth_mode ||
                  "oauth"}
              </span>
            </div>
            {connected ? (
              <button
                type="button"
                onClick={async () => {
                  if (busy) return;
                  setBusy(true);
                  try {
                    await onDisconnect(connector.id);
                  } finally {
                    setBusy(false);
                  }
                }}
                disabled={busy}
                className="flex w-full items-center justify-center rounded-lg border border-white/10 bg-white/[0.02] px-4 py-2 text-[11px] font-bold text-muted-foreground transition-colors hover:bg-white/[0.05] hover:text-foreground"
              >
                Disconnect account
              </button>
            ) : null}
          </div>
        ) : (
          <button
            onClick={handleToggle}
            disabled={busy || (!(connected || trulyConnected) && !canDemoConnect && !isOAuthConnector)}
            className="flex w-full items-center justify-between rounded-lg bg-secondary/50 px-4 py-2.5 text-[12px] font-bold transition-all hover:bg-secondary disabled:cursor-not-allowed disabled:opacity-50"
          >
            {busy
              ? "Updating..."
              : (connected || trulyConnected)
                ? "Disconnect"
                : canDemoConnect
                  ? "Connect"
                  : "API Key Required"}
            <ArrowRight className="h-3.5 w-3.5 opacity-40 group-hover:translate-x-0.5 transition-transform" />
          </button>
        )}
      </div>

      {!connector.implemented && !canDemoConnect && !oauthUrl && (
        <div className="absolute inset-0 z-10 flex flex-col items-center justify-center bg-background/60 opacity-0 group-hover:opacity-100 backdrop-blur-[2px] transition-opacity">
          <Lock className="h-6 w-6 text-muted-foreground/40 mb-2" />
          <p className="text-[11px] font-bold text-muted-foreground uppercase tracking-widest">OAuth adapter pending</p>
        </div>
      )}
    </div>
  );
}

// ── DAG Visualization ─────────────────────────────────────────────────────

const NODE_KIND_COLORS: Record<string, { bg: string; border: string; text: string; glow: string }> = {
  signal:     { bg: "bg-blue-500/10",    border: "border-blue-500/30",    text: "text-blue-400",    glow: "shadow-[0_0_12px_rgba(59,130,246,0.3)]" },
  hypothesis: { bg: "bg-violet-500/10",  border: "border-violet-500/30",  text: "text-violet-400",  glow: "shadow-[0_0_12px_rgba(139,92,246,0.3)]" },
  branch:     { bg: "bg-cyan-500/10",    border: "border-cyan-500/30",    text: "text-cyan-400",    glow: "shadow-[0_0_12px_rgba(6,182,212,0.3)]" },
  subagent:   { bg: "bg-amber-500/10",   border: "border-amber-500/30",   text: "text-amber-400",   glow: "shadow-[0_0_12px_rgba(245,158,11,0.3)]" },
  operator:   { bg: "bg-pink-500/10",    border: "border-pink-500/30",    text: "text-pink-400",    glow: "shadow-[0_0_12px_rgba(236,72,153,0.3)]" },
  replan:     { bg: "bg-orange-500/10",  border: "border-orange-500/30",  text: "text-orange-400",  glow: "shadow-[0_0_12px_rgba(249,115,22,0.3)]" },
  action:     { bg: "bg-emerald-500/10", border: "border-emerald-500/30", text: "text-emerald-400", glow: "shadow-[0_0_12px_rgba(16,185,129,0.3)]" },
  policy:     { bg: "bg-red-500/10",     border: "border-red-500/30",     text: "text-red-400",     glow: "shadow-[0_0_12px_rgba(239,68,68,0.3)]" },
  validation: { bg: "bg-teal-500/10",    border: "border-teal-500/30",    text: "text-teal-400",    glow: "shadow-[0_0_12px_rgba(20,184,166,0.3)]" },
};

const DEFAULT_COLOR = { bg: "bg-white/5", border: "border-white/10", text: "text-muted-foreground", glow: "" };

function MissionDAG({ nodes }: { nodes: MissionGraphNode[] }) {
  if (!nodes.length) {
    return (
      <div className="rounded-lg border border-border/40 bg-white/[0.01] p-8 text-center text-[13px] text-muted-foreground">
        No graph nodes recorded for this mission.
      </div>
    );
  }

  // Build a depth map using BFS from root nodes
  const childrenMap = new Map<string, string[]>();
  const roots: string[] = [];

  for (const node of nodes) {
    if (!node.parent_ids || node.parent_ids.length === 0) {
      roots.push(node.id);
    }
    for (const pid of (node.parent_ids || [])) {
      const existing = childrenMap.get(pid) || [];
      existing.push(node.id);
      childrenMap.set(pid, existing);
    }
  }

  // BFS to assign depths
  const depthMap = new Map<string, number>();
  const queue = roots.map(id => ({ id, depth: 0 }));
  const visited = new Set<string>();
  while (queue.length > 0) {
    const { id, depth } = queue.shift()!;
    if (visited.has(id)) continue;
    visited.add(id);
    depthMap.set(id, depth);
    for (const childId of (childrenMap.get(id) || [])) {
      if (!visited.has(childId)) {
        queue.push({ id: childId, depth: depth + 1 });
      }
    }
  }
  // Nodes not reached by BFS
  for (const node of nodes) {
    if (!depthMap.has(node.id)) depthMap.set(node.id, 0);
  }

  // Group by depth
  const maxDepth = Math.max(...Array.from(depthMap.values()), 0);
  const layers: MissionGraphNode[][] = [];
  for (let d = 0; d <= maxDepth; d++) {
    layers.push(nodes.filter(n => depthMap.get(n.id) === d));
  }

  return (
    <div className="space-y-3">
      {layers.map((layer, depth) => (
        <div key={depth} className="relative">
          {/* Depth indicator */}
          {depth > 0 && (
            <div className="flex justify-center mb-3">
              <div className="h-6 w-px bg-gradient-to-b from-primary/30 to-primary/5" />
            </div>
          )}
          <div className={cn(
            "grid gap-3",
            layer.length === 1 ? "grid-cols-1 max-w-2xl mx-auto" :
            layer.length === 2 ? "grid-cols-2" :
            layer.length === 3 ? "grid-cols-3" :
            "grid-cols-2 lg:grid-cols-4"
          )}>
            {layer.map((node) => {
              const colors = NODE_KIND_COLORS[node.kind] || DEFAULT_COLOR;
              const isComplete = node.status === "complete";
              const isRunning = node.status === "started";
              return (
                <motion.div
                  key={node.id}
                  layout
                  initial={{ opacity: 0, y: 16, scale: 0.95 }}
                  animate={{ 
                    opacity: 1, 
                    y: 0, 
                    scale: 1,
                    borderColor: isRunning ? "rgba(245, 158, 11, 0.4)" : undefined,
                    boxShadow: isRunning ? "0 0 15px rgba(245, 158, 11, 0.15)" : undefined,
                  }}
                  exit={{ opacity: 0, scale: 0.9 }}
                  transition={{ 
                    layout: { type: "spring", stiffness: 300, damping: 30 },
                    opacity: { duration: 0.2 },
                    y: { duration: 0.2 },
                    borderColor: { duration: 0.5, repeat: isRunning ? Infinity : 0, repeatType: "reverse" },
                    boxShadow: { duration: 0.8, repeat: isRunning ? Infinity : 0, repeatType: "reverse" }
                  }}
                  className={cn(
                    "group relative rounded-xl border p-4 transition-all hover:scale-[1.02]",
                    colors.bg, colors.border,
                    isComplete && colors.glow,
                    isRunning && "ring-1 ring-amber-500/30"
                  )}
                >
                  {/* Status dot */}
                  <div className={cn(
                    "absolute -top-1.5 -right-1.5 h-3 w-3 rounded-full border-2 border-background",
                    isComplete ? "bg-emerald-500" : node.status === "failed" ? "bg-red-500" : "bg-amber-400 animate-pulse"
                  )} />

                  <div className="flex items-center gap-2 mb-2">
                    <span className={cn("text-[9px] font-black uppercase tracking-widest", colors.text)}>
                      {node.kind}
                    </span>
                    {node.branch_id && (
                      <span className="text-[8px] font-mono text-muted-foreground/30 truncate">
                        ⎇ {node.branch_id.slice(0, 8)}
                      </span>
                    )}
                  </div>
                  <h4 className="text-[13px] font-bold leading-tight mb-1.5 line-clamp-2">{node.title}</h4>
                  <p className="text-[11px] leading-relaxed text-muted-foreground/50 line-clamp-2">{node.summary}</p>

                  {/* Parent connection indicator */}
                  {node.parent_ids && node.parent_ids.length > 0 && (
                    <div className="mt-2 flex items-center gap-1 text-[9px] text-muted-foreground/30 font-mono">
                      <span>← {node.parent_ids.length} parent{node.parent_ids.length > 1 ? "s" : ""}</span>
                    </div>
                  )}
                </motion.div>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}

// ── Analytics View ────────────────────────────────────────────────────────

interface MissionStats {
  total: number;
  by_status: Record<string, number>;
  avg_confidence: number;
  avg_replans: number;
  avg_evidence: number;
}

interface AgentPerf {
  role: string;
  total_runs: number;
  avg_tool_calls: number;
  avg_confidence: number;
  failure_rate: number;
  avg_duration_ms: number;
}

interface ConnectorHealth {
  connector: string;
  mode?: string;
  detail?: string;
  total: number;
  complete: number;
  failed: number;
  skipped: number;
  blocked: number;
}

function AnalyticsView() {
  const [stats, setStats] = useState<MissionStats | null>(null);
  const [agents, setAgents] = useState<AgentPerf[]>([]);
  const [connHealth, setConnHealth] = useState<ConnectorHealth[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchAnalytics = useCallback(async () => {
    try {
      const [s, a, c] = await Promise.all([
        axios.get(`${API_BASE}/api/analytics/missions`, READ_CONFIG),
        axios.get(`${API_BASE}/api/analytics/agents`, READ_CONFIG),
        axios.get(`${API_BASE}/api/analytics/connectors`, READ_CONFIG),
      ]);
      setStats(s.data);
      setAgents(a.data);
      setConnHealth(c.data);
    } catch (err) {
      console.error("Failed to fetch analytics:", err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const refresh = window.setTimeout(() => {
      void fetchAnalytics();
    }, 0);
    const interval = window.setInterval(() => {
      void fetchAnalytics();
    }, 5000);
    return () => {
      window.clearTimeout(refresh);
      window.clearInterval(interval);
    };
  }, [fetchAnalytics]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64 text-muted-foreground">
        <Activity className="h-5 w-5 animate-spin mr-3" />
        Loading analytics...
      </div>
    );
  }

  return (
    <div className="space-y-10">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Analytics</h1>
        <p className="text-muted-foreground mt-2 text-[14px]">
          Runtime performance metrics across missions, agents, and connectors.
        </p>
      </div>

      {/* Mission Stats Cards */}
      {stats && (
        <section className="space-y-4">
          <h2 className="text-sm font-bold uppercase tracking-wider text-muted-foreground/60">Mission Overview</h2>
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
            <AnalyticCard label="Total Missions" value={stats.total.toString()} accent="text-foreground" />
            <AnalyticCard label="Avg Confidence" value={`${Math.round(stats.avg_confidence * 100)}%`} accent="text-emerald-400" />
            <AnalyticCard label="Avg Replans" value={stats.avg_replans.toFixed(1)} accent="text-amber-400" />
            <AnalyticCard label="Avg Evidence" value={stats.avg_evidence.toFixed(1)} accent="text-blue-400" />
            <AnalyticCard label="Complete" value={(stats.by_status?.complete || 0).toString()} accent="text-emerald-400" />
          </div>
          {Object.keys(stats.by_status).length > 0 && (
            <div className="flex flex-wrap gap-2">
              {Object.entries(stats.by_status).map(([status, count]) => (
                <span key={status} className={cn(
                  "rounded-full px-3 py-1 text-[11px] font-bold border",
                  status === "complete" ? "border-emerald-500/20 bg-emerald-500/5 text-emerald-400"
                    : status === "failed" ? "border-red-500/20 bg-red-500/5 text-red-400"
                    : "border-amber-500/20 bg-amber-500/5 text-amber-400"
                )}>
                  {status}: {count}
                </span>
              ))}
            </div>
          )}
        </section>
      )}

      {/* Agent Performance Table */}
      {agents.length > 0 && (
        <section className="space-y-4">
          <h2 className="text-sm font-bold uppercase tracking-wider text-muted-foreground/60">Agent Performance</h2>
          <div className="rounded-xl border border-border/40 overflow-hidden">
            <table className="w-full text-[12px]">
              <thead>
                <tr className="border-b border-border/30 bg-white/[0.02]">
                  <th className="text-left px-4 py-3 font-bold uppercase tracking-wider text-muted-foreground/60 text-[10px]">Role</th>
                  <th className="text-right px-4 py-3 font-bold uppercase tracking-wider text-muted-foreground/60 text-[10px]">Runs</th>
                  <th className="text-right px-4 py-3 font-bold uppercase tracking-wider text-muted-foreground/60 text-[10px]">Avg Tools</th>
                  <th className="text-right px-4 py-3 font-bold uppercase tracking-wider text-muted-foreground/60 text-[10px]">Avg Conf</th>
                  <th className="text-right px-4 py-3 font-bold uppercase tracking-wider text-muted-foreground/60 text-[10px]">Fail Rate</th>
                  <th className="text-right px-4 py-3 font-bold uppercase tracking-wider text-muted-foreground/60 text-[10px]">Avg Duration</th>
                </tr>
              </thead>
              <tbody>
                {agents.map((a) => (
                  <tr key={a.role} className="border-b border-border/20 hover:bg-white/[0.02] transition-colors">
                    <td className="px-4 py-3 font-bold">{a.role}</td>
                    <td className="text-right px-4 py-3 tabular-nums">{a.total_runs}</td>
                    <td className="text-right px-4 py-3 tabular-nums">{a.avg_tool_calls}</td>
                    <td className="text-right px-4 py-3 tabular-nums">
                      <span className={a.avg_confidence >= 0.7 ? "text-emerald-400" : a.avg_confidence >= 0.4 ? "text-amber-400" : "text-red-400"}>
                        {Math.round(a.avg_confidence * 100)}%
                      </span>
                    </td>
                    <td className="text-right px-4 py-3 tabular-nums">
                      <span className={a.failure_rate === 0 ? "text-emerald-400" : "text-red-400"}>
                        {Math.round(a.failure_rate * 100)}%
                      </span>
                    </td>
                    <td className="text-right px-4 py-3 tabular-nums text-muted-foreground">{a.avg_duration_ms.toFixed(0)}ms</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* Connector Health */}
      <section className="space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-bold uppercase tracking-wider text-muted-foreground/60">Connector Health</h2>
          <span className="text-[10px] font-bold text-muted-foreground/40 uppercase tracking-wider">{connHealth.length} connected</span>
        </div>
        {connHealth.length === 0 ? (
          <div className="rounded-xl border border-border/40 bg-white/[0.01] p-8 text-center">
            <Database className="h-8 w-8 mx-auto mb-3 text-muted-foreground/20" />
            <p className="text-[13px] font-bold text-muted-foreground">No connectors connected yet</p>
            <p className="text-[11px] text-muted-foreground/50 mt-1">Connect your services in the Connector Hub to see health data here.</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
            {connHealth.map((c) => {
              const successRate = c.total > 0 ? c.complete / c.total : null;
              const hasHistory = c.total > 0;
              return (
                <div key={c.connector} className="rounded-xl border border-border/40 bg-white/[0.01] p-4 space-y-3 hover:border-emerald-500/20 transition-all">
                  <div className="flex items-center justify-between">
                    <span className="font-bold text-[13px] capitalize">{c.connector.replace(/_/g, " ")}</span>
                    <div className="flex items-center gap-2">
                      <span className={cn(
                        "text-[9px] font-black uppercase tracking-wider px-1.5 py-0.5 rounded border",
                        "border-emerald-500/20 text-emerald-500 bg-emerald-500/5"
                      )}>
                        {c.mode || "connected"}
                      </span>
                    </div>
                  </div>

                  {/* Progress bar — only shown when there's action history */}
                  {hasHistory ? (
                    <>
                      <div className="space-y-1">
                        <div className="flex justify-between text-[10px] text-muted-foreground/50">
                          <span>{c.total} actions</span>
                          <span>{successRate !== null ? `${Math.round(successRate * 100)}% success` : ""}</span>
                        </div>
                        <div className="h-1.5 rounded-full bg-white/5 overflow-hidden">
                          <div
                            className={cn(
                              "h-full rounded-full transition-all duration-700",
                              successRate === null ? "bg-muted-foreground/20" :
                              successRate >= 0.8 ? "bg-emerald-500" :
                              successRate >= 0.5 ? "bg-amber-500" : "bg-red-500"
                            )}
                            style={{ width: `${successRate !== null ? Math.max(successRate * 100, 3) : 100}%` }}
                          />
                        </div>
                      </div>
                      <div className="flex flex-wrap gap-2 text-[10px] font-bold">
                        {c.complete > 0 && <span className="text-emerald-400">{c.complete} complete</span>}
                        {c.skipped > 0 && <span className="text-amber-400">{c.skipped} skipped</span>}
                        {c.blocked > 0 && <span className="text-orange-400">{c.blocked} blocked</span>}
                        {c.failed > 0 && <span className="text-red-400">{c.failed} failed</span>}
                      </div>
                    </>
                  ) : (
                    <p className="text-[11px] text-muted-foreground/40 italic">
                      {c.detail || "Connected — no actions run yet"}
                    </p>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </section>

      {!stats?.total && agents.length === 0 && connHealth.length === 0 && (
        <div className="rounded-lg border border-border/40 bg-white/[0.01] p-12 text-center text-muted-foreground">
          <BarChart3 className="h-10 w-10 mx-auto mb-4 opacity-20" />
          <p className="text-[14px] font-bold">No analytics data yet</p>
          <p className="text-[12px] mt-1 opacity-60">Run a simulation to generate mission, agent, and connector metrics.</p>
        </div>
      )}
    </div>
  );
}

function AnalyticCard({ label, value, accent }: { label: string; value: string; accent: string }) {
  return (
    <div className="rounded-xl border border-border/40 bg-white/[0.01] p-4">
      <div className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground/40 mb-1">{label}</div>
      <div className={cn("text-[22px] font-black tracking-tight", accent)}>{value}</div>
    </div>
  );
}
