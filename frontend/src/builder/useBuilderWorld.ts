// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Builder world data and typed authoring API state. */

import { useState, useEffect, useCallback, useRef, useSyncExternalStore } from "react";
import { downloadBlob } from "../ui/downloadBlob";
import {
  acceptVisualDraftState,
  applyVisualDraftCommand,
  applyVisualDraftEdits,
  applyVisualDraftWorkspace,
  applyVisualDraftYaml,
  compileBuilderDraft,
  compileVisualDraft,
  createVisualDraft,
  customizeVisualDraftChain,
  deleteCatalogDocument,
  deployBuilderSession,
  editVisualDraftObject,
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
  BuilderContentEdit,
  BuilderDeployVerdict,
  BuilderDraftState,
  BuilderIssue,
  BuilderListEdit,
  BuilderPendingEditOutcome,
  BuilderWorld,
  BuilderSessionSaveRequest,
  BuilderSessionSaveResult,
  BuilderSessionDeployAccepted,
  BuilderSessionDeployRequest,
  BuilderVisualCustomizeChainRequest,
  BuilderVisualCustomizeChainResult,
  BuilderVisualControlMutationRequest,
  BuilderVisualDraftAssemblyResult,
  BuilderVisualDraftApplyEditsResult,
  BuilderVisualDraftApplyYamlResult,
  BuilderVisualDraftCommandRequest,
  BuilderVisualDraftCommandResult,
  BuilderVisualDraftCreateRequest,
  BuilderVisualDraftEnvelope,
  BuilderVisualDraftRetargetRequest,
  BuilderVisualObjectEditRequest,
  BuilderVisualObjectEditResult,
  BuilderVisualWorkspace,
  CatalogDocumentSummary,
  CatalogFamily,
  CatalogSessionYamlImportResult,
  CatalogYamlImportFile,
  CatalogDraftSaveResult,
  JsonValue,
  SessionRef,
} from "./generated/builderApi";

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
// dependency closure can change. The Builder's preview depends on this
// revision, so open edits recompile against the changed closure.

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
  const entries = await refreshCatalogFamily(result.draft.state.family);
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

/** The pending link rule, routing domain and boundary edits, as notices name them. */
export function pendingEditsPhrase(count: number): string {
  return count === 1 ? "pending link or routing edit" : `${count} pending link and routing edits`;
}

/** Fail-closed deploy gate bound to one backend-issued saved revision verdict. */
export function canDeploy(input: {
  savedVerdict: BuilderDeployVerdict | null;
  settledDocumentDigest: string | null;
  settledDependencyDigest: string | null;
  dirtyWindowCount: number;
  pendingEditCount: number;
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
  if (input.pendingEditCount > 0) {
    return {
      ok: false,
      reason: `apply or discard the ${pendingEditsPhrase(input.pendingEditCount)} first`,
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

/** Whether the session YAML buffer holds text the graphical editors must not
 *  edit around: unapplied or uncanonicalized text of the open draft. With no
 *  draft open the buffer holds no text, and nothing is locked. */
export function yamlBlocksGraphicalEditing(
  hasDraft: boolean,
  buffer: SessionYamlBufferState,
): boolean {
  return (
    hasDraft &&
    (buffer.dirty ||
      !buffer.applied ||
      (buffer.canonicalizationRequired && !buffer.canonicalizationAccepted))
  );
}

/** Whether the open session draft holds work the user has not saved: it moved
 *  past the revision it was last opened, created or saved at, its workspace
 *  holds edits the backend has not accepted yet, or it holds unapplied window,
 *  pending link and routing, or YAML edits. The Builder keeps no copy of such
 *  work. */
export function draftHoldsUnsavedWork(input: {
  draft: Pick<BuilderVisualDraftEnvelope, "target_ref" | "draft_revision"> | null;
  clean: { targetRef: string; revision: number } | null;
  twinMatchesDraft: boolean;
  dirtyWindowCount: number;
  pendingEditCount: number;
  yamlDirty: boolean;
}): boolean {
  const { draft, clean } = input;
  if (draft === null) return false;
  return (
    clean === null ||
    clean.targetRef !== draft.target_ref ||
    clean.revision !== draft.draft_revision ||
    !input.twinMatchesDraft ||
    input.dirtyWindowCount > 0 ||
    input.pendingEditCount > 0 ||
    input.yamlDirty
  );
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
  | "edits"
  | "retarget"
  | "compile"
  | "save"
  | "undo";

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

/** Where a pending edit applies: a link rule or a routing domain by the id it
 *  had when the edit began, or the routing boundary list as a whole. */
type PendingEditAddress = BuilderContentEdit["address"];

/** A pending edit of one link rule, one routing domain or the boundary list:
 *  the edit as the backend applies it, beside the backend's latest answer for
 *  its working JSON. Nothing in it is accepted. */
interface PendingObjectEdit {
  readonly edit: BuilderContentEdit;
  readonly answer: BuilderVisualObjectEditResult;
}

/** The key an address holds its pending edit under. */
export function pendingEditKey(address: PendingEditAddress): string {
  return address.kind === "member" ? `${address.list_pointer}/${address.id}` : address.pointer;
}

const NO_PENDING_EDITS: ReadonlyMap<string, PendingObjectEdit> = new Map();

/** What the backend last said about each pending edit, held with the accepted
 *  draft and the pending edits it said it about: a preview's outcome for every
 *  edit, or a refused apply's outcome for each edit the apply sent. */
interface PendingEditVerdicts {
  readonly draft: BuilderVisualDraftEnvelope;
  readonly edits: ReadonlyMap<string, PendingObjectEdit>;
  readonly outcomes: ReadonlyMap<string, BuilderPendingEditOutcome>;
}

const NO_OUTCOMES: ReadonlyMap<string, BuilderPendingEditOutcome> = new Map();

const NO_KEYS: ReadonlySet<string> = new Set();

function isJsonList(value: JsonValue | undefined): value is ReadonlyArray<JsonValue> {
  return Array.isArray(value);
}

function isJsonObject(value: JsonValue | undefined): value is Readonly<Record<string, JsonValue>> {
  return value !== undefined && value !== null && typeof value === "object" && !isJsonList(value);
}

/** The value at a pointer of the accepted session; undefined where it holds none. */
function acceptedValueAt(
  session: Readonly<Record<string, JsonValue>>,
  pointer: string,
): JsonValue | undefined {
  let value: JsonValue | undefined = session;
  for (const token of pointer.split("/").slice(1)) {
    value = isJsonObject(value) ? value[token] : undefined;
  }
  return value;
}

/** The object the accepted session holds at `address`: the list itself, or the
 *  member with the address's id. Undefined where the session holds none. */
function acceptedObjectAt(
  session: Readonly<Record<string, JsonValue>>,
  address: PendingEditAddress,
): Readonly<Record<string, JsonValue>> | ReadonlyArray<JsonValue> | undefined {
  if (address.kind === "whole_list") {
    const list = acceptedValueAt(session, address.pointer);
    if (list !== undefined && !isJsonList(list)) {
      throw new Error(`the accepted session holds ${address.pointer} as something other than a list`);
    }
    return list;
  }
  const members = acceptedValueAt(session, address.list_pointer);
  const matches = (isJsonList(members) ? members : []).filter(
    (member): member is Readonly<Record<string, JsonValue>> =>
      isJsonObject(member) && member.id === address.id,
  );
  if (matches.length > 1) {
    throw new Error(`the accepted session holds ${address.id} twice in ${address.list_pointer}`);
  }
  return matches[0];
}

/** A new pending edit of the object at `address` in the accepted session. Its
 *  base and its working JSON are that object as the session holds it. */
function beginPendingEdit(
  session: Readonly<Record<string, JsonValue>>,
  address: PendingEditAddress,
): BuilderContentEdit {
  const object = acceptedObjectAt(session, address);
  if (object === undefined) {
    throw new Error(
      address.kind === "whole_list"
        ? `the accepted session holds no ${address.pointer} list to edit`
        : `the accepted session holds no ${address.id} in ${address.list_pointer}`,
    );
  }
  return { kind: "content", address, base: object, working: object };
}

/** `edit` taken against the object the accepted session now holds at its
 *  address. Its base becomes that object, or is absent when the session holds
 *  none; its working JSON stays as the user left it. */
function rebasePendingEdit(
  session: Readonly<Record<string, JsonValue>>,
  edit: BuilderContentEdit,
): BuilderContentEdit {
  const object = acceptedObjectAt(session, edit.address);
  return object === undefined
    ? { kind: "content", address: edit.address, working: edit.working }
    : { kind: "content", address: edit.address, base: object, working: edit.working };
}

/** The ids of the members of one id-bearing list of the accepted session, in
 *  the session's order. */
function acceptedMemberIds(
  session: Readonly<Record<string, JsonValue>>,
  listPointer: BuilderListEdit["list_pointer"],
): string[] {
  const members = acceptedValueAt(session, listPointer);
  return (isJsonList(members) ? members : []).map((member) => {
    const id = isJsonObject(member) ? member.id : undefined;
    if (typeof id !== "string") {
      throw new Error(`a member of ${listPointer} in the accepted session has no id`);
    }
    return id;
  });
}

/** The backend's reasons for a refused batch: the issues of each edit it did
 *  not apply, then those of the session the edits made that no edit owns. */
export function refusalMessage(
  result: Pick<BuilderVisualDraftApplyEditsResult, "outcomes" | "issues">,
): string {
  return [
    ...result.outcomes
      .filter((outcome) => outcome.status !== "applied")
      .flatMap((outcome) => outcome.issues.map((issue) => issue.message)),
    ...result.issues.map((issue) => issue.message),
  ].join("; ");
}

/** What an object edit is made against: the accepted draft, its revision, and
 *  the object's edit as it now stands, which is its pending edit or a new one
 *  that starts from the object in the accepted session. */
function objectEditBasis(
  draft: BuilderVisualDraftEnvelope | null,
  pending: ReadonlyMap<string, PendingObjectEdit>,
  address: PendingEditAddress,
): {
  draft: BuilderVisualDraftEnvelope;
  appliedRevision: number;
  key: string;
  edit: BuilderContentEdit;
} {
  if (!draft) throw new Error("there is no visual draft to edit");
  const accepted = draft.applied_session ?? null;
  const appliedRevision = draft.applied_revision ?? null;
  if (accepted === null || appliedRevision === null) {
    throw new Error("link rules, routing domains and boundaries are edited on an accepted session");
  }
  const key = pendingEditKey(address);
  const edit = pending.get(key)?.edit ?? beginPendingEdit(accepted, address);
  return { draft, appliedRevision, key, edit };
}

/** The key of the pending edit a command starts from. A rederive starts from
 *  its rule as the rule's card shows it, with the rule's pending edit applied,
 *  and its result takes that edit's place. No other command starts from a
 *  pending edit. */
function commandStartEditKey(command: BuilderVisualDraftCommandRequest["command"]): string | null {
  return command.operation === "rederive_link"
    ? pendingEditKey({ kind: "member", list_pointer: "/link_rules", id: command.rule_id })
    : null;
}

/** One step of the undo history. `replaced` is the accepted session a change
 *  replaced, with its proposals; it is null for a command that accepted
 *  nothing and only created pending edits. `created` holds the keys of the
 *  pending edits the change created that are still pending. */
interface UndoStep {
  readonly replaced: BuilderDraftState | null;
  readonly created: ReadonlySet<string>;
}

/** The most steps the undo history holds; the oldest goes first. */
const UNDO_LIMIT = 100;

/** The accepted session of `draft` with its proposals, or null when the draft
 *  is not fully accepted. The proposals of such a draft can belong to an edit
 *  the backend has not accepted. */
function acceptedStateOf(draft: BuilderVisualDraftEnvelope): BuilderDraftState | null {
  const session = draft.projection_status === "applied" ? (draft.applied_session ?? null) : null;
  return session === null ? null : { session, catalog_documents: draft.catalog_documents };
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
  // Pending link rule, routing domain and boundary edits by address. The ref
  // holds the same map so writers check the edits as they are now.
  const [pendingEdits, setPendingEditsState] =
    useState<ReadonlyMap<string, PendingObjectEdit>>(NO_PENDING_EDITS);
  const pendingEditsRef = useRef<ReadonlyMap<string, PendingObjectEdit>>(NO_PENDING_EDITS);
  // The latest object edit asked for each address. An older request's answer
  // is not held.
  const objectEditSequenceRef = useRef(0);
  const latestObjectEditRef = useRef(new Map<string, number>());
  const [pendingEditVerdicts, setPendingEditVerdicts] = useState<PendingEditVerdicts | null>(
    null,
  );
  // The undo history of the draft the page holds, oldest step first. Only the
  // writers that accept a change record a step.
  const undoStepsRef = useRef<ReadonlyArray<UndoStep>>([]);
  // The accepted session the page last adopted, with its proposals: the state
  // the next accepted change replaces. A draft that is not fully accepted
  // leaves it as it was.
  const acceptedStateRef = useRef<BuilderDraftState | null>(null);

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

  const setPendingEdits = useCallback((next: ReadonlyMap<string, PendingObjectEdit>) => {
    const left = [...pendingEditsRef.current.keys()].filter((key) => !next.has(key));
    pendingEditsRef.current = next;
    setPendingEditsState(next);
    if (left.length === 0) return;
    // An edit that left the store, applied or discarded, is no longer one an
    // undo drops. A step left with nothing to undo goes.
    undoStepsRef.current = undoStepsRef.current.flatMap((step) => {
      const created = new Set([...step.created].filter((key) => !left.includes(key)));
      return step.replaced === null && created.size === 0
        ? []
        : [{ replaced: step.replaced, created }];
    });
  }, []);

  /** Record one step of the undo history. */
  const recordUndoStep = useCallback((step: UndoStep) => {
    undoStepsRef.current = [...undoStepsRef.current, step].slice(-UNDO_LIMIT);
  }, []);

  /** Empty the undo history when the page adopts a different draft. */
  const forgetUndoHistory = useCallback(() => {
    undoStepsRef.current = [];
    acceptedStateRef.current = null;
  }, []);

  /** Drop every pending edit when the page adopts a different draft. */
  const dropPendingEdits = useCallback(() => {
    latestObjectEditRef.current = new Map();
    setPendingEdits(NO_PENDING_EDITS);
    setPendingEditVerdicts(null);
  }, [setPendingEdits]);

  /** No answer still on its way for the edit under `key` is held: the edit was
   *  applied or discarded since the request was made. */
  const supersedeObjectEdits = useCallback((key: string) => {
    latestObjectEditRef.current.set(key, ++objectEditSequenceRef.current);
  }, []);

  /** Hold what the backend said about the pending edits `edits` over `draft`,
   *  beside what it said earlier about the same edits over the same draft. */
  const holdPendingEditOutcomes = useCallback(
    (
      draft: BuilderVisualDraftEnvelope,
      edits: ReadonlyMap<string, PendingObjectEdit>,
      said: ReadonlyArray<readonly [string, BuilderPendingEditOutcome]>,
    ) => {
      setPendingEditVerdicts((current) => {
        const outcomes = new Map(
          current !== null && current.draft === draft && current.edits === edits
            ? current.outcomes
            : NO_OUTCOMES,
        );
        for (const [key, outcome] of said) outcomes.set(key, outcome);
        return { draft, edits, outcomes };
      });
    },
    [],
  );

  /** The YAML buffer and the session pane change the session as a whole, so
   *  they wait until no link or routing edit is pending. */
  const refuseWhileEditsPending = useCallback((gesture: string) => {
    const count = pendingEditsRef.current.size;
    if (count > 0) {
      throw new Error(`apply or discard the ${pendingEditsPhrase(count)} before ${gesture}`);
    }
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

  /** Fill the on-screen compile slots from one compile, or clear them and
   *  show `failure` when a request produced no compile. */
  const showCompile = useCallback(
    (compile: BuilderCompileResult | null, failure: string | null = null) => {
      const issues = compile?.issues ?? [];
      const message = failure ?? issues.find((issue) => issue.severity === "error")?.message ?? null;
      setCompileResult(compile);
      setCompileIssues(issues);
      setWorld(compile?.resolved_preview ?? null);
      setResolveError((current) =>
        message === null ? null : current?.error === message ? current : { error: message },
      );
    },
    [],
  );

  const adoptCompileResult = useCallback((result: BuilderVisualDraftAssemblyResult) => {
    const compile = result.compile_result;
    const accepted = acceptedStateOf(result.visual_draft);
    if (accepted !== null) acceptedStateRef.current = accepted;
    setVisualDraft(result.visual_draft);
    setAssemblyResult(result);
    setDocumentYaml(compile.canonical_session_yaml ?? null);
    setSettledDocumentDigest(compile.digests?.document ?? null);
    setSettledDependencySha256(compile.digests?.dependency ?? null);
    settledDigestsRef.current = {
      document: compile.digests?.document ?? null,
      dependency: compile.digests?.dependency ?? null,
    };
    showCompile(compile);
  }, [setVisualDraft, showCompile]);

  /** Adopt the result of an accepted change, and record the accepted state it
   *  replaced as one undo step. A result that is not fully accepted records
   *  nothing; the change that completes it records the state both replaced. */
  const adoptAcceptedChange = useCallback(
    (result: BuilderVisualDraftAssemblyResult) => {
      const replaced = acceptedStateRef.current;
      adoptCompileResult(result);
      if (replaced !== null && acceptedStateRef.current !== replaced) {
        recordUndoStep({ replaced, created: NO_KEYS });
      }
    },
    [adoptCompileResult, recordUndoStep],
  );

  const createDraft = useCallback(
    async (request: BuilderVisualDraftCreateRequest): Promise<BuilderVisualDraftEnvelope> => {
      beginEpoch();
      return enqueueWriter("create", async (epoch) => {
        const draft = await createVisualDraft(request);
        assertEpoch(epoch);
        const assembled = await compileVisualDraft({ draft });
        assertEpoch(epoch);
        setOpenedSession(null);
        forgetUndoHistory();
        adoptCompileResult(assembled);
        dropPendingEdits();
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
    [
      adoptCompileResult,
      assertEpoch,
      beginEpoch,
      dropPendingEdits,
      enqueueWriter,
      forgetUndoHistory,
      setYamlBuffer,
    ],
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
          forgetUndoHistory();
          adoptCompileResult(assembled);
          dropPendingEdits();
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
    [
      adoptCompileResult,
      assertEpoch,
      beginEpoch,
      dropPendingEdits,
      enqueueWriter,
      forgetUndoHistory,
      setYamlBuffer,
    ],
  );

  const editYamlBuffer = useCallback((text: string) => {
    if (writerRef.current !== null && writerRef.current !== "yaml") return false;
    if (pendingEditsRef.current.size > 0) return false;
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
      refuseWhileEditsPending("applying the YAML");
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
      adoptAcceptedChange(assembled);
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
    }), [
      adoptAcceptedChange,
      assertEpoch,
      enqueueWriter,
      refuseWhileEditsPending,
      setVisualDraft,
      setYamlBuffer,
    ]);

  const useCanonicalYaml = useCallback(async () => {
    const canonical = assemblyResult?.compile_result.canonical_session_yaml;
    if (!canonical) throw new Error("canonical session YAML is not available");
    refuseWhileEditsPending("applying the YAML");
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
  }, [
    applyYamlBuffer,
    assemblyResult?.compile_result.canonical_session_yaml,
    refuseWhileEditsPending,
    setYamlBuffer,
  ]);

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
        showCompile(null, cause instanceof Error ? cause.message : String(cause));
        throw cause;
      }
      adoptAcceptedChange(result);
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
    }), [adoptAcceptedChange, assertEpoch, enqueueWriter, setYamlBuffer, showCompile]);

  /** Put the accepted draft's compile back in the on-screen slots once no
   *  window edit is being previewed. */
  const showAcceptedCompile = useCallback(() => {
    if (assemblyResult) showCompile(assemblyResult.compile_result);
  }, [assemblyResult, showCompile]);

  /** The page's preview of unapplied window edits: the backend compiles the
   *  workspace over the draft and accepts nothing. The result fills only the
   *  on-screen slots, and only while the draft and the caller's edits are the
   *  ones it was computed for. */
  const compilePreview = useCallback(
    async (workspace: BuilderVisualWorkspace, isCurrent: () => boolean): Promise<void> => {
      const draft = visualDraftRef.current;
      if (!draft) throw new Error("there is no session draft to preview");
      const stillCurrent = () => visualDraftRef.current === draft && isCurrent();
      let result: BuilderVisualDraftAssemblyResult;
      try {
        result = await compileVisualDraft({ draft, workspace });
      } catch (cause) {
        if (stillCurrent()) showCompile(null, cause instanceof Error ? cause.message : String(cause));
        return;
      }
      if (stillCurrent()) showCompile(result.compile_result);
    },
    [showCompile],
  );

  /** The page's preview of the pending link rule, routing domain and boundary
   *  edits: the backend compiles the accepted session with the edits applied
   *  and accepts nothing. The result fills only the on-screen slots, and only
   *  while the accepted draft and the pending edits are the ones it was
   *  computed for. Its outcome for each edit is held for those edits, so a
   *  stale edit shows as stale before anyone applies it. */
  const previewPendingEdits = useCallback(async (): Promise<void> => {
    const draft = visualDraftRef.current;
    const edits = pendingEditsRef.current;
    if (!draft) throw new Error("there is no session draft to preview");
    const accepted = draft.projection_status === "applied" ? (draft.applied_session ?? null) : null;
    if (accepted === null) {
      showCompile(
        null,
        "pending link and routing edits are previewed only on a fully accepted session",
      );
      return;
    }
    const stillCurrent = () =>
      visualDraftRef.current === draft && pendingEditsRef.current === edits;
    let result: BuilderCompileResult;
    try {
      result = await compileBuilderDraft({
        draft: {
          draft_revision: draft.draft_revision,
          state: { session: accepted, catalog_documents: draft.catalog_documents },
        },
        target_ref: draft.target_ref,
        pending_edits: [...edits.values()].map((pending) => pending.edit),
      });
    } catch (cause) {
      if (stillCurrent()) showCompile(null, cause instanceof Error ? cause.message : String(cause));
      return;
    }
    if (!stillCurrent()) return;
    const keys = [...edits.keys()];
    if (result.pending_edit_outcomes.length !== keys.length) {
      showCompile(null, "the preview did not answer one outcome per pending edit");
      return;
    }
    showCompile(result);
    holdPendingEditOutcomes(
      draft,
      edits,
      keys.map((key, index) => [key, result.pending_edit_outcomes[index]!] as const),
    );
  }, [holdPendingEditOutcomes, showCompile]);

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
        adoptAcceptedChange(assembled);
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
    [adoptAcceptedChange, assertEpoch, enqueueWriter, setYamlBuffer],
  );

  /** Run one command with every pending edit sent beside it: the command mints
   *  no id an edit holds, and a command that starts from a pending edit is
   *  evaluated with it. A complete result is the next accepted revision, and
   *  the edit the command started from leaves the store. An incomplete result
   *  accepts nothing: each edit it created is held under its address with the
   *  backend's description of it, in the place of the edit the command started
   *  from. A created edit whose address holds another pending edit, or one
   *  that changed while the command ran, is refused and nothing is held.
   *  Either result is one undo step; undoing an incomplete one drops the edits
   *  it created. */
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
        const sent = pendingEditsRef.current;
        const startKey = commandStartEditKey(command);
        const result = await applyVisualDraftCommand({
          draft,
          expected_draft_revision: draft.draft_revision,
          command,
          pending_edits: [...sent.values()].map((pending) => pending.edit),
        });
        assertEpoch(epoch);
        if (result.created_edits.length > 0) {
          const appliedRevision = draft.applied_revision ?? null;
          if (appliedRevision === null) {
            throw new Error("the backend created pending edits on a draft with no accepted revision");
          }
          const described = await Promise.all(
            result.created_edits.map((edit) =>
              editVisualDraftObject({
                target_ref: draft.target_ref,
                applied_revision: appliedRevision,
                address: edit.address,
                working: edit.working,
              }),
            ),
          );
          assertEpoch(epoch);
          const held = new Map(pendingEditsRef.current);
          const created = result.created_edits.map((edit, index) => {
            const key = pendingEditKey(edit.address);
            if (held.get(key) !== (key === startKey ? sent.get(key) : undefined)) {
              const object =
                edit.address.kind === "member" ? edit.address.id : "the boundary list";
              throw new Error(
                `the ${result.operation} command created an edit of ${object}, which holds ` +
                  "another pending edit; apply or discard that edit first",
              );
            }
            held.set(key, { edit, answer: described[index]! });
            return key;
          });
          for (const key of created) supersedeObjectEdits(key);
          setPendingEdits(held);
          recordUndoStep({ replaced: null, created: new Set(created) });
          return result;
        }
        const assembled = await compileVisualDraft({ draft: result.draft });
        assertEpoch(epoch);
        adoptAcceptedChange(assembled);
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
        // The accepted revision holds the edit the command started from,
        // unless that edit changed while the command ran.
        if (
          startKey !== null &&
          sent.has(startKey) &&
          pendingEditsRef.current.get(startKey) === sent.get(startKey)
        ) {
          const remaining = new Map(pendingEditsRef.current);
          remaining.delete(startKey);
          supersedeObjectEdits(startKey);
          setPendingEdits(remaining);
        }
        return result;
      }),
    [
      adoptAcceptedChange,
      assertEpoch,
      enqueueWriter,
      recordUndoStep,
      setPendingEdits,
      setYamlBuffer,
      supersedeObjectEdits,
    ],
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
        refuseWhileEditsPending("editing the session fields");
        const result = await mutateVisualDraftControls({
          draft,
          expected_draft_revision: draft.draft_revision,
          commands,
        });
        assertEpoch(epoch);
        adoptAcceptedChange(result);
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
    [adoptAcceptedChange, assertEpoch, enqueueWriter, refuseWhileEditsPending, setYamlBuffer],
  );

  /** Edit one link rule, one routing domain or the boundary list without
   *  accepting it. The first edit of an object starts from the object in the
   *  accepted session; a later one builds on the working JSON of its latest
   *  answer. The backend applies the card value or field-editor batch and
   *  answers with the new working JSON, the card value that writes it back, the
   *  field editor over it and its issues. That answer is held as the object's
   *  pending edit when it answers the latest request for the object. */
  const editPendingObject = useCallback(
    async (
      address: PendingEditAddress,
      change: NonNullable<BuilderVisualObjectEditRequest["change"]>,
    ): Promise<BuilderVisualObjectEditResult> => {
      const epoch = epochRef.current;
      const { draft, appliedRevision, key, edit } = objectEditBasis(
        visualDraftRef.current,
        pendingEditsRef.current,
        address,
      );
      if (yamlBlocksGraphicalEditing(true, yamlBufferRef.current)) {
        throw new Error("apply or canonicalize the YAML buffer before editing graphically");
      }
      const request = ++objectEditSequenceRef.current;
      latestObjectEditRef.current.set(key, request);
      const answer = await editVisualDraftObject({
        target_ref: draft.target_ref,
        applied_revision: appliedRevision,
        address,
        working: edit.working,
        change,
      });
      assertEpoch(epoch);
      if (latestObjectEditRef.current.get(key) !== request) throw new SupersededSessionOperation();
      if (yamlBlocksGraphicalEditing(true, yamlBufferRef.current)) {
        throw new Error("the YAML buffer changed while this edit was made; apply it first");
      }
      setPendingEdits(
        new Map(pendingEditsRef.current).set(key, {
          edit: { ...edit, working: answer.working },
          answer,
        }),
      );
      return answer;
    },
    [assertEpoch, setPendingEdits],
  );

  /** Describe one link rule, one routing domain or the boundary list as it now
   *  stands, without changing it: the backend answers for its pending edit's
   *  working JSON, or for the object in the accepted session when no edit of it
   *  is pending, with the field editor over that JSON at the accepted revision.
   *  Nothing is held. */
  const describePendingObject = useCallback(
    async (address: PendingEditAddress): Promise<BuilderVisualObjectEditResult> => {
      const epoch = epochRef.current;
      const { draft, appliedRevision, edit } = objectEditBasis(
        visualDraftRef.current,
        pendingEditsRef.current,
        address,
      );
      const answer = await editVisualDraftObject({
        target_ref: draft.target_ref,
        applied_revision: appliedRevision,
        address,
        working: edit.working,
      });
      assertEpoch(epoch);
      return answer;
    },
    [assertEpoch],
  );

  /** Adopt an accepted revision of link and routing edits as one undo step:
   *  its compile, and the YAML buffer as the canonical text of the new
   *  accepted session. */
  const adoptAcceptedEdits = useCallback(
    (assembly: BuilderVisualDraftAssemblyResult) => {
      adoptAcceptedChange(assembly);
      const canonical =
        assembly.compile_result.canonical_session_yaml ?? assembly.visual_draft.session_yaml;
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
    },
    [adoptAcceptedChange, setYamlBuffer],
  );

  /** The draft that link and routing edits are applied to, with its accepted
   *  session. The apply route requires an accepted session, and graphical
   *  edits wait for an applied YAML buffer. */
  const acceptedDraftForEdits = useCallback((): {
    draft: BuilderVisualDraftEnvelope;
    accepted: Readonly<Record<string, JsonValue>>;
  } => {
    const draft = visualDraftRef.current;
    if (!draft) throw new Error("there is no visual draft to edit");
    const accepted = draft.projection_status === "applied" ? (draft.applied_session ?? null) : null;
    if (accepted === null) {
      throw new Error("link rules, routing domains and boundaries are edited on an accepted session");
    }
    if (yamlBlocksGraphicalEditing(true, yamlBufferRef.current)) {
      throw new Error("apply or canonicalize the YAML buffer before editing graphically");
    }
    return { draft, accepted };
  }, []);

  /** Accept the pending edits held under `keys` as the next revision, through
   *  the route that applies them, and adopt only its answer. Each key in
   *  `reapply` names a stale edit the user chose to keep: it is first taken
   *  against the object the accepted session now holds, and stays so taken.
   *  One stale or invalid edit refuses the whole batch: nothing is accepted,
   *  every edit stays pending, and the backend's outcome for each is held. An
   *  applied edit leaves the store unless it changed while the apply ran. */
  const applyPendingEdits = useCallback(
    (
      keys: ReadonlyArray<string>,
      reapply: ReadonlySet<string> = NO_KEYS,
    ): Promise<BuilderVisualDraftApplyEditsResult> =>
      enqueueWriter("edits", async (epoch) => {
        const { draft, accepted } = acceptedDraftForEdits();
        let held = pendingEditsRef.current;
        if (reapply.size > 0) {
          const rebased = new Map(held);
          for (const key of reapply) {
            const pending = held.get(key);
            if (!pending) throw new Error(`no pending edit is held for ${key}`);
            rebased.set(key, { ...pending, edit: rebasePendingEdit(accepted, pending.edit) });
          }
          setPendingEdits(rebased);
          held = rebased;
        }
        const sent = keys.map((key) => {
          const pending = held.get(key);
          if (!pending) throw new Error(`no pending edit is held for ${key}`);
          return [key, pending] as const;
        });
        const result = await applyVisualDraftEdits({
          draft,
          expected_draft_revision: draft.draft_revision,
          edits: sent.map(([, pending]) => pending.edit),
        });
        assertEpoch(epoch);
        if (result.outcomes.length !== sent.length) {
          throw new Error("the backend did not answer one outcome per pending edit");
        }
        if (!result.applied) {
          if (visualDraftRef.current === draft && pendingEditsRef.current === held) {
            holdPendingEditOutcomes(
              draft,
              held,
              sent.map(([key], index) => [key, result.outcomes[index]!] as const),
            );
          }
          return result;
        }
        if (!result.assembly) {
          throw new Error("the backend applied the pending edits and returned no accepted revision");
        }
        adoptAcceptedEdits(result.assembly);
        const remaining = new Map(pendingEditsRef.current);
        for (const [key, pending] of sent) {
          if (remaining.get(key) !== pending) continue;
          remaining.delete(key);
          supersedeObjectEdits(key);
        }
        setPendingEdits(remaining);
        return result;
      }),
    [
      acceptedDraftForEdits,
      adoptAcceptedEdits,
      assertEpoch,
      enqueueWriter,
      holdPendingEditOutcomes,
      setPendingEdits,
      supersedeObjectEdits,
    ],
  );

  /** Drop one object's pending edit. An answer still on its way for it is not
   *  held. */
  const discardPendingEdit = useCallback(
    (address: PendingEditAddress) => {
      const key = pendingEditKey(address);
      supersedeObjectEdits(key);
      if (!pendingEditsRef.current.has(key)) return;
      const remaining = new Map(pendingEditsRef.current);
      remaining.delete(key);
      setPendingEdits(remaining);
    },
    [setPendingEdits, supersedeObjectEdits],
  );

  /** Remove one link rule or routing domain from the accepted session at once,
   *  as one accepted revision and one undo step, through the route that
   *  applies pending edits.
   *  The removal is a list edit: the list's ids now, and those ids without
   *  this one. It waits while an edit of the object is pending. A refusal
   *  accepts nothing and is thrown with the backend's reasons. */
  const removeListMember = useCallback(
    (
      listPointer: BuilderListEdit["list_pointer"],
      id: string,
    ): Promise<BuilderVisualDraftAssemblyResult> =>
      enqueueWriter("edits", async (epoch) => {
        const { draft, accepted } = acceptedDraftForEdits();
        const ownEdit = pendingEditKey({ kind: "member", list_pointer: listPointer, id });
        if (pendingEditsRef.current.has(ownEdit)) {
          throw new Error(`apply or discard the pending edit of ${id} before removing it`);
        }
        const idsBefore = acceptedMemberIds(accepted, listPointer);
        if (!idsBefore.includes(id)) {
          throw new Error(`the accepted session holds no ${id} in ${listPointer}`);
        }
        const result = await applyVisualDraftEdits({
          draft,
          expected_draft_revision: draft.draft_revision,
          edits: [
            {
              kind: "list",
              list_pointer: listPointer,
              ids_before: idsBefore,
              ids_wanted: idsBefore.filter((member) => member !== id),
            },
          ],
        });
        assertEpoch(epoch);
        if (!result.applied) throw new Error(refusalMessage(result));
        if (!result.assembly) {
          throw new Error("the backend applied the removal and returned no accepted revision");
        }
        adoptAcceptedEdits(result.assembly);
        return result.assembly;
      }),
    [acceptedDraftForEdits, adoptAcceptedEdits, assertEpoch, enqueueWriter],
  );

  /** Undo the newest step of the undo history. A step that replaced an
   *  accepted session re-accepts that session with its proposals as the next
   *  revision, through the route that accepts a stored state, and the page
   *  adopts only its answer. The request carries the draft as it now stands,
   *  so the allocation history keeps every id and undo frees none. The pending
   *  edits the step's change created are dropped. Other pending edits stay;
   *  the preview shows each one whose object changed as stale. A refusal
   *  changes nothing and is thrown with the backend's reason. Resolves the
   *  accepted revision, or null when the step accepted nothing and undo only
   *  dropped the edits it created. */
  const undo = useCallback(
    (): Promise<BuilderVisualDraftAssemblyResult | null> =>
      enqueueWriter("undo", async (epoch) => {
        const draft = visualDraftRef.current;
        if (!draft) throw new Error("there is no session draft to undo");
        if (yamlBlocksGraphicalEditing(true, yamlBufferRef.current)) {
          throw new Error("apply or canonicalize the YAML buffer before undoing");
        }
        const newest = undoStepsRef.current[undoStepsRef.current.length - 1];
        if (newest === undefined) throw new Error("there is no accepted change to undo");
        let assembly: BuilderVisualDraftAssemblyResult | null = null;
        if (newest.replaced !== null) {
          assembly = await acceptVisualDraftState({
            draft,
            expected_draft_revision: draft.draft_revision,
            state: newest.replaced,
          });
          assertEpoch(epoch);
          adoptCompileResult(assembly);
          const canonical =
            assembly.compile_result.canonical_session_yaml ?? assembly.visual_draft.session_yaml;
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
        }
        // Only writers record steps, so the newest step is still the newest.
        // An edit that left the store while the request ran is already off it.
        const steps = undoStepsRef.current;
        const undone = steps[steps.length - 1]!;
        undoStepsRef.current = steps.slice(0, -1);
        if (undone.created.size > 0) {
          const remaining = new Map(pendingEditsRef.current);
          for (const key of undone.created) {
            remaining.delete(key);
            supersedeObjectEdits(key);
          }
          setPendingEdits(remaining);
        }
        return assembly;
      }),
    [
      adoptCompileResult,
      assertEpoch,
      enqueueWriter,
      setPendingEdits,
      setYamlBuffer,
      supersedeObjectEdits,
    ],
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
          // The reopen records no undo step. A save under a new name adopts
          // a different draft, whose history starts empty.
          if (reopened.target_ref !== visualDraftRef.current?.target_ref) forgetUndoHistory();
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
    [
      adoptCompileResult,
      assertEpoch,
      captureIsCurrent,
      enqueueWriter,
      forgetUndoHistory,
      refreshSessions,
      setYamlBuffer,
    ],
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
    forgetUndoHistory();
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
  }, [adoptCompileResult, beginEpoch, forgetUndoHistory, setYamlBuffer]);

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
    dropPendingEdits();
    forgetUndoHistory();
    resetCompileFacts();
  }, [
    beginEpoch,
    dropPendingEdits,
    forgetUndoHistory,
    resetCompileFacts,
    setVisualDraft,
    setYamlBuffer,
  ]);

  // The backend's outcome for each pending edit, while it was said about the
  // accepted draft and the pending edits the page now holds.
  const pendingEditOutcomes =
    pendingEditVerdicts !== null &&
    pendingEditVerdicts.draft === visualDraft &&
    pendingEditVerdicts.edits === pendingEdits
      ? pendingEditVerdicts.outcomes
      : NO_OUTCOMES;

  return {
    sessions,
    sessionsError,
    openedSession,
    visualDraft,
    yamlBuffer,
    pendingEdits,
    pendingEditOutcomes,
    writer,
    assemblyResult,
    world,
    documentYaml,
    compilePreview,
    previewPendingEdits,
    showAcceptedCompile,
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
    editPendingObject,
    describePendingObject,
    applyPendingEdits,
    discardPendingEdit,
    removeListMember,
    undo,
    prepareRetarget,
    captureCoordinator,
    captureIsCurrent,
    saveSession,
    adoptCommittedRetarget,
    deploySession,
    refreshSessions,
    clear,
  };
}
