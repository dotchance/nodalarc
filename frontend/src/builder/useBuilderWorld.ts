// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Builder world data — session list + resolve-backed world loading.
 *
 *  The resolver is the only truth for what a session means: this hook loads
 *  the resolved world from POST /api/v1/builder/resolve-world and never
 *  synthesizes a builder-local expansion. Errors are surfaced, not swallowed —
 *  a world that failed to resolve renders as its error, never as stale data.
 */

import { useState, useEffect, useCallback, useRef, useSyncExternalStore } from "react";
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

// --- Catalog store: ONE state per family, shared by every consumer. ---
// Pickers, the library panel, and editors must all see the same list; a save
// or delete anywhere refreshes everyone. Private per-hook copies were a
// stale-picker bug class.

interface CatalogFamilyState {
  entries: BuilderCatalogEntry[];
  error: string | null;
}

interface CatalogFamilyStore {
  state: CatalogFamilyState;
  listeners: Set<() => void>;
  fetched: boolean;
}

const _catalogStores = new Map<string, CatalogFamilyStore>();

function _catalogStore(family: string): CatalogFamilyStore {
  let store = _catalogStores.get(family);
  if (!store) {
    store = { state: { entries: [], error: null }, listeners: new Set(), fetched: false };
    _catalogStores.set(family, store);
  }
  return store;
}

/** Re-fetch one family and notify every consumer. Mutation helpers call this
 *  themselves — callers cannot forget. */
export async function refreshCatalogFamily(family: string): Promise<void> {
  const store = _catalogStore(family);
  try {
    const response = await fetch(
      `${REST_URL}/api/v1/builder/catalog?family=${encodeURIComponent(family)}`,
      { headers: authHeaders() },
    );
    if (!response.ok) throw new Error(await _errorMessage(response));
    store.state = { entries: (await response.json()) as BuilderCatalogEntry[], error: null };
  } catch (e) {
    store.state = {
      entries: store.state.entries,
      error: e instanceof Error ? e.message : String(e),
    };
  }
  for (const listener of store.listeners) listener();
}

/** One catalog family's primitives — shared state across all consumers. */
export function useBuilderCatalog(family: string) {
  const store = _catalogStore(family);
  const state = useSyncExternalStore(
    (onChange) => {
      store.listeners.add(onChange);
      return () => store.listeners.delete(onChange);
    },
    () => store.state,
  );
  useEffect(() => {
    if (!store.fetched) {
      store.fetched = true;
      void refreshCatalogFamily(family);
    }
  }, [family, store]);
  return {
    entries: state.entries,
    error: state.error,
    refresh: () => refreshCatalogFamily(family),
  };
}

/** Read one catalog document (authoring-wrapper form). */
export async function readCatalogObject(
  ref: string,
): Promise<{ ref: string; family_wrapper: string; document: Record<string, unknown> }> {
  const response = await fetch(
    `${REST_URL}/api/v1/builder/catalog/object?ref=${encodeURIComponent(ref)}`,
    { headers: authHeaders() },
  );
  if (!response.ok) throw new Error(await _errorMessage(response));
  return response.json();
}

/** Import a primitive YAML file into the user catalog (family derived from
 *  the document's own wrapper; the server owns parsing and validation). */
export async function importUserObjectYaml(
  documentYaml: string,
  options?: { overwrite?: boolean },
): Promise<BuilderCatalogEntry> {
  const response = await fetch(`${REST_URL}/api/v1/builder/catalog/save`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ document_yaml: documentYaml, overwrite: options?.overwrite ?? false }),
  });
  if (!response.ok) {
    const error = new Error(await _errorMessage(response)) as Error & { status?: number };
    error.status = response.status;
    throw error;
  }
  const imported = (await response.json()) as BuilderCatalogEntry;
  void refreshCatalogFamily(imported.family);
  return imported;
}

/** Download one catalog document as a canonical YAML file. */
export async function exportCatalogObject(ref: string): Promise<void> {
  const response = await fetch(
    `${REST_URL}/api/v1/builder/catalog/export?ref=${encodeURIComponent(ref)}`,
    { headers: authHeaders() },
  );
  if (!response.ok) throw new Error(await _errorMessage(response));
  const text = await response.text();
  const blob = new Blob([text], { type: "text/yaml" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `${ref.split("/").pop() ?? "object.yaml"}`;
  anchor.click();
  URL.revokeObjectURL(url);
}

/** Delete one user catalog entry. */
export async function deleteUserObject(ref: string): Promise<void> {
  const response = await fetch(
    `${REST_URL}/api/v1/builder/catalog/object?ref=${encodeURIComponent(ref)}`,
    { method: "DELETE", headers: authHeaders() },
  );
  if (!response.ok) throw new Error(await _errorMessage(response));
  const family = ref.split(":", 2)[1]?.split("/")[0];
  if (family) void refreshCatalogFamily(family);
}

/** Save one primitive document into the user catalog. */
export async function saveUserObject(
  family: string,
  document: Record<string, unknown>,
  options?: { overwrite?: boolean },
): Promise<BuilderCatalogEntry> {
  const response = await fetch(`${REST_URL}/api/v1/builder/catalog/save`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ family, document, overwrite: options?.overwrite ?? false }),
  });
  if (!response.ok) {
    const error = new Error(await _errorMessage(response)) as Error & { status?: number };
    error.status = response.status;
    throw error;
  }
  const saved = (await response.json()) as BuilderCatalogEntry;
  void refreshCatalogFamily(saved.family);
  return saved;
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

  const refreshSessions = useCallback(async () => {
    try {
      const response = await fetch(`${REST_URL}/api/v1/sessions`, { headers: authHeaders() });
      if (!response.ok) throw new Error(await _errorMessage(response));
      setSessions((await response.json()) as BuilderSessionListEntry[]);
      setSessionsError(null);
    } catch (e) {
      setSessionsError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void refreshSessions();
  }, [refreshSessions]);

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
  /** Save the workspace document server-side. The server resolves first and
   *  writes the canonical YAML exclusively; the result names the saved
   *  session. Throws with the server's message on failure. */
  const saveSession = useCallback(
    async (document: unknown): Promise<{ name: string; file: string; nodes: number }> => {
      const response = await fetch(`${REST_URL}/api/v1/builder/save-session`, {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ document }),
      });
      if (!response.ok) throw new Error(await _errorMessage(response));
      const result = await response.json();
      await refreshSessions();
      return result;
    },
    [refreshSessions],
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
    saveSession,
    clear,
  };
}
