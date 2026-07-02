// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Builder world data — session list + resolve-backed world loading.
 *
 *  The resolver is the only truth for what a session means: this hook loads
 *  the resolved world from POST /api/v1/builder/resolve-world and never
 *  synthesizes a builder-local expansion. Errors are surfaced, not swallowed —
 *  a world that failed to resolve renders as its error, never as stale data.
 */

import { useState, useEffect, useCallback } from "react";
import { REST_URL, authHeaders } from "../config";
import type { BuilderWorld, BuilderSessionListEntry } from "./builderTypes";

async function _errorMessage(response: Response): Promise<string> {
  try {
    const data = await response.json();
    if (data && typeof data.error === "string") return data.error;
  } catch {
    /* non-JSON error body */
  }
  return `request failed (${response.status})`;
}

export function useBuilderWorld() {
  const [sessions, setSessions] = useState<BuilderSessionListEntry[]>([]);
  const [sessionsError, setSessionsError] = useState<string | null>(null);
  const [world, setWorld] = useState<BuilderWorld | null>(null);
  const [loadedFile, setLoadedFile] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch(`${REST_URL}/api/v1/sessions`, { headers: authHeaders() })
      .then(async (r) => {
        if (!r.ok) throw new Error(await _errorMessage(r));
        return r.json();
      })
      .then((data: BuilderSessionListEntry[]) => {
        if (!cancelled) setSessions(data);
      })
      .catch((e: Error) => {
        if (!cancelled) setSessionsError(e.message);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const loadSession = useCallback(async (file: string) => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(`${REST_URL}/api/v1/builder/resolve-world`, {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ session: file }),
      });
      if (!response.ok) throw new Error(await _errorMessage(response));
      const data: BuilderWorld = await response.json();
      setWorld(data);
      setLoadedFile(file);
    } catch (e) {
      setWorld(null);
      setLoadedFile(null);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  return { sessions, sessionsError, world, loadedFile, loading, error, loadSession };
}
