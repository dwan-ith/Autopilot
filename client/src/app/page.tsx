"use client";

import { useState, useMemo, useEffect } from "react";
import { 
  Play, 
  RefreshCcw, 
  Activity, 
  Terminal, 
  ShieldCheck, 
  Search, 
  Layers, 
  CheckCircle2, 
  AlertCircle,
  Clock,
  ChevronRight,
  Database,
  Cpu
} from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { formatDistanceToNow } from "date-fns";
import { useAutopilot } from "@/hooks/useAutopilot";
import { cn } from "@/lib/utils";
import { Mission, StepStatus, MissionStatus } from "@/types";

export default function Dashboard() {
  const { missions, traces, connectors, provider, runDemo } = useAutopilot();
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const selectedMission = useMemo(() => 
    missions.find(m => m.id === selectedId) || missions[0], 
    [missions, selectedId]
  );

  useEffect(() => {
    if (!selectedId && missions.length > 0) {
      setSelectedId(missions[0].id);
    }
  }, [missions, selectedId]);

  return (
    <div className="relative z-10 min-h-screen p-4 md:p-8 flex flex-col gap-6 max-w-[1600px] mx-auto">
      {/* Header */}
      <header className="flex flex-col md:flex-row items-start md:items-center justify-between gap-4">
        <div className="flex items-center gap-4">
          <div className="relative h-12 w-12 flex items-center justify-center rounded-xl bg-primary/10 border border-primary/20 shadow-[0_0_15px_rgba(63,184,175,0.2)]">
            <motion.div 
              animate={{ opacity: [0.3, 0.6, 0.3], scale: [1, 1.1, 1] }}
              transition={{ duration: 3, repeat: Infinity }}
              className="absolute inset-0 rounded-xl border border-primary/30"
            />
            <Activity className="h-6 w-6 text-primary" />
          </div>
          <div>
            <div className="text-[10px] font-bold tracking-[0.2em] text-primary uppercase mb-0.5">Autonomous Operator Runtime</div>
            <h1 className="text-3xl font-black tracking-tighter logo-gradient">AUTOPILOT</h1>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <div className="hidden sm:flex items-center gap-2 px-3 py-1.5 rounded-full bg-secondary/10 border border-secondary/20 text-[11px] font-mono font-medium text-secondary">
            <Cpu className="h-3 w-3" />
            {provider}
          </div>
          <div className="flex items-center gap-2 px-3 py-1.5 rounded-full bg-card/50 border border-border text-[11px] font-mono">
            <div className={cn("h-1.5 w-1.5 rounded-full", missions.some(m => m.status === 'running') ? "bg-amber-400 animate-pulse" : "bg-emerald-400")} />
            {missions.some(m => m.status === 'running') ? "RUNNING" : "READY"}
          </div>
          <button 
            onClick={runDemo}
            className="group flex items-center gap-2 px-4 py-2 rounded-lg bg-gradient-to-br from-primary to-secondary text-white font-bold text-sm shadow-lg shadow-primary/20 hover:shadow-primary/30 hover:-translate-y-0.5 transition-all active:translate-y-0"
          >
            <Play className="h-4 w-4 fill-current group-hover:scale-110 transition-transform" />
            RUN DEMO
          </button>
        </div>
      </header>

      <main className="flex-1 grid grid-cols-1 lg:grid-cols-[340px_1fr_320px] gap-6 overflow-hidden">
        {/* Left Column: Missions */}
        <aside className="glass rounded-2xl flex flex-col overflow-hidden">
          <div className="px-5 py-4 border-b border-border bg-white/[0.02] flex items-center justify-between">
            <h2 className="text-xs font-bold uppercase tracking-widest text-muted-foreground flex items-center gap-2">
              <Layers className="h-3.5 w-3.5" />
              Missions
            </h2>
            <span className="font-mono text-[11px] bg-primary/10 text-primary px-2 py-0.5 rounded-full border border-primary/20">
              {missions.length}
            </span>
          </div>
          <div className="flex-1 overflow-y-auto p-3 space-y-2">
            <AnimatePresence initial={false}>
              {missions.map((mission) => (
                <MissionItem 
                  key={mission.id} 
                  mission={mission} 
                  isSelected={selectedId === mission.id}
                  onClick={() => setSelectedId(mission.id)}
                />
              ))}
              {missions.length === 0 && (
                <div className="flex flex-col items-center justify-center py-20 text-muted-foreground/40 text-center px-4">
                  <Clock className="h-8 w-8 mb-3 opacity-20" />
                  <p className="text-xs italic">No missions active. Click 'Run Demo' to initialize.</p>
                </div>
              )}
            </AnimatePresence>
          </div>
        </aside>

        {/* Middle Column: Detail */}
        <section className="glass rounded-2xl flex flex-col overflow-hidden">
          <div className="px-5 py-4 border-b border-border bg-white/[0.02] flex items-center justify-between">
            <h2 className="text-xs font-bold uppercase tracking-widest text-muted-foreground flex items-center gap-2">
              <Activity className="h-3.5 w-3.5" />
              Execution Graph
            </h2>
            {selectedMission && (
              <StatusBadge status={selectedMission.status} size="sm" />
            )}
          </div>
          
          <div className="flex-1 overflow-y-auto p-6">
            {selectedMission ? (
              <div className="space-y-8 max-w-3xl mx-auto">
                <header>
                  <h3 className="text-xl font-bold tracking-tight mb-2">{selectedMission.title}</h3>
                  <div className="flex flex-wrap gap-2">
                    <StatusBadge status={selectedMission.status} />
                    <SeverityBadge severity={selectedMission.severity} />
                    <Badge icon={<ShieldCheck className="h-3 w-3" />}>Conf: {(selectedMission.confidence * 100).toFixed(0)}%</Badge>
                    <Badge icon={<RefreshCcw className="h-3 w-3" />}>Replans: {selectedMission.replans}</Badge>
                  </div>
                </header>

                <div>
                  <SectionHeader title="Autonomous Investigation" icon={<Terminal className="h-4 w-4" />} />
                  <div className="relative pl-6 space-y-4 before:absolute before:left-[11px] before:top-2 before:bottom-2 before:w-[2px] before:bg-gradient-to-b before:from-primary before:to-secondary before:opacity-30">
                    <TimelineStep 
                      name="Signal Evaluator" 
                      role="Cognitive Agent" 
                      status="complete" 
                      output={selectedMission.summary}
                    />
                    {/* Add more timeline steps as needed, or map from mission.graph if available */}
                    {selectedMission.hypotheses.map((h, i) => (
                      <TimelineStep 
                        key={h.id}
                        name={`Hypothesis ${i+1}`}
                        role="Agent Thinking"
                        status="complete"
                        output={h.rationale}
                        isSubstep
                      />
                    ))}
                  </div>
                </div>

                {selectedMission.evidence.length > 0 && (
                  <div>
                    <SectionHeader title="Gathered Evidence" icon={<Search className="h-4 w-4" />} />
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                      {selectedMission.evidence.map((ev) => (
                        <div key={ev.id} className="p-3 rounded-xl bg-white/[0.03] border border-border hover:border-primary/30 transition-colors group">
                          <div className="flex items-center justify-between mb-2">
                            <span className="text-[10px] font-bold text-muted-foreground uppercase">{ev.source}</span>
                            <span className="text-[10px] font-mono text-primary">{(ev.confidence * 100).toFixed(0)}% Match</span>
                          </div>
                          <h4 className="text-xs font-bold mb-1 group-hover:text-primary transition-colors">{ev.title}</h4>
                          <p className="text-[11px] text-muted-foreground leading-relaxed line-clamp-2">{ev.summary}</p>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {selectedMission.actions.length > 0 && (
                  <div>
                    <SectionHeader title="Policy & Execution" icon={<ShieldCheck className="h-4 w-4" />} />
                    <div className="space-y-3">
                      {selectedMission.actions.map((act) => (
                        <div key={act.id} className="p-4 rounded-xl bg-emerald-500/5 border border-emerald-500/20 flex items-start gap-4">
                          <div className="h-8 w-8 rounded-lg bg-emerald-500/10 flex items-center justify-center shrink-0">
                            <CheckCircle2 className="h-4 w-4 text-emerald-400" />
                          </div>
                          <div>
                            <div className="text-[10px] font-bold text-emerald-400 uppercase tracking-wider mb-1">Bounded Action Executed</div>
                            <h4 className="text-sm font-bold mb-1">{act.action}</h4>
                            <p className="text-xs text-muted-foreground mb-2">{act.summary}</p>
                            {act.artifact_path && (
                              <div className="font-mono text-[10px] text-muted-foreground bg-black/40 px-2 py-1 rounded border border-border inline-block">
                                {act.artifact_path}
                              </div>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            ) : (
              <div className="h-full flex flex-col items-center justify-center text-muted-foreground/40 text-center max-w-xs mx-auto">
                <Layers className="h-12 w-12 mb-4 opacity-10" />
                <p className="text-sm font-medium">Select a mission to view the execution graph and autonomous reasoning steps.</p>
              </div>
            )}
          </div>
        </section>

        {/* Right Column: Connectors & Trace */}
        <aside className="space-y-6 overflow-hidden flex flex-col">
          <div className="glass rounded-2xl flex flex-col overflow-hidden flex-1 max-h-[50%]">
            <div className="px-5 py-4 border-b border-border bg-white/[0.02] flex items-center justify-between">
              <h2 className="text-xs font-bold uppercase tracking-widest text-muted-foreground flex items-center gap-2">
                <Database className="h-3.5 w-3.5" />
                Connectors
              </h2>
            </div>
            <div className="flex-1 overflow-y-auto p-3 space-y-2">
              {connectors.map((conn) => (
                <div key={conn.name} className="p-3 rounded-xl bg-white/[0.02] border border-border">
                  <div className="flex items-center justify-between mb-1.5">
                    <span className="text-xs font-bold">{conn.name}</span>
                    <div className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
                  </div>
                  <p className="text-[10px] text-muted-foreground mb-3 leading-tight">{conn.description}</p>
                  <div className="flex flex-wrap gap-1">
                    {conn.capabilities.map(cap => (
                      <span key={cap} className="text-[9px] font-mono bg-primary/5 text-primary/80 px-1.5 py-0.5 rounded border border-primary/10 uppercase font-bold">{cap}</span>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="glass rounded-2xl flex flex-col overflow-hidden flex-1">
            <div className="px-5 py-4 border-b border-border bg-white/[0.02] flex items-center justify-between">
              <h2 className="text-xs font-bold uppercase tracking-widest text-muted-foreground flex items-center gap-2">
                <Activity className="h-3.5 w-3.5" />
                Live Trace
              </h2>
              <span className="font-mono text-[10px] text-muted-foreground">{traces.length}</span>
            </div>
            <div className="flex-1 overflow-y-auto p-2 font-mono text-[10px]">
              {traces.map((trace) => (
                <div key={trace.id} className="py-1.5 px-3 rounded hover:bg-white/[0.03] flex items-start gap-3 group transition-colors">
                  <span className="text-muted-foreground/30 group-hover:text-muted-foreground transition-colors shrink-0">
                    {new Date(trace.created_at).toLocaleTimeString([], { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                  </span>
                  <div className="space-y-0.5 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className={cn(
                        "font-bold truncate",
                        trace.status === 'failed' ? "text-red-400" : 
                        trace.status === 'complete' ? "text-emerald-400" : "text-amber-400"
                      )}>
                        {trace.name}
                      </span>
                      <span className="text-muted-foreground/40 text-[9px]">{trace.status}</span>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </aside>
      </main>
    </div>
  );
}

// ── Shared Subcomponents ───────────────────────────────────────────────

function MissionItem({ mission, isSelected, onClick }: { mission: Mission, isSelected: boolean, onClick: () => void }) {
  return (
    <motion.div 
      layout
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      onClick={onClick}
      className={cn(
        "group p-4 rounded-xl cursor-pointer border transition-all duration-300",
        isSelected 
          ? "glass bg-primary/[0.04] border-primary/40 shadow-[0_4px_20px_-4px_rgba(63,184,175,0.15)] ring-1 ring-primary/20" 
          : "bg-white/[0.01] border-border hover:border-primary/20 hover:bg-white/[0.03]"
      )}
    >
      <div className="flex justify-between items-start mb-2 gap-3">
        <h3 className="text-[13px] font-bold leading-tight group-hover:text-primary transition-colors line-clamp-2">
          {mission.title}
        </h3>
        {isSelected && <ChevronRight className="h-4 w-4 text-primary shrink-0" />}
      </div>
      <div className="flex flex-wrap gap-1.5 mb-3">
        <StatusBadge status={mission.status} size="xs" />
        <SeverityBadge severity={mission.severity} size="xs" />
        <span className="text-[9px] font-bold font-mono text-muted-foreground bg-card px-1.5 py-0.5 rounded border border-border uppercase">
          {(mission.confidence * 100).toFixed(0)}%
        </span>
      </div>
      <p className="text-[11px] text-muted-foreground line-clamp-2 leading-relaxed opacity-80 mb-2">
        {mission.summary}
      </p>
      <div className="flex items-center justify-between mt-2 pt-2 border-t border-border/50">
        <span className="text-[9px] text-muted-foreground/50 font-medium">
          {formatDistanceToNow(new Date(mission.updated_at), { addSuffix: true })}
        </span>
        <span className="text-[9px] text-muted-foreground/50 font-mono">
          R:{mission.replans}
        </span>
      </div>
    </motion.div>
  );
}

function TimelineStep({ name, role, status, output, isSubstep }: { name: string, role: string, status: StepStatus, output?: string, isSubstep?: boolean }) {
  return (
    <motion.div 
      initial={{ opacity: 0, x: -10 }}
      animate={{ opacity: 1, x: 0 }}
      className={cn("relative group", isSubstep && "ml-4 opacity-80")}
    >
      <div className={cn(
        "absolute -left-[20px] top-[14px] h-2.5 w-2.5 rounded-full border-2 border-background z-10 transition-shadow duration-500",
        status === 'complete' ? "bg-primary shadow-[0_0_8px_rgba(63,184,175,0.6)]" : 
        status === 'failed' ? "bg-destructive" : "bg-amber-400 animate-pulse"
      )} />
      <div className="p-4 rounded-xl bg-white/[0.02] border border-border group-hover:border-primary/20 transition-all duration-300">
        <div className="flex items-center justify-between mb-1.5">
          <div className="font-bold text-xs group-hover:text-primary transition-colors">{name}</div>
          <span className="text-[9px] font-mono font-bold text-muted-foreground opacity-50 uppercase tracking-tighter">{role}</span>
        </div>
        {output && <p className="text-[11px] text-muted-foreground leading-relaxed">{output}</p>}
      </div>
    </motion.div>
  );
}

function SectionHeader({ title, icon }: { title: string, icon: React.ReactNode }) {
  return (
    <div className="flex items-center gap-2 mb-4">
      <div className="h-6 w-6 rounded-lg bg-primary/10 flex items-center justify-center">
        {icon}
      </div>
      <h4 className="text-xs font-bold uppercase tracking-wider text-primary/80">{title}</h4>
    </div>
  );
}

function Badge({ children, icon }: { children: React.ReactNode, icon?: React.ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-card border border-border text-[10px] font-bold text-muted-foreground uppercase tracking-tight">
      {icon}
      {children}
    </span>
  );
}

function StatusBadge({ status, size = "md" }: { status: MissionStatus | StepStatus, size?: "xs" | "sm" | "md" }) {
  const config = {
    complete: { color: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20", icon: CheckCircle2 },
    running: { color: "bg-amber-500/10 text-amber-400 border-amber-500/20", icon: Activity },
    queued: { color: "bg-blue-500/10 text-blue-400 border-blue-500/20", icon: Clock },
    failed: { color: "bg-red-500/10 text-red-400 border-red-500/20", icon: AlertCircle },
    started: { color: "bg-amber-500/10 text-amber-400 border-amber-500/20", icon: Activity },
    waiting: { color: "bg-slate-500/10 text-slate-400 border-slate-500/20", icon: Clock }
  };
  
  const { color, icon: Icon } = config[status] || config.queued;
  
  return (
    <span className={cn(
      "inline-flex items-center gap-1.5 rounded-full border font-bold uppercase tracking-tighter",
      color,
      size === "xs" ? "px-1.5 py-0.5 text-[8px]" : size === "sm" ? "px-2 py-0.5 text-[9px]" : "px-2.5 py-1 text-[10px]"
    )}>
      {size !== "xs" && <Icon className={cn(size === "sm" ? "h-2.5 w-2.5" : "h-3 w-3")} />}
      {status}
    </span>
  );
}

function SeverityBadge({ severity, size = "md" }: { severity: string, size?: "xs" | "sm" | "md" }) {
  const isHigh = ["high", "critical", "p0", "p1"].includes(severity.toLowerCase());
  return (
    <span className={cn(
      "inline-flex items-center rounded-full border font-bold uppercase tracking-tighter",
      isHigh ? "bg-red-500/10 text-red-400 border-red-500/20" : "bg-slate-500/10 text-slate-400 border-slate-500/20",
      size === "xs" ? "px-1.5 py-0.5 text-[8px]" : size === "sm" ? "px-2 py-0.5 text-[9px]" : "px-2.5 py-1 text-[10px]"
    )}>
      {severity}
    </span>
  );
}
