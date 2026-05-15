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
  ArrowRight
} from "lucide-react";
import { formatDistanceToNow } from "date-fns";
import { useAutopilot } from "@/hooks/useAutopilot";
import { cn } from "@/lib/utils";
import { Mission, StepStatus, MissionStatus } from "@/types";

export default function Dashboard() {
  const { missions, traces, connectors, provider, runDemo } = useAutopilot();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  
  // Hydration fix
  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    setMounted(true);
  }, []);

  const selectedMission = useMemo(() => 
    missions.find(m => m.id === selectedId) || missions[0], 
    [missions, selectedId]
  );

  useEffect(() => {
    if (!selectedId && missions.length > 0) {
      setSelectedId(missions[0].id);
    }
  }, [missions, selectedId]);

  if (!mounted) return null;

  return (
    <div className="min-h-screen flex flex-col font-sans">
      {/* Header */}
      <header className="sticky top-0 z-50 w-full border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60">
        <div className="container mx-auto max-w-[1400px] flex h-14 items-center justify-between px-4">
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-2">
              <div className="bg-foreground text-background p-1 rounded-md">
                <TerminalSquare className="h-4 w-4" />
              </div>
              <span className="font-semibold text-sm tracking-tight">AUTOPILOT</span>
            </div>
            <div className="hidden md:flex items-center gap-2 border-l pl-4 text-xs text-muted-foreground">
              <Cpu className="h-3 w-3" />
              {provider}
            </div>
          </div>

          <div className="flex items-center gap-3">
            <div className="flex items-center gap-1.5 px-2 py-1 rounded-md border text-xs font-medium">
              <div className={cn("h-1.5 w-1.5 rounded-full", missions.some(m => m.status === 'running') ? "bg-amber-500" : "bg-emerald-500")} />
              {missions.some(m => m.status === 'running') ? "Active" : "Ready"}
            </div>
            <button 
              onClick={runDemo}
              className="inline-flex items-center justify-center rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 bg-primary text-primary-foreground shadow hover:bg-primary/90 h-8 px-4 py-2 gap-2"
            >
              <Play className="h-3 w-3 fill-current" />
              Run Demo
            </button>
          </div>
        </div>
      </header>

      {/* Main Layout */}
      <main className="flex-1 container mx-auto max-w-[1400px] p-4 grid grid-cols-1 md:grid-cols-[300px_1fr_300px] gap-6">
        
        {/* Left Sidebar: Missions */}
        <aside className="flex flex-col gap-4">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-medium">Missions</h2>
            <Badge variant="outline">{missions.length}</Badge>
          </div>
          <div className="flex flex-col gap-2">
            {missions.length === 0 ? (
              <div className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">
                No missions yet.
              </div>
            ) : (
              missions.map(mission => (
                <button
                  key={mission.id}
                  onClick={() => setSelectedId(mission.id)}
                  className={cn(
                    "text-left p-3 rounded-lg border transition-colors hover:bg-accent",
                    selectedId === mission.id ? "bg-accent border-primary/50" : "bg-card"
                  )}
                >
                  <div className="flex items-start justify-between mb-2">
                    <span className="font-semibold text-sm line-clamp-1 pr-2">{mission.title}</span>
                    <StatusBadge status={mission.status} />
                  </div>
                  <p className="text-xs text-muted-foreground line-clamp-2 mb-2">
                    {mission.summary || "Pending analysis..."}
                  </p>
                  <div className="flex items-center justify-between text-[10px] text-muted-foreground">
                    <span>{formatDistanceToNow(new Date(mission.updated_at), { addSuffix: true })}</span>
                    <span className="font-mono">{(mission.confidence * 100).toFixed(0)}% Conf</span>
                  </div>
                </button>
              ))
            )}
          </div>
        </aside>

        {/* Center: Mission Detail */}
        <section className="flex flex-col gap-6 border-x px-6 pb-6">
          {selectedMission ? (
            <div className="space-y-8 animate-in fade-in duration-300">
              <div className="space-y-4">
                <h1 className="text-2xl font-semibold tracking-tight">{selectedMission.title}</h1>
                <div className="flex flex-wrap items-center gap-2">
                  <StatusBadge status={selectedMission.status} />
                  <Badge variant={selectedMission.severity === "high" ? "destructive" : "secondary"}>
                    {selectedMission.severity} severity
                  </Badge>
                  <Badge variant="outline">
                    {(selectedMission.confidence * 100).toFixed(0)}% confidence
                  </Badge>
                  <Badge variant="outline">
                    {selectedMission.replans} replans
                  </Badge>
                </div>
              </div>

              {/* Execution Graph */}
              <div className="space-y-4">
                <h3 className="text-sm font-medium border-b pb-2 flex items-center gap-2">
                  <Activity className="h-4 w-4" />
                  Execution Timeline
                </h3>
                <div className="relative pl-4 space-y-4 before:absolute before:left-[7px] before:top-2 before:bottom-2 before:w-[1px] before:bg-border">
                  <TimelineItem 
                    title="Mission Initialized" 
                    description={selectedMission.summary}
                    status="complete"
                  />
                  {selectedMission.hypotheses?.map((h, i) => (
                    <TimelineItem 
                      key={h.id}
                      title={`Hypothesis: ${h.title}`}
                      description={h.rationale}
                      status={selectedMission.status === "running" && i === (selectedMission.hypotheses?.length || 0) - 1 ? "running" : "complete"}
                    />
                  ))}
                  {selectedMission.status === "complete" && (
                    <TimelineItem 
                      title="Actions Published" 
                      status="complete"
                    />
                  )}
                </div>
              </div>

              {/* Evidence */}
              {(selectedMission.evidence?.length || 0) > 0 && (
                <div className="space-y-4">
                  <h3 className="text-sm font-medium border-b pb-2 flex items-center gap-2">
                    <Search className="h-4 w-4" />
                    Gathered Evidence
                  </h3>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    {selectedMission.evidence?.map(ev => (
                      <div key={ev.id} className="rounded-lg border bg-card p-4 shadow-sm">
                        <div className="flex items-center justify-between mb-2">
                          <span className="text-xs font-mono text-muted-foreground uppercase">{ev.source}</span>
                          <span className="text-xs font-medium">{(ev.confidence * 100).toFixed(0)}%</span>
                        </div>
                        <h4 className="text-sm font-medium mb-1">{ev.title}</h4>
                        <p className="text-xs text-muted-foreground">{ev.summary}</p>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Actions */}
              {(selectedMission.actions?.length || 0) > 0 && (
                <div className="space-y-4">
                  <h3 className="text-sm font-medium border-b pb-2 flex items-center gap-2">
                    <ShieldCheck className="h-4 w-4" />
                    Executed Actions
                  </h3>
                  <div className="space-y-3">
                    {selectedMission.actions?.map(act => (
                      <div key={act.id} className="flex items-start gap-3 rounded-lg border bg-card p-4 shadow-sm">
                        <CheckCircle2 className="h-5 w-5 text-emerald-500 shrink-0 mt-0.5" />
                        <div>
                          <div className="font-medium text-sm mb-1">{act.action}</div>
                          <p className="text-xs text-muted-foreground mb-2">{act.summary}</p>
                          {act.artifact_path && (
                            <code className="text-[10px] bg-muted px-1.5 py-0.5 rounded">
                              {act.artifact_path}
                            </code>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

            </div>
          ) : (
            <div className="h-full flex items-center justify-center text-muted-foreground">
              Select a mission to view details.
            </div>
          )}
        </section>

        {/* Right Sidebar: Connectors & Traces */}
        <aside className="flex flex-col gap-8">
          {/* Connectors */}
          <div className="space-y-4">
            <h2 className="text-sm font-medium flex items-center gap-2">
              <Database className="h-4 w-4" />
              Connectors
            </h2>
            <div className="space-y-2">
              {connectors.map(conn => (
                <div key={conn.name} className="p-3 rounded-lg border bg-card">
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-xs font-semibold">{conn.name}</span>
                    <div className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
                  </div>
                  <p className="text-[10px] text-muted-foreground mb-2">{conn.description}</p>
                  <div className="flex flex-wrap gap-1">
                    {conn.capabilities.map(cap => (
                      <span key={cap} className="text-[9px] bg-secondary px-1 py-0.5 rounded font-mono uppercase">
                        {cap}
                      </span>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Traces */}
          <div className="space-y-4 flex-1">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-medium flex items-center gap-2">
                <Activity className="h-4 w-4" />
                Live Trace
              </h2>
              <span className="text-xs text-muted-foreground">{traces.length}</span>
            </div>
            <div className="rounded-lg border bg-card h-[300px] overflow-y-auto p-2">
              {traces.length === 0 ? (
                <div className="p-4 text-center text-xs text-muted-foreground">No traces yet.</div>
              ) : (
                traces.map(trace => (
                  <div key={trace.id} className="flex gap-2 text-[10px] font-mono py-1 border-b last:border-0 hover:bg-accent px-1 transition-colors">
                    <span className="text-muted-foreground shrink-0">
                      {new Date(trace.created_at).toLocaleTimeString([], { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                    </span>
                    <span className={cn(
                      "truncate",
                      trace.status === 'failed' ? "text-red-500" : 
                      trace.status === 'complete' ? "text-emerald-500" : "text-amber-500"
                    )}>
                      {trace.name}
                    </span>
                  </div>
                ))
              )}
            </div>
          </div>
        </aside>

      </main>
    </div>
  );
}

// ── Components ─────────────────────────────────────────────────────────

function Badge({ children, variant = "default" }: { children: React.ReactNode, variant?: "default" | "secondary" | "destructive" | "outline" }) {
  const variants = {
    default: "border-transparent bg-primary text-primary-foreground shadow",
    secondary: "border-transparent bg-secondary text-secondary-foreground",
    destructive: "border-transparent bg-destructive text-destructive-foreground shadow",
    outline: "text-foreground border-border",
  };
  return (
    <span className={cn("inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-semibold transition-colors", variants[variant])}>
      {children}
    </span>
  );
}

function StatusBadge({ status }: { status: MissionStatus | StepStatus | "running" }) {
  const config = {
    complete: { color: "text-emerald-500 bg-emerald-500/10 border-emerald-500/20", icon: CheckCircle2 },
    running: { color: "text-amber-500 bg-amber-500/10 border-amber-500/20", icon: Activity },
    queued: { color: "text-blue-500 bg-blue-500/10 border-blue-500/20", icon: Clock },
    failed: { color: "text-red-500 bg-red-500/10 border-red-500/20", icon: AlertCircle },
    started: { color: "text-amber-500 bg-amber-500/10 border-amber-500/20", icon: Activity },
    waiting: { color: "text-slate-500 bg-slate-500/10 border-slate-500/20", icon: Clock }
  };
  
  const { color, icon: Icon } = config[status] || config.queued;
  
  return (
    <span className={cn("inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[10px] font-medium uppercase", color)}>
      <Icon className="h-3 w-3" />
      {status}
    </span>
  );
}

function TimelineItem({ title, description, status }: { title: string, description?: string, status: "complete" | "running" | "failed" }) {
  return (
    <div className="relative">
      <div className={cn(
        "absolute -left-[18px] top-1 h-2.5 w-2.5 rounded-full border-2 border-background z-10",
        status === 'complete' ? "bg-emerald-500" : 
        status === 'failed' ? "bg-red-500" : "bg-amber-500 animate-pulse"
      )} />
      <div className="pb-4">
        <h4 className="text-sm font-medium leading-none mb-1">{title}</h4>
        {description && <p className="text-xs text-muted-foreground mt-1.5">{description}</p>}
      </div>
    </div>
  );
}
