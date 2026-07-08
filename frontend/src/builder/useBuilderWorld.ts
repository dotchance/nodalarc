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
import { apiErrorMessage } from "../ui/apiError";
import type {
  BuilderCatalogEntry,
  BuilderResolveCheck,
  BuilderSessionListEntry,
  BuilderResolveError,
  BuilderWorld,
} from "./builderTypes";

class ResolveRefusal extends Error {
  detail: BuilderResolveError;
  constructor(detail: BuilderResolveError) {
    super(detail.error);
    this.detail = detail;
  }
}

async function _structuredError(response: Response): Promise<BuilderResolveError> {
  try {
    const data = await response.json();
    if (data && typeof data.error === "string") {
      // The body is the BuilderResolveRefusal envelope — pass it through;
      // re-mapping fields here would be a second schema.
      return data as BuilderResolveError;
    }
  } catch {
    /* non-JSON error body */
  }
  return { error: `request failed (${response.status})` };
}

// --- Catalog store: one state per family, shared by every consumer. ---
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

/** Test-only: drop the module-global catalog cache so a suite that mounts the
 *  builder across many cases starts each with a fresh fetch (the cache is keyed
 *  by family and otherwise lives for the whole module lifetime). */
export function resetCatalogStores(): void {
  _catalogStores.clear();
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
    if (!response.ok) throw new Error(await apiErrorMessage(response));
    store.state = { entries: (await response.json()) as BuilderCatalogEntry[], error: null };
  } catch (e) {
    store.state = {
      entries: store.state.entries,
      error: e instanceof Error ? e.message : String(e),
    };
  }
  for (const listener of store.listeners) listener();
}

// --- Save-reveal: a save is never a dead end. -------------------------
// Every save-to-library announces its result here; the Library surface
// subscribes and lands on the asset (family tab, visible filter, highlight),
// and the view opens the Library window. One mechanism at the one choke
// point every family's save flows through — no per-editor wiring to forget.

interface LibraryReveal {
  entry: BuilderCatalogEntry;
  nonce: number;
}

let _revealState: LibraryReveal | null = null;
const _revealListeners = new Set<() => void>();
let _revealNonce = 0;

export function requestLibraryReveal(entry: BuilderCatalogEntry): void {
  _revealNonce += 1;
  _revealState = { entry, nonce: _revealNonce };
  for (const listener of _revealListeners) listener();
}

/** The latest saved asset to land on (null until the first save). */
export function useLibraryReveal(): LibraryReveal | null {
  return useSyncExternalStore(
    (onChange) => {
      _revealListeners.add(onChange);
      return () => _revealListeners.delete(onChange);
    },
    () => _revealState,
  );
}

// Retired nonces live at module level, one slot per consumer role: the
// opener (BuilderView) and the lander (LibraryPanel) each handle a reveal
// once across remounts. Per-mount refs replayed the last reveal on every
// remount; the state is never cleared, so a consumer that mounts late with
// an unseen nonce still fires.
const _retiredRevealNonces = new Map<string, number>();

/** Claim a reveal for one consumer role — returns it once, null ever after. */
export function claimLibraryReveal(
  role: "opener" | "lander",
  reveal: LibraryReveal | null,
): LibraryReveal | null {
  if (!reveal) return null;
  if ((_retiredRevealNonces.get(role) ?? 0) >= reveal.nonce) return null;
  _retiredRevealNonces.set(role, reveal.nonce);
  return reveal;
}

// --- Outline reveal: a SEPARATE channel from the Library reveal. ---------
// A library reveal means "a saved/imported asset — open and land the Library on
// it." An outline reveal means "a Use gesture placed a ref in the session — show
// where it landed in the session anatomy." Different destinations, different
// consumers, different intents. They share the consume-once nonce PATTERN but
// nothing else: a placement never opens the Library, and a save never scrolls
// the outline. Keyed on the placed row's stable key (segment_id).
interface OutlineReveal {
  segmentId: string;
  nonce: number;
}

let _outlineRevealState: OutlineReveal | null = null;
const _outlineRevealListeners = new Set<() => void>();
let _outlineRevealNonce = 0;

/** Reveal a just-placed segment's row in the outline (IG-1 ref floor: a placed
 *  reference has no editor, so its Use scrolls its row into view and flashes
 *  it). */
export function requestOutlineReveal(segmentId: string): void {
  _outlineRevealNonce += 1;
  _outlineRevealState = { segmentId, nonce: _outlineRevealNonce };
  for (const listener of _outlineRevealListeners) listener();
}

export function useOutlineReveal(): OutlineReveal | null {
  return useSyncExternalStore(
    (onChange) => {
      _outlineRevealListeners.add(onChange);
      return () => _outlineRevealListeners.delete(onChange);
    },
    () => _outlineRevealState,
  );
}

const _retiredOutlineNonces = new Map<string, number>();

/** Claim the outline reveal for one consumer role — returns it once, null ever
 *  after (a remount never replays a stale placement). Its retired-nonce map is
 *  separate from the Library channel's, so a newer reveal on one channel never
 *  retires or consumes the other. */
export function claimOutlineReveal(
  role: "outline",
  reveal: OutlineReveal | null,
): OutlineReveal | null {
  if (!reveal) return null;
  if ((_retiredOutlineNonces.get(role) ?? 0) >= reveal.nonce) return null;
  _retiredOutlineNonces.set(role, reveal.nonce);
  return reveal;
}

// --- Library revision: bumps on every user-catalog mutation. -----------
// The workspace does not change when a library object does, but the
// hypothetical save artifact can (references are dereferenced server-side)
// — the resolve loop depends on this revision so the deploy gate tracks
// library drift, including deletion (which resolves to a refusal).

let _libraryRevision = 0;
const _libraryRevisionListeners = new Set<() => void>();

function _bumpLibraryRevision(): void {
  _libraryRevision += 1;
  for (const listener of _libraryRevisionListeners) listener();
}

export function useLibraryRevision(): number {
  return useSyncExternalStore(
    (onChange) => {
      _libraryRevisionListeners.add(onChange);
      return () => _libraryRevisionListeners.delete(onChange);
    },
    () => _libraryRevision,
  );
}

/** The deploy gate, as a pure truth table. Deploy ships a saved FILE, so it
 *  requires: a saved artifact, a SETTLED resolve of the current document
 *  (explicit state — null after clear() or a refusal, never inferred from
 *  !loading), no unapplied window edits, and the saved artifact matching
 *  what saving the current document would write. Fail closed: any missing
 *  fact disables with its reason. */
export function canDeploy(input: {
  savedFile: string | null;
  savedArtifactSha256: string | null;
  settledArtifactSha256: string | null;
  dirtyWindowCount: number;
  deployReady: boolean;
  deployBlockers: readonly string[];
}): { ok: boolean; reason: string | null } {
  if (!input.savedFile || !input.savedArtifactSha256) {
    return { ok: false, reason: "save the session first, then deploy" };
  }
  if (input.settledArtifactSha256 === null) {
    return { ok: false, reason: "the session must resolve before deploy" };
  }
  if (!input.deployReady) {
    // Q3: a grammar-valid, saved, settled session may still be unable to start
    // on the cluster (no satellites, an unrunnable rule). Disable Deploy with
    // the server's reason; Save and library actions stay enabled (Q1).
    return {
      ok: false,
      reason: input.deployBlockers[0] ?? "the session cannot start on the cluster yet",
    };
  }
  if (input.dirtyWindowCount > 0) {
    return {
      ok: false,
      reason: `apply or discard the ${input.dirtyWindowCount} ${
        input.dirtyWindowCount === 1 ? "window" : "windows"
      } with unapplied edits first`,
    };
  }
  if (input.savedArtifactSha256 !== input.settledArtifactSha256) {
    return { ok: false, reason: "saved copy is behind your edits — save again" };
  }
  return { ok: true, reason: null };
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
  if (!response.ok) throw new Error(await apiErrorMessage(response));
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
    const error = new Error(await apiErrorMessage(response)) as Error & { status?: number };
    error.status = response.status;
    throw error;
  }
  const imported = (await response.json()) as BuilderCatalogEntry;
  void refreshCatalogFamily(imported.family);
  requestLibraryReveal(imported);
  _bumpLibraryRevision();
  return imported;
}

/** Download one catalog document as a canonical YAML file. */
export async function exportCatalogObject(ref: string): Promise<void> {
  const response = await fetch(
    `${REST_URL}/api/v1/builder/catalog/export?ref=${encodeURIComponent(ref)}`,
    { headers: authHeaders() },
  );
  if (!response.ok) throw new Error(await apiErrorMessage(response));
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
  if (!response.ok) throw new Error(await apiErrorMessage(response));
  const family = ref.split(":", 2)[1]?.split("/")[0];
  if (family) void refreshCatalogFamily(family);
  _bumpLibraryRevision();
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
    const error = new Error(await apiErrorMessage(response)) as Error & { status?: number };
    error.status = response.status;
    throw error;
  }
  const saved = (await response.json()) as BuilderCatalogEntry;
  void refreshCatalogFamily(saved.family);
  requestLibraryReveal(saved);
  _bumpLibraryRevision();
  return saved;
}

export function useBuilderWorld() {
  const [sessions, setSessions] = useState<BuilderSessionListEntry[]>([]);
  const [sessionsError, setSessionsError] = useState<string | null>(null);
  const [world, setWorld] = useState<BuilderWorld | null>(null);
  const [documentYaml, setDocumentYaml] = useState<string | null>(null);
  // The resolved session as a parsed mapping — what the workspace importer
  // consumes when editing an existing (e.g. the running) session.
  const [loadedDocument, setLoadedDocument] = useState<Record<string, unknown> | null>(null);
  const [loadedFile, setLoadedFile] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  // Structured refusal from the resolver — `error` (the display string)
  // derives from it; the structure routes the wall to its owning object.
  const [resolveError, setResolveError] = useState<BuilderResolveError | null>(null);
  // The settled artifact hash: what saving the CURRENT resolved document
  // would write. Explicit state, never inferred from !loading — set only
  // when a resolve completes, nulled by clear() and every refusal. The
  // deploy gate fails closed on null.
  const [settledArtifactSha256, setSettledArtifactSha256] = useState<string | null>(null);
  // Deploy-readiness (Q3) from the last successful resolve: whether the
  // session can start on the cluster, and why not. Reset by clear() and every
  // refusal — the deploy gate fails closed on the false default.
  const [deployReady, setDeployReady] = useState(false);
  const [deployBlockers, setDeployBlockers] = useState<string[]>([]);
  // Monotonic resolve counter: a stale in-flight response must never
  // overwrite a newer edit's result.
  const resolveSeq = useRef(0);

  const refreshSessions = useCallback(async () => {
    try {
      const response = await fetch(`${REST_URL}/api/v1/sessions`, { headers: authHeaders() });
      if (!response.ok) throw new Error(await apiErrorMessage(response));
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
      setResolveError(null);
      try {
        const response = await fetch(`${REST_URL}/api/v1/builder/resolve-world`, {
          method: "POST",
          headers: authHeaders({ "Content-Type": "application/json" }),
          body: JSON.stringify(input),
        });
        if (!response.ok) {
          throw new ResolveRefusal(await _structuredError(response));
        }
        const data: BuilderResolveCheck = await response.json();
        if (seq !== resolveSeq.current) return;
        setWorld(data.world);
        setDocumentYaml(data.document_yaml);
        setLoadedDocument(data.document);
        setLoadedFile(fileLabel);
        setSettledArtifactSha256(data.artifact_sha256);
        setDeployReady(data.deploy_ready);
        setDeployBlockers(data.deploy_blockers);
      } catch (e) {
        if (seq !== resolveSeq.current) return;
        // An edit that fails resolution keeps nothing stale on screen: the
        // error is the state.
        setWorld(null);
        setDocumentYaml(null);
        setLoadedDocument(null);
        setLoadedFile(null);
        setSettledArtifactSha256(null);
        setDeployReady(false);
        setDeployBlockers([]);
        setResolveError(
          e instanceof ResolveRefusal
            ? e.detail
            : { error: e instanceof Error ? e.message : String(e) },
        );
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
  /** Deploy a saved session file to the cluster — the same switch the app's
   *  session picker uses; the builder adds nothing to the path. */
  const deploySession = useCallback(
    async (file: string): Promise<void> => {
      const response = await fetch(`${REST_URL}/api/v1/sessions/switch`, {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ session: file }),
      });
      if (!response.ok) throw new Error(await apiErrorMessage(response));
      // The active flag is changing hands — refresh so displays that read
      // it (running-session entry, provenance) track the switch.
      void refreshSessions();
    },
    [refreshSessions],
  );

  const saveSession = useCallback(
    async (
      document: unknown,
    ): Promise<{ name: string; file: string; nodes: number; artifact_sha256: string }> => {
      const response = await fetch(`${REST_URL}/api/v1/builder/save-session`, {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ document }),
      });
      if (!response.ok) throw new Error(await apiErrorMessage(response));
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
    setLoadedDocument(null);
    setLoadedFile(null);
    setResolveError(null);
    setSettledArtifactSha256(null);
    setDeployReady(false);
    setDeployBlockers([]);
    setLoading(false);
  }, []);

  return {
    sessions,
    sessionsError,
    world,
    documentYaml,
    loadedDocument,
    loadedFile,
    loading,
    error: resolveError?.error ?? null,
    resolveError,
    settledArtifactSha256,
    deployReady,
    deployBlockers,
    loadSession,
    resolveDocument,
    saveSession,
    deploySession,
    // Exposed so the Open picker can refetch on open: the running chip and
    // auto-import target must not claim a session the cluster switched away
    // from (N15). The mount effect already fetches once.
    refreshSessions,
    clear,
  };
}
