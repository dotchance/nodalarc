/** Hook for running whitelisted vtysh commands via VS-API introspect endpoint. */

import { useState, useEffect, useCallback } from "react";
import { REST_URL } from "../config";

interface UseIntrospectResult {
  loading: boolean;
  output: string | null;
  error: string | null;
  commands: string[];
  execute: (nodeId: string, command: string) => Promise<void>;
}

export function useIntrospect(): UseIntrospectResult {
  const [loading, setLoading] = useState(false);
  const [output, setOutput] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [commands, setCommands] = useState<string[]>([]);

  // Fetch available commands on mount
  useEffect(() => {
    let cancelled = false;
    fetch(`${REST_URL}/api/v1/introspect/commands`)
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then((data) => {
        if (!cancelled && Array.isArray(data)) setCommands(data);
      })
      .catch(() => {
        /* ignore — commands will be empty until VS-API is reachable */
      });
    return () => { cancelled = true; };
  }, []);

  const execute = useCallback(async (nodeId: string, command: string) => {
    setLoading(true);
    setOutput(null);
    setError(null);
    try {
      const res = await fetch(`${REST_URL}/api/v1/introspect`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ node_id: nodeId, command }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.error ?? `HTTP ${res.status}`);
      } else if (data.error) {
        setError(data.error);
        setOutput(data.output || null);
      } else {
        setOutput(data.output);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Network error");
    } finally {
      setLoading(false);
    }
  }, []);

  return { loading, output, error, commands, execute };
}
