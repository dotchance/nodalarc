// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Builder world data — session list + resolve-backed world loading.
 *
 *  The resolver is the only truth for what a session means: this hook loads
 *  the resolved world from POST /api/v1/builder/resolve-world and never
 *  synthesizes a builder-local expansion. Errors are surfaced, not swallowed —
 *  a world that failed to resolve renders as its error, never as stale data.
 */

import { useState, useEffect, useCallback, useRef } from "react";
import { REST_URL, authHeaders } from "../config";
import type {
  BuilderCatalogEntry,
  BuilderResolveCheck,
  BuilderSessionListEntry,
  BuilderWorld,
} from "./builderTypes";

async function _errorMessage(response: Response): Promise<string> {
  try {
    const data = await response.json();
    if (data && typeof data.error === "string") return data.error;
  } catch {
    /* non-JSON error body */
  }
  return `request failed (${response.status})`;
}

/** One catalog family's primitives for the library pickers. */
export function useBuilderCatalog(family: string) {
  const [entries, setEntries] = useState<BuilderCatalogEntry[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch(`${REST_URL}/api/v1/builder/catalog?family=${encodeURIComponent(family)}`, {
      headers: authHeaders(),
    })
      .then(async (r) => {
        if (!r.ok) throw new Error(await _errorMessage(r));
        return r.json();
      })
      .then((data: BuilderCatalogEntry[]) => {
        if (!cancelled) setEntries(data);
      })
      .catch((e: Error) => {
        if (!cancelled) setError(e.message);
      });
    return () => {
      cancelled = true;
    };
  }, [family]);

  return { entries, error };
}

export function useBuilderWorld() {
  const [sessions, setSessions] = useState<BuilderSessionListEntry[]>([]);
  const [sessionsError, setSessionsError] = useState<string | null>(null);
  const [world, setWorld] = useState<BuilderWorld | null>(null);
  const [documentYaml, setDocumentYaml] = useState<string | null>(null);
  const [loadedFile, setLoadedFile] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Monotonic resolve counter: a stale in-flight response must never
  // overwrite a newer edit's result.
  const resolveSeq = useRef(0);

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

  const resolve = useCallback(
    async (input: { session?: string; document?: unknown }, fileLabel: string | null) => {
      const seq = ++resolveSeq.current;
      setLoading(true);
      setError(null);
      try {
        const response = await fetch(`${REST_URL}/api/v1/builder/resolve-world`, {
          method: "POST",
          headers: authHeaders({ "Content-Type": "application/json" }),
          body: JSON.stringify(input),
        });
        if (!response.ok) throw new Error(await _errorMessage(response));
        const data: BuilderResolveCheck = await response.json();
        if (seq !== resolveSeq.current) return;
        setWorld(data.world);
        setDocumentYaml(data.document_yaml);
        setLoadedFile(fileLabel);
      } catch (e) {
        if (seq !== resolveSeq.current) return;
        // An edit that fails resolution keeps nothing stale on screen: the
        // error is the state.
        setWorld(null);
        setDocumentYaml(null);
        setLoadedFile(null);
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (seq === resolveSeq.current) setLoading(false);
      }
    },
    [],
  );

  const loadSession = useCallback(
    (file: string) => resolve({ session: file }, file),
    [resolve],
  );
  const resolveDocument = useCallback(
    (document: unknown) => resolve({ document }, null),
    [resolve],
  );
  /** Drop the current world (e.g. starting a fresh workspace): a stale world
   *  must never render behind a draft that has not resolved yet. */
  const clear = useCallback(() => {
    resolveSeq.current += 1;
    setWorld(null);
    setDocumentYaml(null);
    setLoadedFile(null);
    setError(null);
    setLoading(false);
  }, []);

  return {
    sessions,
    sessionsError,
    world,
    documentYaml,
    loadedFile,
    loading,
    error,
    loadSession,
    resolveDocument,
    clear,
  };
}
