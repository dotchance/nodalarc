// Copyright 2024-2026 .chance (dotchance)
// SPDX-License-Identifier: AGPL-3.0-only
/** Hook for running whitelisted vtysh commands via VS-API introspect endpoint. */

import { useState, useEffect, useCallback } from "react";
import type { IntrospectResult } from "../generated/vsApi";
import { REST_URL, authHeaders } from "../config";
import { apiErrorMessage, apiErrorFromException } from "../ui/apiError";

interface UseIntrospectResult {
  loading: boolean;
  output: string | null;
  error: string | null;
  commands: readonly string[];
  execute: (nodeId: string, command: string) => Promise<void>;
}

export function useIntrospect(): UseIntrospectResult {
  const [loading, setLoading] = useState(false);
  const [output, setOutput] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [commands, setCommands] = useState<readonly string[]>([]);

  // Fetch available commands on mount; a failed read is shown where command output goes.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`${REST_URL}/api/v1/introspect/commands`, { headers: authHeaders() });
        if (!res.ok) {
          const message = await apiErrorMessage(res);
          if (!cancelled) setError(`command list unavailable: ${message}`);
          return;
        }
        const data = (await res.json()) as string[];
        if (!cancelled) setCommands(data);
      } catch (err) {
        if (!cancelled) setError(`command list unavailable: ${apiErrorFromException(err)}`);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const execute = useCallback(async (nodeId: string, command: string) => {
    setLoading(true);
    setOutput(null);
    setError(null);
    try {
      const res = await fetch(`${REST_URL}/api/v1/introspect`, {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ node_id: nodeId, command }),
      });
      if (!res.ok) {
        setError(await apiErrorMessage(res));
        return;
      }
      const data = (await res.json()) as IntrospectResult;
      if (data.exit_code !== 0) {
        setError(`vtysh exited ${data.exit_code}: ${data.stderr.trim() || "no error output"}`);
      }
      setOutput(data.output);
    } catch (err) {
      setError(apiErrorFromException(err));
    } finally {
      setLoading(false);
    }
  }, []);

  return { loading, output, error, commands, execute };
}
