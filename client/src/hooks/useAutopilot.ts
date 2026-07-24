import { useState, useEffect, useCallback, useRef } from "react";
import axios from "axios";
import {
  Mission,
  Trace,
  Connector,
  ConnectorDirectoryItem,
  ActionApproval,
  Operator,
  OperatorProbeResult,
  ProviderHealth,
  TracingStatus,
  MCPClientConfig,
} from "@/types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";
const API_KEY = process.env.NEXT_PUBLIC_AUTOPILOT_API_KEY || "";
const writeConfig = API_KEY ? { headers: { "x-autopilot-key": API_KEY } } : undefined;
const readConfig = writeConfig;
const eventStreamUrl = `${API_BASE}/api/events${API_KEY ? `?access_key=${encodeURIComponent(API_KEY)}` : ""}`;

export type BackendConnection = {
  status: "checking" | "connected" | "degraded" | "offline";
  provider: string;
  runtime: string;
  message: string;
  apiBase: string;
  sseConnected: boolean;
};

export type ManualSignal = {
  source: string;
  type: string;
  summary: string;
  entities: string[];
  urgency: string;
  payload: Record<string, unknown>;
};

export type MonitoringCheckResult = {
  status: string;
  source: string;
  duration_ms: number;
  targets: number;
  checks: Array<Record<string, unknown>>;
  issues: Array<Record<string, unknown>>;
  mission_id?: string | null;
};

const initialConnection: BackendConnection = {
  status: "checking",
  provider: "-",
  runtime: "unknown",
  message: "Checking backend connection",
  apiBase: API_BASE || "same-origin proxy",
  sseConnected: false,
};

export function useAutopilot() {
  const [missions, setMissions] = useState<Mission[]>([]);
  const [missionDetails, setMissionDetails] = useState<Record<string, Mission>>({});
  const [traces, setTraces] = useState<Trace[]>([]);
  const [totals, setTotals] = useState({ missions: 0, traces: 0, approvals: 0 });
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [connectorDirectory, setConnectorDirectory] = useState<ConnectorDirectoryItem[]>([]);
  const [operators, setOperators] = useState<Operator[]>([]);
  const [operatorProbeResults, setOperatorProbeResults] = useState<Record<string, OperatorProbeResult>>({});
  const [providerHealth, setProviderHealth] = useState<ProviderHealth | null>(null);
  const [tracingStatus, setTracingStatus] = useState<TracingStatus | null>(null);
  const [mcpClientConfig, setMcpClientConfig] = useState<MCPClientConfig | null>(null);
  const [approvals, setApprovals] = useState<ActionApproval[]>([]);
  const [connection, setConnection] = useState<BackendConnection>(initialConnection);
  const [isLoading, setIsLoading] = useState(true);

  const sseRef = useRef<EventSource | null>(null);
  const setupSSERef = useRef<() => void>(() => undefined);
  const reconnectRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const failRef = useRef(0);

  const fetchMissionsAndTraces = useCallback(async () => {
    try {
      const [missionsRes, tracesRes, approvalsRes] = await Promise.all([
        axios.get(`${API_BASE}/api/missions?compact=true&limit=50`, readConfig),
        axios.get(`${API_BASE}/api/traces`, readConfig),
        axios.get(`${API_BASE}/api/approvals`, readConfig).catch(() => ({ data: [] })),
      ]);
      setMissions(missionsRes.data || []);
      setTraces((tracesRes.data || []).slice(0, 50));
      setApprovals(approvalsRes.data || []);
      setTotals({
        missions: (missionsRes.data || []).length,
        traces: (tracesRes.data || []).length,
        approvals: (approvalsRes.data || []).length,
      });
      failRef.current = 0;
    } catch {
      failRef.current += 1;
    }
  }, []);

  const fetchStaticData = useCallback(async () => {
    try {
      const [healthRes, connRes, dirRes, provRes, mcpConfigRes] = await Promise.all([
        axios.get(`${API_BASE}/health`),
        axios.get(`${API_BASE}/api/connectors`, readConfig),
        axios.get(`${API_BASE}/api/connector-directory`, readConfig),
        axios.get(`${API_BASE}/api/provider`, readConfig),
        axios.get(`${API_BASE}/api/mcp/client-config`, readConfig),
      ]);

      let operatorsData: Operator[] = [];
      let providerHealthData: ProviderHealth | null = null;
      let tracingStatusData: TracingStatus | null = null;

      try {
        const [operatorsRes, providerHealthRes, tracingStatusRes] = await Promise.allSettled([
          axios.get(`${API_BASE}/api/operators`, readConfig),
          axios.get(`${API_BASE}/api/provider/health`, readConfig),
          axios.get(`${API_BASE}/api/tracing/status`, readConfig),
        ]);
        if (operatorsRes.status === "fulfilled") operatorsData = operatorsRes.value.data || [];
        if (providerHealthRes.status === "fulfilled") providerHealthData = providerHealthRes.value.data;
        if (tracingStatusRes.status === "fulfilled") tracingStatusData = tracingStatusRes.value.data;
      } catch { /* non-fatal */ }

      setConnectors(connRes.data || []);
      setConnectorDirectory(dirRes.data || []);
      setOperators(operatorsData);
      setProviderHealth(providerHealthData);
      setTracingStatus(tracingStatusData);
      setMcpClientConfig(mcpConfigRes.data || null);
      setConnection((current) => ({
        ...current,
        status: current.sseConnected ? "connected" : "degraded",
        provider: provRes.data.provider || healthRes.data.provider || "-",
        runtime: healthRes.data.runtime || "autonomous-operator-runtime",
        message: current.sseConnected ? "Backend and event stream connected" : "Backend reachable; waiting for event stream",
      }));

      await fetchMissionsAndTraces();
      failRef.current = 0;
    } catch (error) {
      console.error("Failed to connect to AUTOPILOT backend:", error);
      failRef.current += 1;
      setConnection((current) => ({
        ...current,
        status: "offline",
        message: "Backend API is unreachable from the frontend",
        sseConnected: false,
      }));
    } finally {
      setIsLoading(false);
    }
  }, [fetchMissionsAndTraces]);

  const setupSSE = useCallback(() => {
    if (sseRef.current) {
      sseRef.current.close();
    }
    const es = new EventSource(eventStreamUrl);
    sseRef.current = es;

    es.onopen = () => {
      setConnection((current) => ({
        ...current,
        status: current.status === "offline" ? "degraded" : "connected",
        message: "Backend and event stream connected",
        sseConnected: true,
      }));
    };

    es.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.missions) setMissions(data.missions);
        if (data.traces) setTraces((data.traces || []).slice(0, 50));
        if (data.totals) setTotals(data.totals);
      } catch (err) {
        console.error("SSE parse error:", err);
      }
    };

    es.onerror = () => {
      setConnection((current) => ({
        ...current,
        status: current.status === "checking" ? "offline" : "degraded",
        message: "Backend event stream disconnected — polling fallback active",
        sseConnected: false,
      }));
      // Reconnect SSE after a delay
      if (reconnectRef.current) clearTimeout(reconnectRef.current);
      reconnectRef.current = setTimeout(() => {
        if (document.visibilityState !== "hidden") {
          setupSSERef.current();
        }
      }, 5000);
    };
  }, []);

  useEffect(() => {
    setupSSERef.current = setupSSE;
  }, [setupSSE]);

  useEffect(() => {
    const initialLoad = setTimeout(() => {
      void fetchStaticData();
      setupSSE();
    }, 0);

    // Adaptive polling: faster when active missions exist
    const startPolling = () => {
      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = setInterval(() => {
        void fetchMissionsAndTraces();
      }, 3000);
    };
    startPolling();

    // Reconnect SSE when tab becomes visible again
    const onVis = () => {
      if (document.visibilityState === "visible") {
        void fetchMissionsAndTraces();
        if (!sseRef.current || sseRef.current.readyState === EventSource.CLOSED) {
          setupSSE();
        }
      }
    };
    document.addEventListener("visibilitychange", onVis);

    return () => {
      clearTimeout(initialLoad);
      if (reconnectRef.current) clearTimeout(reconnectRef.current);
      if (pollRef.current) clearInterval(pollRef.current);
      if (sseRef.current) sseRef.current.close();
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [fetchStaticData, fetchMissionsAndTraces, setupSSE]);

  const monitorConnectedServices = async () => {
    try {
      await axios.post<MonitoringCheckResult>(`${API_BASE}/api/monitoring/check`, {}, writeConfig);
      await fetchStaticData();
    } catch (error) {
      console.error("Failed to monitor connected services:", error);
      setConnection((current) => ({
        ...current,
        status: "offline",
        message: "Connected-service monitor could not reach the backend",
      }));
    }
  };

  const loadMissionDetails = useCallback(async (missionId: string) => {
    const response = await axios.get(`${API_BASE}/api/missions/${missionId}`, readConfig);
    const mission = response.data?.mission as Mission | undefined;
    if (mission) {
      setMissionDetails((current) => ({ ...current, [mission.id]: mission }));
    }
    return mission || null;
  }, []);

  const cleanupStuckMissions = async () => {
    try {
      const res = await axios.post(`${API_BASE}/api/missions/cleanup`, {}, writeConfig);
      await fetchMissionsAndTraces();
      return res.data as { cleaned: number; mission_ids: string[] };
    } catch (error) {
      console.error("Failed to cleanup stuck missions:", error);
      return null;
    }
  };

  const cancelMission = async (missionId: string, reason?: string) => {
    try {
      await axios.post(`${API_BASE}/api/missions/${missionId}/cancel`, { reason: reason || "Canceled from dashboard" }, writeConfig);
      await fetchMissionsAndTraces();
    } catch (error) {
      console.error("Failed to cancel mission:", error);
      throw error;
    }
  };

  const sendSignal = async (signal: ManualSignal) => {
    try {
      await axios.post(`${API_BASE}/api/signals`, signal, writeConfig);
      await fetchStaticData();
    } catch (error) {
      console.error("Failed to send signal:", error);
      setConnection((current) => ({
        ...current,
        status: "offline",
        message: "Manual signal request could not reach the backend",
      }));
      throw error;
    }
  };

  const connectConnector = async (connectorId: string, authMode: string = "demo") => {
    try {
      await axios.post(`${API_BASE}/api/connector-directory/${connectorId}/connect`, { auth_mode: authMode }, writeConfig);
      await fetchStaticData();
    } catch (error) {
      console.error("Failed to connect connector:", error);
      setConnection((current) => ({
        ...current,
        status: "degraded",
        message: "Connector update failed; backend is reachable but the request was rejected",
      }));
      throw error;
    }
  };

  const disconnectConnector = async (connectorId: string) => {
    try {
      await axios.post(`${API_BASE}/api/connector-directory/${connectorId}/disconnect`, {}, writeConfig);
      await fetchStaticData();
    } catch (error) {
      console.error("Failed to disconnect connector:", error);
      setConnection((current) => ({
        ...current,
        status: "degraded",
        message: "Connector update failed; backend is reachable but the request was rejected",
      }));
      throw error;
    }
  };

  const approveAction = async (approvalId: string) => {
    try {
      await axios.post(`${API_BASE}/api/approvals/${approvalId}/approve`, { decided_by: "dashboard" }, writeConfig);
      await fetchMissionsAndTraces();
    } catch (error) {
      console.error("Failed to approve action:", error);
      throw error;
    }
  };

  const rejectAction = async (approvalId: string) => {
    try {
      await axios.post(`${API_BASE}/api/approvals/${approvalId}/reject`, { decided_by: "dashboard" }, writeConfig);
      await fetchMissionsAndTraces();
    } catch (error) {
      console.error("Failed to reject action:", error);
      throw error;
    }
  };

  const probeOperator = async (operatorId: string) => {
    const response = await axios.post<OperatorProbeResult>(
      `${API_BASE}/api/operators/${operatorId}/probe`,
      {},
      writeConfig,
    );
    setOperatorProbeResults((current) => ({ ...current, [operatorId]: response.data }));
    await fetchStaticData();
  };

  return {
    missions,
    missionDetails,
    traces,
    connectors,
    connectorDirectory,
    operators,
    operatorProbeResults,
    providerHealth,
    tracingStatus,
    mcpClientConfig,
    approvals,
    provider: connection.provider,
    connection,
    isLoading,
    monitorConnectedServices,
    loadMissionDetails,
    cleanupStuckMissions,
    cancelMission,
    sendSignal,
    connectConnector,
    disconnectConnector,
    approveAction,
    rejectAction,
    probeOperator,
    refresh: fetchStaticData,
    totals,
  };
}
