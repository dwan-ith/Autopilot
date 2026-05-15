import { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import { Mission, Trace, Connector } from '@/types';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8090';

export function useAutopilot() {
  const [missions, setMissions] = useState<Mission[]>([]);
  const [traces, setTraces] = useState<Trace[]>([]);
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [provider, setProvider] = useState<string>('—');
  const [isLoading, setIsLoading] = useState(true);

  const fetchStaticData = useCallback(async () => {
    try {
      const [connRes, provRes] = await Promise.all([
        axios.get(`${API_BASE}/api/connectors`),
        axios.get(`${API_BASE}/api/provider`)
      ]);
      setConnectors(connRes.data);
      setProvider(provRes.data.provider);
    } catch (error) {
      console.error('Failed to fetch static data:', error);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchStaticData();

    const eventSource = new EventSource(`${API_BASE}/api/events`);
    
    eventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.missions) setMissions(data.missions);
        if (data.traces) setTraces(data.traces);
      } catch (err) {
        console.error('SSE parse error:', err);
      }
    };

    eventSource.onerror = () => {
      console.warn('SSE connection lost, retrying...');
    };

    return () => eventSource.close();
  }, [fetchStaticData]);

  const runDemo = async () => {
    try {
      await axios.post(`${API_BASE}/demo/fire`);
    } catch (error) {
      console.error('Failed to run demo:', error);
    }
  };

  return { missions, traces, connectors, provider, isLoading, runDemo };
}
