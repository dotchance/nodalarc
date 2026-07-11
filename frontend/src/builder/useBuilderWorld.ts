// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Builder world data and typed authoring API state. */

import { useState, useEffect, useCallback, useRef, useSyncExternalStore } from "react";
import { downloadBlob } from "../ui/downloadBlob";
import {
  applyVisualDraftCommand,
  compileVisualDraft,
  createVisualDraft,
  customizeVisualDraftChain,
  deleteCatalogDocument,
  deployBuilderSession,
  exportCatalogSession,
  getBuilderBootstrap,
  getCatalogDependents,
  getCatalogDocument,
  importCatalogSession,
  listCatalog,
  openVisualDraft,
  saveBuilderSession,
} from "./builderApiClient";
import type { BuilderResolveError } from "./builderTypes";
import type {
  BuilderCatalogBootstrap,
  BuilderCompileResult,
  BuilderDeployVerdict,
  BuilderIssue,
  BuilderWorld,
  BuilderSessionSaveRequest,
  BuilderSessionSaveResult,
  BuilderSessionDeployAccepted,
  BuilderSessionDeployRequest,
  BuilderVisualCustomizeChainRequest,
  BuilderVisualCustomizeChainResult,
  BuilderVisualCatalogRevision,
  BuilderVisualDraftAssemblyResult,
  BuilderVisualDraftCommandRequest,
  BuilderVisualDraftCommandResult,
  BuilderVisualDraftCreateRequest,
  BuilderVisualDraftEnvelope,
  CatalogDocumentSummary,
  CatalogClosureImportRequest,
  CatalogFamily,
  CatalogImportResult,
  CatalogSessionExport,
  CatalogDraftSaveResult,
  SessionRef,
} from "./generated/builderApi";

const OPAQUE_DRAFT_AUTOSAVE_KEY = "nodalarc-builder-opaque-yaml-draft";
const OPAQUE_DRAFT_AUTOSAVE_VERSION = 1;
const OPAQUE_DRAFT_AUTOSAVE_DEBOUNCE_MS = 800;

export type OpaqueDraftRestoreResult =
  | { ok: true }
  | { ok: false; reason: string };

function _readOpaqueDraft(raw: string): BuilderVisualDraftEnvelope | null {
  try {
    const parsed = JSON.parse(raw) as {
      v?: unknown;
      draft?: Record<string, unknown>;
    };
    const draft = parsed.draft;
    if (
      parsed.v !== OPAQUE_DRAFT_AUTOSAVE_VERSION ||
      !draft ||
      draft.mode !== "opaque_yaml" ||
      typeof draft.draft_revision !== "number" ||
      typeof draft.target_ref !== "string" ||
      !draft.target_ref.startsWith("user:sessions/") ||
      typeof draft.session_yaml !== "string" ||
      draft.workspace != null
    ) {
      return null;
    }
    return draft as unknown as BuilderVisualDraftEnvelope;
  } catch {
    return null;
  }
}

function _recoveredDraftSummary(draft: BuilderVisualDraftEnvelope): CatalogDocumentSummary {
  const ref = draft.source_ref ?? draft.target_ref;
  const displayName = ref.split("/").pop()?.replace(/\.ya?ml$/, "") ?? "YAML draft";
  const serialized = draft.session_yaml ?? JSON.stringify(draft.workspace ?? {});
  return {
    ref,
    family: "sessions",
    namespace: ref.startsWith("user:") ? "user" : "nodalarc",
    revision: draft.expected_session_revision ?? "browser-draft",
    size_bytes: new TextEncoder().encode(serialized).byteLength,
    display_name: displayName,
    summary: "restored browser draft",
  };
}

// --- Catalog store: one state per family, shared by every consumer. ---
// Pickers, the library panel, and editors must all see the same list; a save
// or delete anywhere refreshes everyone. Private per-hook copies were a
// stale-picker bug class.

interface CatalogFamilyState {
  entries: CatalogDocumentSummary[];
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
export async function refreshCatalogFamily(family: string): Promise<CatalogDocumentSummary[]> {
  const store = _catalogStore(family);
  try {
    const entries: CatalogDocumentSummary[] = [];
    let pageToken: string | undefined;
    do {
      const page = await listCatalog({
        family: family as CatalogFamily,
        page_size: 100,
        ...(pageToken ? { page_token: pageToken } : {}),
      });
      entries.push(...page.items);
      pageToken = page.next_page_token ?? undefined;
    } while (pageToken);
    store.state = { entries, error: null };
  } catch (e) {
    store.state = {
      entries: store.state.entries,
      error: e instanceof Error ? e.message : String(e),
    };
  }
  for (const listener of store.listeners) listener();
  return store.state.entries;
}

// --- Save-reveal: a save is never a dead end. -------------------------
// Every save-to-library announces its result here; the Library surface
// subscribes and lands on the asset (family tab, visible filter, highlight),
// and the view opens the Library window. One mechanism at the one choke
// point every family's save flows through — no per-editor wiring to forget.

interface LibraryReveal {
  entry: CatalogDocumentSummary;
  nonce: number;
}

let _revealState: LibraryReveal | null = null;
const _revealListeners = new Set<() => void>();
let _revealNonce = 0;

export function requestLibraryReveal(entry: CatalogDocumentSummary): void {
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

/** Reveal a just-placed segment's row in the outline (ref floor: a placed
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
// dependency closure can change. Compile depends on this revision so the
// deploy gate tracks library drift, including deletion.

let _libraryRevision = 0;
const _libraryRevisionListeners = new Set<() => void>();

function _bumpLibraryRevision(): void {
  _libraryRevision += 1;
  for (const listener of _libraryRevisionListeners) listener();
}

/** Adopt a backend catalog-draft save into every shared Library surface. */
export async function announceCatalogDraftSaved(
  result: CatalogDraftSaveResult,
): Promise<void> {
  const entries = await refreshCatalogFamily(result.draft.family);
  const saved = entries.find((entry) => entry.ref === result.result.document.ref);
  if (saved) requestLibraryReveal(saved);
  _bumpLibraryRevision();
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

export function useBuilderBootstrap() {
  const [bootstrap, setBootstrap] = useState<BuilderCatalogBootstrap | null>(null);
  const [error, setError] = useState<string | null>(null);
  const refresh = useCallback(async () => {
    try {
      const result = await getBuilderBootstrap();
      if (
        result.capabilities.user_catalog_write !== true ||
        result.capabilities.deploy_yaml_closure !== true
      ) {
        throw new Error("Builder backend capabilities are incomplete");
      }
      setBootstrap(result);
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, []);
  useEffect(() => {
    void refresh();
  }, [refresh]);
  return { bootstrap, error, refresh };
}

/** Fail-closed deploy gate bound to one backend-issued saved revision verdict. */
export function canDeploy(input: {
  savedVerdict: BuilderDeployVerdict | null;
  settledDocumentDigest: string | null;
  settledDependencyDigest: string | null;
  dirtyWindowCount: number;
}): { ok: boolean; reason: string | null } {
  if (!input.savedVerdict) {
    return { ok: false, reason: "save the session first, then deploy" };
  }
  if (!input.savedVerdict.allowed) {
    return {
      ok: false,
      reason:
        input.savedVerdict.blockers?.[0]?.message ??
        "the saved session cannot start on the cluster",
    };
  }
  if (input.settledDocumentDigest === null || input.settledDependencyDigest === null) {
    return { ok: false, reason: "the session must compile before deploy" };
  }
  if (input.dirtyWindowCount > 0) {
    return {
      ok: false,
      reason: `apply or discard the ${input.dirtyWindowCount} ${
        input.dirtyWindowCount === 1 ? "window" : "windows"
      } with unapplied edits first`,
    };
  }
  if (
    input.savedVerdict.digests.document !== input.settledDocumentDigest ||
    input.savedVerdict.digests.dependency !== input.settledDependencyDigest
  ) {
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
    refresh: async () => {
      await refreshCatalogFamily(family);
    },
  };
}

/** Read one catalog document (authoring-wrapper form). */
export async function readCatalogObject(
  ref: string,
): Promise<{ ref: string; document: Record<string, unknown> }> {
  const result = await getCatalogDocument({ ref });
  return {
    ref: result.ref,
    document: result.canonical_json as unknown as Record<string, unknown>,
  };
}

/** Download one catalog document as a canonical YAML file. */
export async function exportCatalogObject(ref: string): Promise<void> {
  const document = await getCatalogDocument({ ref });
  downloadBlob(document.canonical_yaml, ref.split("/").pop() ?? "object.yaml");
}

/** Export a session and its exact reference closure as the backend's portable
 *  transfer envelope. The YAML members remain the configuration artifacts;
 *  this JSON is only a browser-friendly carrier for those exact bytes. */
export async function exportSessionClosure(entry: CatalogDocumentSummary): Promise<void> {
  const closureExport = await exportCatalogSession({
    session_ref: entry.ref as SessionRef,
    expected_session_revision: entry.revision,
  });
  const filename = `${entry.ref.split("/").pop()?.replace(/\.ya?ml$/, "") ?? "session"}.nodalarc-session-closure.json`;
  downloadBlob(JSON.stringify(closureExport, null, 2), filename, "application/json");
}

function _closureImportRequest(
  portableClosure: unknown,
  commit: boolean,
): CatalogClosureImportRequest {
  if (!portableClosure || typeof portableClosure !== "object" || Array.isArray(portableClosure)) {
    throw new Error("session transfer file must contain one JSON object");
  }
  const value = portableClosure as Partial<CatalogSessionExport>;
  if (
    value.contract_version !== 1 ||
    typeof value.session_ref !== "string" ||
    typeof value.document_digest !== "string" ||
    typeof value.closure_digest !== "string" ||
    !value.root ||
    typeof value.root.exact_yaml !== "string" ||
    !Array.isArray(value.entries)
  ) {
    throw new Error("session transfer file is missing its typed closure fields");
  }
  return {
    contract_version: 1,
    root_ref: value.session_ref as SessionRef,
    root_yaml: value.root.exact_yaml,
    document_digest: value.document_digest,
    closure_digest: value.closure_digest,
    entries: value.entries.map((entry) => {
      if (
        !entry ||
        typeof entry.ref !== "string" ||
        typeof entry.exact_yaml !== "string" ||
        typeof entry.document_digest !== "string"
      ) {
        throw new Error("session transfer file contains an invalid closure entry");
      }
      return {
        ref: entry.ref,
        exact_yaml: entry.exact_yaml,
        document_digest: entry.document_digest,
      };
    }),
    commit,
  };
}

/** Ask the backend to validate or atomically commit one exact closure. The
 *  browser checks only the transport shape; YAML grammar, identity, graph, and
 *  collision authority remain entirely on the server. */
export async function importSessionClosure(
  portableClosure: unknown,
  commit: boolean,
): Promise<CatalogImportResult> {
  const result = await importCatalogSession(_closureImportRequest(portableClosure, commit));
  if (result.outcome === "committed") {
    const families = new Set(result.proposed_writes.map((entry) => entry.family));
    for (const family of families) await refreshCatalogFamily(family);
    _bumpLibraryRevision();
  }
  return result;
}

/** Delete one user catalog entry. */
export async function deleteUserObject(ref: string): Promise<void> {
  const impact = await getCatalogDependents({ ref });
  if (!impact.delete_allowed) {
    throw new Error(
      `${ref} is used by ${impact.transitive_dependents.length} catalog document${
        impact.transitive_dependents.length === 1 ? "" : "s"
      }`,
    );
  }
  await deleteCatalogDocument({
    ref,
    expected_revision: impact.target_revision,
    impact_acknowledgement: impact.acknowledgement,
  });
  const family = ref.split(":", 2)[1]?.split("/")[0];
  if (family) void refreshCatalogFamily(family);
  _bumpLibraryRevision();
}

export function useBuilderWorld() {
  const [sessions, setSessions] = useState<CatalogDocumentSummary[]>([]);
  const [sessionsError, setSessionsError] = useState<string | null>(null);
  const [openedSession, setOpenedSession] = useState<CatalogDocumentSummary | null>(null);
  const [visualDraft, setVisualDraftState] = useState<BuilderVisualDraftEnvelope | null>(null);
  const visualDraftRef = useRef<BuilderVisualDraftEnvelope | null>(null);
  const opaqueHistory = useRef<BuilderVisualDraftEnvelope[]>([]);
  const [assemblyResult, setAssemblyResult] = useState<BuilderVisualDraftAssemblyResult | null>(
    null,
  );
  const [world, setWorld] = useState<BuilderWorld | null>(null);
  const [documentYaml, setDocumentYaml] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [resolveError, setResolveError] = useState<BuilderResolveError | null>(null);
  const [compileResult, setCompileResult] = useState<BuilderCompileResult | null>(null);
  const [compileIssues, setCompileIssues] = useState<readonly BuilderIssue[]>([]);
  const [settledDocumentDigest, setSettledDocumentDigest] = useState<string | null>(null);
  const [settledDependencySha256, setSettledDependencySha256] = useState<string | null>(null);
  const resolveSeq = useRef(0);

  const refreshSessions = useCallback(async () => {
    try {
      const entries: CatalogDocumentSummary[] = [];
      let pageToken: string | undefined;
      do {
        const page = await listCatalog({
          family: "sessions",
          page_size: 100,
          ...(pageToken ? { page_token: pageToken } : {}),
        });
        entries.push(...page.items);
        pageToken = page.next_page_token ?? undefined;
      } while (pageToken);
      setSessions(entries);
      setSessionsError(null);
    } catch (e) {
      setSessionsError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void refreshSessions();
  }, [refreshSessions]);

  const setVisualDraft = useCallback((draft: BuilderVisualDraftEnvelope | null) => {
    visualDraftRef.current = draft;
    setVisualDraftState(draft);
  }, []);

  const currentVisualDraft = useCallback(() => visualDraftRef.current, []);

  useEffect(() => {
    if (visualDraft?.mode !== "opaque_yaml") return;
    const timer = setTimeout(() => {
      try {
        localStorage.setItem(
          OPAQUE_DRAFT_AUTOSAVE_KEY,
          JSON.stringify({ v: OPAQUE_DRAFT_AUTOSAVE_VERSION, draft: visualDraft }),
        );
      } catch {
        // Storage quota and privacy-mode failures must never break editing.
      }
    }, OPAQUE_DRAFT_AUTOSAVE_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [visualDraft]);

  const hasOpaqueAutosave = useCallback((): boolean => {
    try {
      const raw = localStorage.getItem(OPAQUE_DRAFT_AUTOSAVE_KEY);
      return raw !== null && _readOpaqueDraft(raw) !== null;
    } catch {
      return false;
    }
  }, []);

  const stashOpaqueDraft = useCallback((): void => {
    const current = visualDraftRef.current;
    if (current?.mode !== "opaque_yaml") return;
    try {
      localStorage.setItem(
        OPAQUE_DRAFT_AUTOSAVE_KEY,
        JSON.stringify({ v: OPAQUE_DRAFT_AUTOSAVE_VERSION, draft: current }),
      );
    } catch {
      // The displacing gesture remains usable when browser storage is unavailable.
    }
  }, []);

  const restoreOpaqueAutosave = useCallback((): OpaqueDraftRestoreResult => {
    let raw: string | null = null;
    try {
      raw = localStorage.getItem(OPAQUE_DRAFT_AUTOSAVE_KEY);
    } catch {
      return { ok: false, reason: "browser storage is unavailable" };
    }
    if (raw === null) return { ok: false, reason: "there is no YAML draft to restore" };
    const draft = _readOpaqueDraft(raw);
    if (!draft) return { ok: false, reason: "the saved YAML draft could not be read" };
    resolveSeq.current += 1;
    opaqueHistory.current = [];
    setOpenedSession(_recoveredDraftSummary(draft));
    setVisualDraft(draft);
    setAssemblyResult(null);
    setCompileResult(null);
    setCompileIssues([]);
    setWorld(null);
    setDocumentYaml(null);
    setResolveError(null);
    setSettledDocumentDigest(null);
    setSettledDependencySha256(null);
    setLoading(false);
    return { ok: true };
  }, [setVisualDraft]);

  const adoptRecoveredStructuredDraft = useCallback((draft: BuilderVisualDraftEnvelope) => {
    if (draft.mode !== "structured") {
      throw new Error("structured recovery requires a structured visual draft");
    }
    resolveSeq.current += 1;
    opaqueHistory.current = [];
    setOpenedSession(draft.source_ref ? _recoveredDraftSummary(draft) : null);
    setVisualDraft(draft);
    setAssemblyResult(null);
    setCompileResult(null);
    setCompileIssues([]);
    setWorld(null);
    setDocumentYaml(null);
    setResolveError(null);
    setSettledDocumentDigest(null);
    setSettledDependencySha256(null);
    setLoading(false);
  }, [setVisualDraft]);

  const adoptCompileResult = useCallback((result: BuilderVisualDraftAssemblyResult) => {
    const compile = result.compile_result;
    const issues = compile.issues ?? [];
    const firstError = issues.find((issue) => issue.severity === "error");
    const preview = compile.resolved_preview;
    setVisualDraft(result.visual_draft);
    setAssemblyResult(result);
    setCompileResult(compile);
    setCompileIssues(issues);
    setDocumentYaml(compile.canonical_session_yaml ?? null);
    setWorld(preview ?? null);
    setSettledDocumentDigest(compile.digests?.document ?? null);
    setSettledDependencySha256(compile.digests?.dependency ?? null);
    setResolveError(firstError ? { error: firstError.message } : null);
  }, [setVisualDraft]);

  const compileDraft = useCallback(async (
    draft: BuilderVisualDraftEnvelope,
  ): Promise<BuilderVisualDraftAssemblyResult | null> => {
    const seq = ++resolveSeq.current;
    setLoading(true);
    setResolveError(null);
    setAssemblyResult(null);
    setSettledDocumentDigest(null);
    setSettledDependencySha256(null);
    try {
      const result = await compileVisualDraft({ draft });
      if (seq !== resolveSeq.current) return null;
      adoptCompileResult(result);
      return result;
    } catch (error) {
      if (seq !== resolveSeq.current) return null;
      setWorld(null);
      setDocumentYaml(null);
      setAssemblyResult(null);
      setCompileResult(null);
      setCompileIssues([]);
      setResolveError({ error: error instanceof Error ? error.message : String(error) });
      return null;
    } finally {
      if (seq === resolveSeq.current) setLoading(false);
    }
  }, [adoptCompileResult]);

  const createDraft = useCallback(
    async (request: BuilderVisualDraftCreateRequest): Promise<BuilderVisualDraftEnvelope> => {
      const seq = ++resolveSeq.current;
      setLoading(true);
      try {
        const draft = await createVisualDraft(request);
        if (seq !== resolveSeq.current) return draft;
        opaqueHistory.current = [];
        setOpenedSession(null);
        setVisualDraft(draft);
        setAssemblyResult(null);
        setCompileResult(null);
        setCompileIssues([]);
        setWorld(null);
        setDocumentYaml(null);
        setResolveError(null);
        return draft;
      } finally {
        if (seq === resolveSeq.current) setLoading(false);
      }
    },
    [setVisualDraft],
  );

  const retargetDraft = useCallback(
    async (sessionName: string): Promise<BuilderVisualDraftEnvelope> => {
      const current = visualDraftRef.current;
      if (!current || current.mode !== "structured") {
        throw new Error("only structured visual drafts can be retargeted");
      }
      const seq = ++resolveSeq.current;
      const fresh = await createVisualDraft({ session_name: sessionName });
      const retargeted: BuilderVisualDraftEnvelope = {
        ...fresh,
        draft_revision: current.draft_revision + 1,
        catalog_documents: current.catalog_documents,
        expected_catalog_revisions: current.expected_catalog_revisions,
      };
      if (seq === resolveSeq.current) {
        setOpenedSession(null);
        setVisualDraft(retargeted);
        setAssemblyResult(null);
      }
      return retargeted;
    },
    [setVisualDraft],
  );

  const openSession = useCallback(
    async (
      entry: CatalogDocumentSummary,
      targetRef?: SessionRef,
    ): Promise<{ ok: true } | { ok: false; reason: string }> => {
      const seq = ++resolveSeq.current;
      setLoading(true);
      try {
        const draft = await openVisualDraft({
          source_ref: entry.ref as SessionRef,
          ...(targetRef ? { target_ref: targetRef } : {}),
        });
        if (seq !== resolveSeq.current) return { ok: false, reason: "session open was superseded" };
        opaqueHistory.current = [];
        setOpenedSession(entry);
        setVisualDraft(draft);
        setAssemblyResult(null);
        setWorld(null);
        setDocumentYaml(null);
        setResolveError(null);
        setCompileResult(null);
        setCompileIssues([]);
        setSettledDocumentDigest(null);
        setSettledDependencySha256(null);
        return { ok: true };
      } catch (cause) {
        return {
          ok: false,
          reason: cause instanceof Error ? cause.message : String(cause),
        };
      } finally {
        if (seq === resolveSeq.current) setLoading(false);
      }
    },
    [setVisualDraft],
  );

  const editOpaqueYaml = useCallback((sessionYaml: string) => {
    const current = visualDraftRef.current;
    if (!current || current.mode !== "opaque_yaml") return;
    opaqueHistory.current.push(current);
    if (opaqueHistory.current.length > 100) opaqueHistory.current.shift();
    setVisualDraft({
      ...current,
      draft_revision: current.draft_revision + 1,
      session_yaml: sessionYaml,
    });
  }, [setVisualDraft]);

  const undoOpaque = useCallback(() => {
    const previous = opaqueHistory.current.pop();
    if (previous) setVisualDraft(previous);
  }, [setVisualDraft]);

  const markSavedRevision = useCallback((
    revision: string,
    expectedCatalogRevisions: readonly BuilderVisualCatalogRevision[],
  ) => {
    const current = visualDraftRef.current;
    if (!current) return;
    const revisionsByRef = new Map(
      expectedCatalogRevisions.map((item) => [item.ref, item.expected_revision] as const),
    );
    const proposalRefs = new Set((current.catalog_documents ?? []).map((item) => item.ref));
    setVisualDraft({
      ...current,
      draft_revision: current.draft_revision + 1,
      expected_session_revision: revision,
      // Customize-chain proposals already live in the visual envelope. Their
      // optimistic fence must travel on the proposal itself because opaque
      // assembly forwards those proposals unchanged, and structured assembly
      // preloads them before applying generated-component expectations.
      catalog_documents: (current.catalog_documents ?? []).map((proposal) => ({
        ...proposal,
        expected_revision: revisionsByRef.get(proposal.ref) ?? proposal.expected_revision,
      })),
      // Expectations for backend-generated workspace components remain in the
      // visual expectation list; preloaded proposals above must not appear here
      // or the assembler correctly reports them as unused expectations.
      expected_catalog_revisions: expectedCatalogRevisions.filter(
        (item) => !proposalRefs.has(item.ref),
      ),
    });
  }, [setVisualDraft]);

  const customizeChain = useCallback(
    async (
      request: Omit<BuilderVisualCustomizeChainRequest, "draft">,
    ): Promise<BuilderVisualCustomizeChainResult> => {
      const current = visualDraftRef.current;
      if (!current) throw new Error("there is no visual draft to customize");
      const result = await customizeVisualDraftChain({ ...request, draft: current });
      if (result.applied) {
        if (current.mode === "opaque_yaml") opaqueHistory.current.push(current);
        setVisualDraft(result.draft);
      }
      return result;
    },
    [setVisualDraft],
  );

  const runVisualCommand = useCallback(
    (request: BuilderVisualDraftCommandRequest): Promise<BuilderVisualDraftCommandResult> =>
      applyVisualDraftCommand(request),
    [],
  );

  const adoptVisualCommandResult = useCallback(
    (result: BuilderVisualDraftCommandResult) => {
      resolveSeq.current += 1;
      setVisualDraft(result.draft);
      setAssemblyResult(null);
      setCompileResult(null);
      setCompileIssues([]);
      setDocumentYaml(null);
      setResolveError(null);
      setSettledDocumentDigest(null);
      setSettledDependencySha256(null);
      setLoading(false);
    },
    [setVisualDraft],
  );

  const saveSession = useCallback(
    async (request: BuilderSessionSaveRequest): Promise<BuilderSessionSaveResult> => {
      const result = await saveBuilderSession(request);
      for (const family of new Set(result.dependency_closure.entries.map((entry) => entry.family))) {
        if (family !== "sessions") void refreshCatalogFamily(family);
      }
      _bumpLibraryRevision();
      await refreshSessions();
      return result;
    },
    [refreshSessions],
  );

  const deploySession = useCallback(
    (request: BuilderSessionDeployRequest): Promise<BuilderSessionDeployAccepted> =>
      deployBuilderSession(request),
    [],
  );

  /** Drop the current world (e.g. starting a fresh workspace): a stale world
   *  must never render behind a draft that has not resolved yet. */
  const clear = useCallback(() => {
    resolveSeq.current += 1;
    setWorld(null);
    setDocumentYaml(null);
    setOpenedSession(null);
    setVisualDraft(null);
    setAssemblyResult(null);
    setResolveError(null);
    setCompileResult(null);
    setCompileIssues([]);
    setSettledDocumentDigest(null);
    setSettledDependencySha256(null);
    setLoading(false);
    opaqueHistory.current = [];
  }, [setVisualDraft]);

  return {
    sessions,
    sessionsError,
    openedSession,
    visualDraft,
    currentVisualDraft,
    assemblyResult,
    world,
    documentYaml,
    loading,
    error: resolveError?.error ?? null,
    resolveError,
    compileResult,
    compileIssues,
    settledDocumentDigest,
    settledDependencySha256,
    createDraft,
    retargetDraft,
    openSession,
    editOpaqueYaml,
    undoOpaque,
    markSavedRevision,
    customizeChain,
    runVisualCommand,
    adoptVisualCommandResult,
    compileDraft,
    hasOpaqueAutosave,
    stashOpaqueDraft,
    restoreOpaqueAutosave,
    adoptRecoveredStructuredDraft,
    saveSession,
    deploySession,
    refreshSessions,
    clear,
  };
}
