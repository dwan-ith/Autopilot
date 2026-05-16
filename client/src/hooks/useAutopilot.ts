import { useState, useEffect, useCallback } from "react";
import axios from "axios";
import { Mission, Trace, Connector, ConnectorDirectoryItem, ActionApproval, Operator, OperatorProbeResult } from "@/types";

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
  const [traces, setTraces] = useState<Trace[]>([]);
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [connectorDirectory, setConnectorDirectory] = useState<ConnectorDirectoryItem[]>([]);
  const [operators, setOperators] = useState<Operator[]>([]);
  const [operatorProbeResults, setOperatorProbeResults] = useState<Record<string, OperatorProbeResult>>({});
  const [approvals, setApprovals] = useState<ActionApproval[]>([]);
  const [connection, setConnection] = useState<BackendConnection>(initialConnection);
  const [isLoading, setIsLoading] = useState(true);

  const fetchStaticData = useCallback(async () => {
    try {
      const [healthRes, connRes, dirRes, operatorsRes, provRes] = await Promise.all([
        axios.get(`${API_BASE}/health`),
        axios.get(`${API_BASE}/api/connectors`),
        axios.get(`${API_BASE}/api/connector-directory`),
        axios.get(`${API_BASE}/api/operators`),
        axios.get(`${API_BASE}/api/provider`),
      ]);

      let approvalsData: ActionApproval[] = [];
      let missionsData: Mission[] = [];
      let tracesData: Trace[] = [];

      try {
        const [apprRes, missRes, tracRes] = await Promise.all([
          axios.get(`${API_BASE}/api/approvals`, readConfig),
          axios.get(`${API_BASE}/api/missions`, readConfig),
          axios.get(`${API_BASE}/api/traces`, readConfig),
        ]);
        approvalsData = apprRes.data;
        missionsData = missRes.data;
        tracesData = tracRes.data;
      } catch {
        /* These endpoints require AUTOPILOT_API_KEY when set; dashboard must still refresh public data without it */
      }

      setConnectors(connRes.data);
      setConnectorDirectory(dirRes.data);
      setOperators(operatorsRes.data);
      setApprovals(approvalsData);
      setMissions(missionsData);
      setTraces(tracesData);
      setConnection((current) => ({
        ...current,
        status: current.sseConnected ? "connected" : "degraded",
        provider: provRes.data.provider || healthRes.data.provider || "-",
        runtime: healthRes.data.runtime || "autonomous-operator-runtime",
        message: current.sseConnected ? "Backend and event stream connected" : "Backend reachable; waiting for event stream",
      }));
    } catch (error) {
      console.error("Failed to connect to AUTOPILOT backend:", error);
      setConnection((current) => ({
        ...current,
        status: "offline",
        message: "Backend API is unreachable from the frontend",
        sseConnected: false,
      }));
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    const refresh = window.setTimeout(() => {
      void fetchStaticData();
    }, 0);

    const eventSource = new EventSource(eventStreamUrl);

    eventSource.onopen = () => {
      setConnection((current) => ({
        ...current,
        status: current.status === "offline" ? "degraded" : "connected",
        message: "Backend and event stream connected",
        sseConnected: true,
      }));
    };

    eventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.missions) setMissions(data.missions);
        if (data.traces) setTraces(data.traces);
      } catch (err) {
        console.error("SSE parse error:", err);
        setConnection((current) => ({
          ...current,
          status: "degraded",
          message: "Event stream returned invalid data",
        }));
      }
    };

    eventSource.onerror = () => {
      setConnection((current) => ({
        ...current,
        status: current.status === "checking" ? "offline" : "degraded",
        message: "Backend event stream disconnected",
        sseConnected: false,
      }));
    };

    return () => {
      window.clearTimeout(refresh);
      eventSource.close();
    };
  }, [fetchStaticData]);

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

  const connectConnector = async (connectorId: string) => {
    try {
      await axios.post(`${API_BASE}/api/connector-directory/${connectorId}/connect`, { auth_mode: "demo" }, writeConfig);
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
      await fetchStaticData();
    } catch (error) {
      console.error("Failed to approve action:", error);
      setConnection((current) => ({
        ...current,
        status: "degraded",
        message: "Approval request failed; check API key, connector config, or backend logs",
      }));
      throw error;
    }
  };

  const rejectAction = async (approvalId: string) => {
    try {
      await axios.post(`${API_BASE}/api/approvals/${approvalId}/reject`, { decided_by: "dashboard" }, writeConfig);
      await fetchStaticData();
    } catch (error) {
      console.error("Failed to reject action:", error);
      setConnection((current) => ({
        ...current,
        status: "degraded",
        message: "Approval rejection failed; check API key or backend logs",
      }));
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
    traces,
    connectors,
    connectorDirectory,
    operators,
    operatorProbeResults,
    approvals,
    provider: connection.provider,
    connection,
    isLoading,
    monitorConnectedServices,
    sendSignal,
    connectConnector,
    disconnectConnector,
    approveAction,
    rejectAction,
    probeOperator,
    refresh: fetchStaticData,
  };
}
