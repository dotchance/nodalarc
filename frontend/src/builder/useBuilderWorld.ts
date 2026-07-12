// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Builder world data and typed authoring API state. */

import { useState, useEffect, useCallback, useRef, useSyncExternalStore } from "react";
import { downloadBlob } from "../ui/downloadBlob";
import {
  applyVisualDraftCommand,
  applyVisualDraftWorkspace,
  applyVisualDraftYaml,
  compileVisualDraft,
  createVisualDraft,
  customizeVisualDraftChain,
  deleteCatalogDocument,
  deployBuilderSession,
  exportCatalogSessionYaml,
  getBuilderBootstrap,
  getCatalogDependents,
  getCatalogDocument,
  importCatalogSessionYaml,
  listCatalog,
  mutateVisualDraftControls,
  openVisualDraft,
  retargetVisualDraft,
  saveBuilderSession,
} from "./builderApiClient";
import type { BuilderResolveError } from "./builderTypes";
import { writeSessionYamlExport } from "./sessionYamlTransfer";
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
  BuilderVisualControlMutationRequest,
  BuilderVisualDraftAssemblyResult,
  BuilderVisualDraftApplyYamlResult,
  BuilderVisualDraftCommandRequest,
  BuilderVisualDraftCommandResult,
  BuilderVisualDraftCreateRequest,
  BuilderVisualDraftEnvelope,
  BuilderVisualDraftRetargetRequest,
  BuilderVisualWorkspace,
  CatalogDocumentSummary,
  CatalogFamily,
  CatalogSessionYamlImportResult,
  CatalogYamlImportFile,
  CatalogDraftSaveResult,
  SessionRef,
} from "./generated/builderApi";

function _recoveredDraftSummary(draft: BuilderVisualDraftEnvelope): CatalogDocumentSummary {
  const ref = draft.source_ref ?? draft.target_ref;
  const displayName = ref.split("/").pop()?.replace(/\.ya?ml$/, "") ?? "YAML draft";
  const serialized = draft.session_yaml;
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

// Save-to-library publishes the saved entry here so the Library can select and
// highlight it without separate wiring in each editor.

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

/** Reveal a just-placed reference in the outline because it has no editor. */
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

/** Export ordinary session and catalog YAML files without introducing a
 *  user-facing carrier format. Supported browsers preserve the backend-owned
 *  directory layout; other browsers download each YAML document separately. */
export async function exportSessionYaml(entry: CatalogDocumentSummary): Promise<void> {
  const yamlExport = await exportCatalogSessionYaml({
    session_ref: entry.ref as SessionRef,
    expected_session_revision: entry.revision,
  });
  await writeSessionYamlExport(entry.ref, yamlExport.files);
}

/** Ask the backend to validate or atomically commit ordinary YAML texts. YAML
 *  grammar, document identity, reference placement, and collision authority
 *  remain entirely on the server. */
export async function importSessionYamlFiles(
  yamlFiles: readonly CatalogYamlImportFile[],
  proposalToken: string | null,
): Promise<CatalogSessionYamlImportResult> {
  if (yamlFiles.length === 0) throw new Error("select at least one YAML file");
  const result = await importCatalogSessionYaml({
    yaml_files: yamlFiles,
    commit: proposalToken !== null,
    ...(proposalToken === null ? {} : { proposal_token: proposalToken }),
  });
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

export type SessionDraftWriter =
  | "create"
  | "open"
  | "yaml"
  | "workspace"
  | "command"
  | "customize"
  | "control"
  | "retarget"
  | "compile"
  | "save";

export interface SessionYamlBufferState {
  text: string;
  appliedText: string;
  generation: number;
  dirty: boolean;
  applied: boolean;
  canonicalizationRequired: boolean;
  canonicalizationAccepted: boolean;
  issues: readonly BuilderIssue[];
}

export interface SessionCoordinatorCapture {
  epoch: number;
  draftRevision: number;
  bufferGeneration: number;
  documentDigest: string | null;
  dependencyDigest: string | null;
}

export interface BuilderSessionSaveAdoption {
  result: BuilderSessionSaveResult;
  reopenedDraft: BuilderVisualDraftEnvelope | null;
  postCommitError: string | null;
}

const EMPTY_YAML_BUFFER: SessionYamlBufferState = {
  text: "",
  appliedText: "",
  generation: 0,
  dirty: false,
  applied: false,
  canonicalizationRequired: false,
  canonicalizationAccepted: false,
  issues: [],
};

class SupersededSessionOperation extends Error {
  constructor() {
    super("the session changed while this operation was running");
  }
}

export function useBuilderWorld() {
  const [sessions, setSessions] = useState<CatalogDocumentSummary[]>([]);
  const [sessionsError, setSessionsError] = useState<string | null>(null);
  const [openedSession, setOpenedSession] = useState<CatalogDocumentSummary | null>(null);
  const [visualDraft, setVisualDraftState] = useState<BuilderVisualDraftEnvelope | null>(null);
  const visualDraftRef = useRef<BuilderVisualDraftEnvelope | null>(null);
  const [yamlBuffer, setYamlBufferState] = useState<SessionYamlBufferState>(EMPTY_YAML_BUFFER);
  const yamlBufferRef = useRef<SessionYamlBufferState>(EMPTY_YAML_BUFFER);
  const [writer, setWriter] = useState<SessionDraftWriter | null>(null);
  const writerRef = useRef<SessionDraftWriter | null>(null);
  const writerTokenRef = useRef(0);
  const activeWriterTokenRef = useRef(0);
  const epochRef = useRef(0);
  const writeQueueRef = useRef<Promise<void>>(Promise.resolve());
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
  const settledDigestsRef = useRef<{ document: string | null; dependency: string | null }>({
    document: null,
    dependency: null,
  });

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

  const setYamlBuffer = useCallback((next: SessionYamlBufferState) => {
    yamlBufferRef.current = next;
    setYamlBufferState(next);
  }, []);

  const beginEpoch = useCallback(() => {
    epochRef.current += 1;
    writeQueueRef.current = Promise.resolve();
    writerTokenRef.current += 1;
    activeWriterTokenRef.current = writerTokenRef.current;
    writerRef.current = null;
    setWriter(null);
    setLoading(false);
    return epochRef.current;
  }, []);

  const assertEpoch = useCallback((epoch: number) => {
    if (epoch !== epochRef.current) throw new SupersededSessionOperation();
  }, []);

  const enqueueWriter = useCallback(
    async <Result,>(
      kind: SessionDraftWriter,
      operation: (epoch: number) => Promise<Result>,
      assertEpochAfter = true,
    ): Promise<Result> => {
      const epoch = epochRef.current;
      let resolveResult!: (result: Result) => void;
      let rejectResult!: (reason: unknown) => void;
      const resultPromise = new Promise<Result>((resolve, reject) => {
        resolveResult = resolve;
        rejectResult = reject;
      });
      writeQueueRef.current = writeQueueRef.current
        .catch(() => undefined)
        .then(async () => {
          const writerToken = ++writerTokenRef.current;
          activeWriterTokenRef.current = writerToken;
          try {
            assertEpoch(epoch);
            writerRef.current = kind;
            setWriter(kind);
            setLoading(true);
            const result = await operation(epoch);
            if (assertEpochAfter) assertEpoch(epoch);
            resolveResult(result);
          } catch (cause) {
            rejectResult(cause);
          } finally {
            if (activeWriterTokenRef.current === writerToken) {
              writerRef.current = null;
              setWriter(null);
              setLoading(false);
            }
          }
        });
      return resultPromise;
    },
    [assertEpoch],
  );

  const resetCompileFacts = useCallback(() => {
    setAssemblyResult(null);
    setCompileResult(null);
    setCompileIssues([]);
    setWorld(null);
    setDocumentYaml(null);
    setResolveError(null);
    setSettledDocumentDigest(null);
    setSettledDependencySha256(null);
    settledDigestsRef.current = { document: null, dependency: null };
  }, []);

  const adoptRecoveredStructuredDraft = useCallback((
    draft: BuilderVisualDraftEnvelope,
    recoveredYaml?: SessionYamlBufferState,
  ) => {
    beginEpoch();
    setOpenedSession(draft.source_ref ? _recoveredDraftSummary(draft) : null);
    setVisualDraft(draft);
    setYamlBuffer(
      recoveredYaml ?? {
        text: draft.session_yaml,
        appliedText: draft.session_yaml,
        generation: 0,
        dirty: false,
        applied:
          draft.projection_status === "applied" ||
          draft.projection_status === "incomplete_authoring",
        canonicalizationRequired: false,
        canonicalizationAccepted: false,
        issues: [],
      },
    );
    resetCompileFacts();
  }, [beginEpoch, resetCompileFacts, setVisualDraft, setYamlBuffer]);

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
    settledDigestsRef.current = {
      document: compile.digests?.document ?? null,
      dependency: compile.digests?.dependency ?? null,
    };
    setResolveError(firstError ? { error: firstError.message } : null);
  }, [setVisualDraft]);

  const compileCurrent = useCallback(async (): Promise<BuilderVisualDraftAssemblyResult> =>
    enqueueWriter("compile", async (epoch) => {
      const draft = visualDraftRef.current;
      if (!draft) throw new Error("there is no session draft to compile");
      try {
        const result = await compileVisualDraft({ draft });
        assertEpoch(epoch);
        adoptCompileResult(result);
        return result;
      } catch (cause) {
        assertEpoch(epoch);
        resetCompileFacts();
        setResolveError({ error: cause instanceof Error ? cause.message : String(cause) });
        throw cause;
      }
    }), [adoptCompileResult, assertEpoch, enqueueWriter, resetCompileFacts]);

  const createDraft = useCallback(
    async (request: BuilderVisualDraftCreateRequest): Promise<BuilderVisualDraftEnvelope> => {
      beginEpoch();
      return enqueueWriter("create", async (epoch) => {
        const draft = await createVisualDraft(request);
        assertEpoch(epoch);
        const assembled = await compileVisualDraft({ draft });
        assertEpoch(epoch);
        setOpenedSession(null);
        adoptCompileResult(assembled);
        const canonical = assembled.compile_result.canonical_session_yaml;
        setYamlBuffer({
          text: draft.session_yaml,
          appliedText: draft.session_yaml,
          generation: 0,
          dirty: false,
          applied:
            draft.projection_status === "applied" ||
            draft.projection_status === "incomplete_authoring",
          canonicalizationRequired: canonical !== null && canonical !== undefined && canonical !== draft.session_yaml,
          canonicalizationAccepted: false,
          issues: [],
        });
        return draft;
      });
    },
    [adoptCompileResult, assertEpoch, beginEpoch, enqueueWriter, setYamlBuffer],
  );

  const openSession = useCallback(
    async (
      entry: CatalogDocumentSummary,
      targetRef?: SessionRef,
    ): Promise<{ ok: true; draft: BuilderVisualDraftEnvelope } | { ok: false; reason: string }> => {
      beginEpoch();
      try {
        const draft = await enqueueWriter("open", async (epoch) => {
          const opened = await openVisualDraft({
            source_ref: entry.ref as SessionRef,
            ...(targetRef ? { target_ref: targetRef } : {}),
          });
          assertEpoch(epoch);
          const assembled = await compileVisualDraft({ draft: opened });
          assertEpoch(epoch);
          setOpenedSession(entry);
          adoptCompileResult(assembled);
          const canonical = assembled.compile_result.canonical_session_yaml;
          setYamlBuffer({
            text: opened.session_yaml,
            appliedText: opened.session_yaml,
            generation: 0,
            dirty: false,
            applied: opened.projection_status === "applied",
            canonicalizationRequired: canonical !== null && canonical !== undefined && canonical !== opened.session_yaml,
            canonicalizationAccepted: false,
            issues: [],
          });
          return opened;
        });
        return { ok: true, draft };
      } catch (cause) {
        return {
          ok: false,
          reason: cause instanceof Error ? cause.message : String(cause),
        };
      }
    },
    [adoptCompileResult, assertEpoch, beginEpoch, enqueueWriter, setYamlBuffer],
  );

  const editYamlBuffer = useCallback((text: string) => {
    if (writerRef.current !== null && writerRef.current !== "yaml") return false;
    const current = yamlBufferRef.current;
    setYamlBuffer({
      ...current,
      text,
      generation: current.generation + 1,
      dirty: text !== current.appliedText,
      applied: text === current.appliedText && current.applied,
      issues: text === current.appliedText ? current.issues : [],
    });
    return true;
  }, [setYamlBuffer]);

  const applyYamlBuffer = useCallback(async (
    expectedGeneration?: number,
  ): Promise<BuilderVisualDraftApplyYamlResult> =>
    enqueueWriter("yaml", async (epoch) => {
      const draft = visualDraftRef.current;
      const buffer = yamlBufferRef.current;
      if (!draft) throw new Error("there is no session draft to edit");
      if (expectedGeneration !== undefined && buffer.generation !== expectedGeneration) {
        throw new SupersededSessionOperation();
      }
      const result = await applyVisualDraftYaml({
        draft,
        expected_draft_revision: draft.draft_revision,
        buffer_generation: buffer.generation,
        yaml_text: buffer.text,
      });
      assertEpoch(epoch);
      if (result.buffer_generation !== yamlBufferRef.current.generation) {
        throw new SupersededSessionOperation();
      }
      if (!result.applied) {
        setVisualDraft(result.draft);
        setYamlBuffer({
          text: result.yaml_text,
          appliedText: buffer.appliedText,
          generation: result.buffer_generation,
          dirty: result.yaml_text !== buffer.appliedText,
          applied: false,
          canonicalizationRequired: false,
          canonicalizationAccepted: false,
          issues: result.issues ?? [],
        });
        return result;
      }
      const assembled = await compileVisualDraft({ draft: result.draft });
      assertEpoch(epoch);
      adoptCompileResult(assembled);
      setYamlBuffer({
        text: result.yaml_text,
        appliedText: result.yaml_text,
        generation: result.buffer_generation,
        dirty: false,
        applied: true,
        canonicalizationRequired: result.canonicalization_required,
        canonicalizationAccepted: false,
        issues: result.issues ?? [],
      });
      return result;
    }), [adoptCompileResult, assertEpoch, enqueueWriter, setVisualDraft, setYamlBuffer]);

  const useCanonicalYaml = useCallback(async () => {
    const canonical = assemblyResult?.compile_result.canonical_session_yaml;
    if (!canonical) throw new Error("canonical session YAML is not available");
    const current = yamlBufferRef.current;
    setYamlBuffer({
      ...current,
      text: canonical,
      generation: current.generation + 1,
      dirty: canonical !== current.appliedText,
      canonicalizationAccepted: true,
      issues: [],
    });
    return applyYamlBuffer(current.generation + 1);
  }, [applyYamlBuffer, assemblyResult?.compile_result.canonical_session_yaml, setYamlBuffer]);

  const applyWorkspace = useCallback(async (
    workspace: BuilderVisualWorkspace,
  ): Promise<BuilderVisualDraftAssemblyResult> =>
    enqueueWriter("workspace", async (epoch) => {
      const draft = visualDraftRef.current;
      const buffer = yamlBufferRef.current;
      if (!draft) throw new Error("there is no session draft to edit");
      if (
        buffer.dirty ||
        !buffer.applied ||
        (buffer.canonicalizationRequired && !buffer.canonicalizationAccepted)
      ) {
        throw new Error("apply or canonicalize the YAML buffer before editing graphically");
      }
      let result: BuilderVisualDraftAssemblyResult;
      try {
        result = await applyVisualDraftWorkspace({
          draft,
          expected_draft_revision: draft.draft_revision,
          workspace,
        });
        assertEpoch(epoch);
      } catch (cause) {
        assertEpoch(epoch);
        resetCompileFacts();
        setResolveError({ error: cause instanceof Error ? cause.message : String(cause) });
        throw cause;
      }
      adoptCompileResult(result);
      const canonical = result.compile_result.canonical_session_yaml ?? result.visual_draft.session_yaml;
      setYamlBuffer({
        text: canonical,
        appliedText: canonical,
        generation: buffer.generation + 1,
        dirty: false,
        applied: true,
        canonicalizationRequired: false,
        canonicalizationAccepted: true,
        issues: [],
      });
      return result;
    }), [adoptCompileResult, assertEpoch, enqueueWriter, resetCompileFacts, setYamlBuffer]);

  const customizeChain = useCallback(
    async (
      request: Omit<
        BuilderVisualCustomizeChainRequest,
        "draft" | "expected_draft_revision"
      >,
    ): Promise<BuilderVisualCustomizeChainResult> => {
      return enqueueWriter("customize", async (epoch) => {
        const current = visualDraftRef.current;
        if (!current) throw new Error("there is no visual draft to customize");
        const buffer = yamlBufferRef.current;
        if (
          buffer.dirty ||
          !buffer.applied ||
          (buffer.canonicalizationRequired && !buffer.canonicalizationAccepted)
        ) {
          throw new Error("apply or canonicalize the YAML buffer before editing graphically");
        }
        const result = await customizeVisualDraftChain({
          ...request,
          draft: current,
          expected_draft_revision: current.draft_revision,
        });
        assertEpoch(epoch);
        if (!result.applied) return result;
        const assembled = await compileVisualDraft({ draft: result.draft });
        assertEpoch(epoch);
        adoptCompileResult(assembled);
        const canonical = assembled.compile_result.canonical_session_yaml ?? result.draft.session_yaml;
        const nextBuffer = yamlBufferRef.current;
        setYamlBuffer({
          text: canonical,
          appliedText: canonical,
          generation: nextBuffer.generation + 1,
          dirty: false,
          applied: true,
          canonicalizationRequired: false,
          canonicalizationAccepted: true,
          issues: [],
        });
        return result;
      });
    },
    [adoptCompileResult, assertEpoch, enqueueWriter, setYamlBuffer],
  );

  const runVisualCommand = useCallback(
    (command: BuilderVisualDraftCommandRequest["command"]): Promise<BuilderVisualDraftCommandResult> =>
      enqueueWriter("command", async (epoch) => {
        const draft = visualDraftRef.current;
        if (!draft) throw new Error("there is no visual draft to edit");
        const currentBuffer = yamlBufferRef.current;
        if (
          currentBuffer.dirty ||
          !currentBuffer.applied ||
          (currentBuffer.canonicalizationRequired && !currentBuffer.canonicalizationAccepted)
        ) {
          throw new Error("apply or canonicalize the YAML buffer before editing graphically");
        }
        const result = await applyVisualDraftCommand({
          draft,
          expected_draft_revision: draft.draft_revision,
          command,
        });
        assertEpoch(epoch);
        const assembled = await compileVisualDraft({ draft: result.draft });
        assertEpoch(epoch);
        adoptCompileResult(assembled);
        const canonical = assembled.compile_result.canonical_session_yaml ?? result.draft.session_yaml;
        const buffer = yamlBufferRef.current;
        setYamlBuffer({
          text: canonical,
          appliedText: canonical,
          generation: buffer.generation + 1,
          dirty: false,
          applied: true,
          canonicalizationRequired: false,
          canonicalizationAccepted: true,
          issues: [],
        });
        return result;
      }),
    [adoptCompileResult, assertEpoch, enqueueWriter, setYamlBuffer],
  );

  const prepareRetarget = useCallback(
    (
      targetRef: BuilderVisualDraftRetargetRequest["target_ref"],
    ): Promise<BuilderVisualDraftAssemblyResult> =>
      enqueueWriter("retarget", async (epoch) => {
        const draft = visualDraftRef.current;
        if (!draft) throw new Error("there is no visual draft to retarget");
        const buffer = yamlBufferRef.current;
        if (
          buffer.dirty ||
          !buffer.applied ||
          (buffer.canonicalizationRequired && !buffer.canonicalizationAccepted)
        ) {
          throw new Error("apply or canonicalize the YAML buffer before saving under a new name");
        }
        const result = await retargetVisualDraft({
          draft,
          expected_draft_revision: draft.draft_revision,
          target_ref: targetRef,
        });
        assertEpoch(epoch);
        return result;
      }),
    [assertEpoch, enqueueWriter],
  );

  const mutateControls = useCallback(
    (commands: BuilderVisualControlMutationRequest["commands"]) =>
      enqueueWriter("control", async (epoch) => {
        const draft = visualDraftRef.current;
        if (!draft) throw new Error("there is no visual draft to edit");
        const buffer = yamlBufferRef.current;
        if (
          buffer.dirty ||
          !buffer.applied ||
          (buffer.canonicalizationRequired && !buffer.canonicalizationAccepted)
        ) {
          throw new Error("apply or canonicalize the YAML buffer before editing graphically");
        }
        const result = await mutateVisualDraftControls({
          draft,
          expected_draft_revision: draft.draft_revision,
          commands,
        });
        assertEpoch(epoch);
        adoptCompileResult(result);
        const canonical =
          result.compile_result.canonical_session_yaml ?? result.visual_draft.session_yaml;
        setYamlBuffer({
          text: canonical,
          appliedText: canonical,
          generation: buffer.generation + 1,
          dirty: false,
          applied: true,
          canonicalizationRequired: false,
          canonicalizationAccepted: true,
          issues: [],
        });
        return result;
      }),
    [adoptCompileResult, assertEpoch, enqueueWriter, setYamlBuffer],
  );

  const captureCoordinator = useCallback((): SessionCoordinatorCapture => ({
    epoch: epochRef.current,
    draftRevision: visualDraftRef.current?.draft_revision ?? -1,
    bufferGeneration: yamlBufferRef.current.generation,
    documentDigest: settledDigestsRef.current.document,
    dependencyDigest: settledDigestsRef.current.dependency,
  }), []);

  const captureIsCurrent = useCallback((capture: SessionCoordinatorCapture) => {
    const current = captureCoordinator();
    return (
      capture.epoch === current.epoch &&
      capture.draftRevision === current.draftRevision &&
      capture.bufferGeneration === current.bufferGeneration &&
      capture.documentDigest === current.documentDigest &&
      capture.dependencyDigest === current.dependencyDigest
    );
  }, [captureCoordinator]);

  const saveSession = useCallback(
    async (
      request: BuilderSessionSaveRequest,
      capture: SessionCoordinatorCapture,
    ): Promise<BuilderSessionSaveAdoption> =>
      enqueueWriter("save", async (epoch) => {
        if (!captureIsCurrent(capture)) throw new SupersededSessionOperation();
        const result = await saveBuilderSession(request);
        try {
          assertEpoch(epoch);
          for (const family of new Set(result.dependency_closure.entries.map((entry) => entry.family))) {
            if (family !== "sessions") void refreshCatalogFamily(family);
          }
          _bumpLibraryRevision();
          await refreshSessions();
          assertEpoch(epoch);
          if (!captureIsCurrent(capture)) {
            return {
              result,
              reopenedDraft: null,
              postCommitError: "the editor changed after persistence committed",
            };
          }
          const reopened = await openVisualDraft({
            source_ref: result.session.ref as SessionRef,
            target_ref: result.session.ref as SessionRef,
          });
          assertEpoch(epoch);
          if (!captureIsCurrent(capture)) {
            return {
              result,
              reopenedDraft: null,
              postCommitError: "the editor changed before the saved session reopened",
            };
          }
          const assembled = await compileVisualDraft({ draft: reopened });
          assertEpoch(epoch);
          adoptCompileResult(assembled);
          const canonical = assembled.compile_result.canonical_session_yaml ?? reopened.session_yaml;
          setYamlBuffer({
            text: canonical,
            appliedText: canonical,
            generation: capture.bufferGeneration + 1,
            dirty: false,
            applied: true,
            canonicalizationRequired: false,
            canonicalizationAccepted: true,
            issues: [],
          });
          setOpenedSession({
            ref: result.session.ref,
            family: "sessions",
            namespace: "user",
            revision: result.session.revision,
            size_bytes: new TextEncoder().encode(result.session.canonical_yaml).byteLength,
            display_name: result.session.ref.split("/").pop()?.replace(/\.ya?ml$/, "") ?? "session",
            summary: null,
          });
          return { result, reopenedDraft: reopened, postCommitError: null };
        } catch (cause) {
          return {
            result,
            reopenedDraft: null,
            postCommitError:
              cause instanceof Error ? cause.message : String(cause),
          };
        }
      }, false),
    [adoptCompileResult, assertEpoch, captureIsCurrent, enqueueWriter, refreshSessions, setYamlBuffer],
  );

  const adoptCommittedRetarget = useCallback((
    prepared: BuilderVisualDraftAssemblyResult,
    saved: {
      ref: string;
      revision?: string | null;
      canonicalYaml?: string | null;
    },
  ) => {
    beginEpoch();
    adoptCompileResult(prepared);
    const canonical =
      prepared.compile_result.canonical_session_yaml ?? prepared.visual_draft.session_yaml;
    setYamlBuffer({
      text: canonical,
      appliedText: canonical,
      generation: yamlBufferRef.current.generation + 1,
      dirty: false,
      applied: true,
      canonicalizationRequired: false,
      canonicalizationAccepted: true,
      issues: [],
    });
    setOpenedSession({
      ref: saved.ref,
      family: "sessions",
      namespace: "user",
      revision: saved.revision ?? "committed-unverified",
      size_bytes: new TextEncoder().encode(saved.canonicalYaml ?? canonical).byteLength,
      display_name: saved.ref.split("/").pop()?.replace(/\.ya?ml$/, "") ?? "session",
      summary: null,
    });
  }, [adoptCompileResult, beginEpoch, setYamlBuffer]);

  const deploySession = useCallback(
    (request: BuilderSessionDeployRequest): Promise<BuilderSessionDeployAccepted> =>
      deployBuilderSession(request),
    [],
  );

  /** Drop the current world (e.g. starting a fresh workspace): a stale world
   *  must never render behind a draft that has not resolved yet. */
  const clear = useCallback(() => {
    beginEpoch();
    setOpenedSession(null);
    setVisualDraft(null);
    setYamlBuffer(EMPTY_YAML_BUFFER);
    resetCompileFacts();
  }, [beginEpoch, resetCompileFacts, setVisualDraft, setYamlBuffer]);

  return {
    sessions,
    sessionsError,
    openedSession,
    visualDraft,
    yamlBuffer,
    writer,
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
    openSession,
    editYamlBuffer,
    applyYamlBuffer,
    useCanonicalYaml,
    applyWorkspace,
    customizeChain,
    runVisualCommand,
    mutateControls,
    prepareRetarget,
    compileCurrent,
    adoptRecoveredStructuredDraft,
    captureCoordinator,
    captureIsCurrent,
    saveSession,
    adoptCommittedRetarget,
    deploySession,
    refreshSessions,
    clear,
  };
}
