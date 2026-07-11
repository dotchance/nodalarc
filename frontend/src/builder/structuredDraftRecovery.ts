/** Lossless browser recovery for structured Builder application state. */

import {
  BUILDER_VISUAL_DRAFT_ENVELOPE_RUNTIME_DESCRIPTOR,
  BUILDER_VISUAL_GROUND_DRAFT_RUNTIME_DESCRIPTOR,
  BUILDER_VISUAL_LINK_RULE_RUNTIME_DESCRIPTOR,
  BUILDER_VISUAL_ROUTING_BOUNDARY_RUNTIME_DESCRIPTOR,
  BUILDER_VISUAL_ROUTING_DOMAIN_RUNTIME_DESCRIPTOR,
  BUILDER_VISUAL_SPACE_DRAFT_RUNTIME_DESCRIPTOR,
  BUILDER_VISUAL_WORKSPACE_RUNTIME_DESCRIPTOR,
  CATALOG_COMPONENT_DRAFT_ENVELOPE_RUNTIME_DESCRIPTOR,
  type BuilderIssue,
  type BuilderVisualDraftEnvelope,
  type BuilderVisualRuntimeDescriptor,
  type CatalogComponentDraftEnvelope,
  type JsonValue,
} from "./generated/builderApi";
import {
  targetKey,
  type EditorTarget,
  type EditorWindow,
} from "./useEditorWindows";
import { overlayBuffers, type BufferMap } from "./useWorkspace";
import type { Workspace } from "./workspace";

export const STRUCTURED_RECOVERY_VERSION = 2;
export const CATALOG_DRAFT_RECOVERY_VERSION = 2;
const RECOVERY_STORAGE_PREFIX = "nodalarc-builder-recovery-v2";

export interface RecoveryStorageScope {
  authoringContextBinding: string;
  tabBinding: string;
}

export type StructuredRecoverySlot = "autosave" | "backup";

let pageBinding: string | null = null;

export function getRecoveryTabBinding(): string {
  pageBinding ??=
    globalThis.crypto?.randomUUID?.() ??
    `page-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  return pageBinding;
}

function recoveryScopePrefix(scope: RecoveryStorageScope): string {
  return `${RECOVERY_STORAGE_PREFIX}:${encodeURIComponent(scope.authoringContextBinding)}:${encodeURIComponent(scope.tabBinding)}`;
}

export function recoveryStorageKey(
  scope: RecoveryStorageScope,
  kind: StructuredRecoverySlot | "catalog",
  targetRef: string,
): string {
  return `${recoveryScopePrefix(scope)}:${kind}:${encodeURIComponent(targetRef)}`;
}

function currentTargetKey(
  scope: RecoveryStorageScope,
  kind: StructuredRecoverySlot | "catalog",
): string {
  return `${RECOVERY_STORAGE_PREFIX}:${encodeURIComponent(scope.authoringContextBinding)}:current:${kind}`;
}

interface RecoveryPointer {
  targetRef: string;
  storageKey: string;
}

function currentRecoveryPointer(
  scope: RecoveryStorageScope,
  kind: StructuredRecoverySlot | "catalog",
): RecoveryPointer | null {
  const raw = sessionStorage.getItem(currentTargetKey(scope, kind));
  if (!raw) return null;
  try {
    const value: unknown = JSON.parse(raw);
    const expectedPrefix = `${RECOVERY_STORAGE_PREFIX}:${encodeURIComponent(scope.authoringContextBinding)}:`;
    return isRecord(value) &&
      typeof value.targetRef === "string" &&
      typeof value.storageKey === "string" &&
      value.storageKey.startsWith(expectedPrefix)
      ? { targetRef: value.targetRef, storageKey: value.storageKey }
      : null;
  } catch {
    return null;
  }
}

function setCurrentRecoveryPointer(
  scope: RecoveryStorageScope,
  kind: StructuredRecoverySlot | "catalog",
  targetRef: string,
  storageKey: string,
): void {
  sessionStorage.setItem(
    currentTargetKey(scope, kind),
    JSON.stringify({ targetRef, storageKey }),
  );
}

export interface StructuredEditorRecoveryState {
  windows: EditorWindow[];
  buffers: BufferMap;
}

export interface StructuredDraftRecovery {
  authoringContextBinding: string;
  workspace: Workspace;
  visualDraft: BuilderVisualDraftEnvelope;
  yaml: StructuredYamlRecoveryState;
  editor: StructuredEditorRecoveryState;
}

export interface StructuredYamlRecoveryState {
  text: string;
  appliedText: string;
  generation: number;
  canonicalizationRequired: boolean;
  canonicalizationAccepted: boolean;
  issues: readonly BuilderIssue[];
}

export type StructuredRecoveryReadResult =
  | { ok: true; recovery: StructuredDraftRecovery }
  | { ok: false; reason: string };

export type StructuredStashOutcome = "stashed" | "skipped" | "refused";

export interface CatalogDraftEditorRecovery {
  draft: CatalogComponentDraftEnvelope;
  baselineDocument: Readonly<Record<string, JsonValue>>;
  workingDocument: Readonly<Record<string, JsonValue>>;
  yamlText: string;
  appliedYamlText: string;
  canonicalizationRequired: boolean;
  canonicalizationAccepted: boolean;
}

interface SerializedCatalogDraftEditorRecovery {
  authoring_context_binding: string;
  draft: CatalogComponentDraftEnvelope;
  baseline_document: Readonly<Record<string, JsonValue>>;
  working_document: Readonly<Record<string, JsonValue>>;
  yaml_text: string;
  applied_yaml_text: string;
  canonicalization_required: boolean;
  canonicalization_accepted: boolean;
}

export type CatalogDraftRecoveryReadResult =
  | { ok: true; recovery: CatalogDraftEditorRecovery }
  | { ok: false; reason: string };

const isRecord = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);
const hasOwn = (value: Record<string, unknown>, key: string): boolean =>
  Object.prototype.hasOwnProperty.call(value, key);

function jsonEquals(left: JsonValue, right: JsonValue): boolean {
  if (left === right) return true;
  if (Array.isArray(left) || Array.isArray(right)) {
    return (
      Array.isArray(left) &&
      Array.isArray(right) &&
      left.length === right.length &&
      left.every((item, index) => jsonEquals(item, right[index]!))
    );
  }
  if (!isRecord(left) || !isRecord(right)) return false;
  const leftKeys = Object.keys(left);
  const rightKeys = Object.keys(right);
  return (
    leftKeys.length === rightKeys.length &&
    leftKeys.every(
      (key) =>
        hasOwn(right, key) &&
        jsonEquals(left[key] as JsonValue, right[key] as JsonValue),
    )
  );
}

function isJsonValue(value: unknown): value is JsonValue {
  if (
    value === null ||
    typeof value === "boolean" ||
    typeof value === "string"
  ) {
    return true;
  }
  if (typeof value === "number") return Number.isFinite(value);
  if (Array.isArray(value)) return value.every(isJsonValue);
  return isRecord(value) && Object.values(value).every(isJsonValue);
}

function matchesArrayBounds(
  value: readonly unknown[],
  descriptor: {
    readonly min_items?: number;
    readonly max_items?: number;
    readonly unique?: boolean;
  },
): boolean {
  if (descriptor.min_items !== undefined && value.length < descriptor.min_items) {
    return false;
  }
  if (descriptor.max_items !== undefined && value.length > descriptor.max_items) {
    return false;
  }
  if (descriptor.unique) {
    return value.every(
      (item, index) =>
        value.findIndex(
          (candidate) =>
            isJsonValue(item) &&
            isJsonValue(candidate) &&
            jsonEquals(item, candidate),
        ) === index,
    );
  }
  return true;
}

function matchesNumericBounds(
  value: number,
  descriptor: Extract<
    BuilderVisualRuntimeDescriptor,
    { readonly kind: "integer" | "number" }
  >,
): boolean {
  if (descriptor.minimum !== undefined && value < descriptor.minimum) return false;
  if (descriptor.maximum !== undefined && value > descriptor.maximum) return false;
  if (
    descriptor.exclusive_minimum !== undefined &&
    value <= descriptor.exclusive_minimum
  ) {
    return false;
  }
  if (
    descriptor.exclusive_maximum !== undefined &&
    value >= descriptor.exclusive_maximum
  ) {
    return false;
  }
  if (descriptor.multiple_of !== undefined) {
    const quotient = value / descriptor.multiple_of;
    const tolerance = Number.EPSILON * Math.max(1, Math.abs(quotient)) * 4;
    if (Math.abs(quotient - Math.round(quotient)) > tolerance) return false;
  }
  return true;
}

export function matchesRuntimeDescriptor(
  value: unknown,
  descriptor: BuilderVisualRuntimeDescriptor,
): boolean {
  switch (descriptor.kind) {
    case "json":
      return isJsonValue(value);
    case "literal":
      return isJsonValue(value) && jsonEquals(value, descriptor.value);
    case "enum":
      return (
        isJsonValue(value) &&
        descriptor.values.some((candidate) => jsonEquals(value, candidate))
      );
    case "null":
      return value === null;
    case "boolean":
      return typeof value === "boolean";
    case "string": {
      if (typeof value !== "string") return false;
      const length = [...value].length;
      return (
        (descriptor.min_length === undefined || length >= descriptor.min_length) &&
        (descriptor.max_length === undefined || length <= descriptor.max_length) &&
        (descriptor.pattern === undefined || new RegExp(descriptor.pattern).test(value))
      );
    }
    case "integer":
    case "number":
      return (
        typeof value === "number" &&
        Number.isFinite(value) &&
        (descriptor.kind !== "integer" || Number.isInteger(value)) &&
        matchesNumericBounds(value, descriptor)
      );
    case "array":
      return (
        Array.isArray(value) &&
        matchesArrayBounds(value, descriptor) &&
        value.every((item) => matchesRuntimeDescriptor(item, descriptor.items))
      );
    case "tuple": {
      if (!Array.isArray(value) || !matchesArrayBounds(value, descriptor)) return false;
      if (descriptor.rest === false && value.length > descriptor.items.length) return false;
      if (
        !descriptor.items.slice(0, value.length).every((item, index) =>
          matchesRuntimeDescriptor(value[index], item),
        )
      ) {
        return false;
      }
      const rest = descriptor.rest;
      return (
        rest === false ||
        value
          .slice(descriptor.items.length)
          .every((item) => matchesRuntimeDescriptor(item, rest))
      );
    }
    case "union": {
      const matches = descriptor.options.filter((option) =>
        matchesRuntimeDescriptor(value, option),
      ).length;
      return descriptor.exclusive ? matches === 1 : matches > 0;
    }
    case "intersection":
      return descriptor.options.every((option) =>
        matchesRuntimeDescriptor(value, option),
      );
    case "object": {
      if (!isRecord(value)) return false;
      if (
        !Object.entries(descriptor.fields).every(
          ([key, field]) =>
            hasOwn(value, key) && matchesRuntimeDescriptor(value[key], field),
        )
      ) {
        return false;
      }
      for (const [key, candidate] of Object.entries(value)) {
        if (hasOwn(descriptor.fields, key)) continue;
        const matchingPatterns = (descriptor.patterns ?? []).filter(({ pattern }) =>
          new RegExp(pattern).test(key),
        );
        if (matchingPatterns.length > 0) {
          if (
            !matchingPatterns.every(({ values }) =>
              matchesRuntimeDescriptor(candidate, values),
            )
          ) {
            return false;
          }
          continue;
        }
        if (
          descriptor.additional === false ||
          !matchesRuntimeDescriptor(candidate, descriptor.additional)
        ) {
          return false;
        }
      }
      return true;
    }
  }
}

function hasExactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  return (
    Object.keys(value).length === keys.length && keys.every((key) => hasOwn(value, key))
  );
}

function uniqueRefs(items: ReadonlyArray<{ readonly ref: string }>): boolean {
  return new Set(items.map((item) => item.ref)).size === items.length;
}

function isComponentRef(ref: string): boolean {
  return !ref.includes(":sessions/");
}

export function isVisualDraftEnvelope(value: unknown): value is BuilderVisualDraftEnvelope {
  if (
    !matchesRuntimeDescriptor(
      value,
      BUILDER_VISUAL_DRAFT_ENVELOPE_RUNTIME_DESCRIPTOR,
    )
  ) {
    return false;
  }
  const envelope = value as BuilderVisualDraftEnvelope;
  return (
    envelope.target_ref.startsWith("user:sessions/") &&
    (envelope.expected_session_revision === null ||
      envelope.expected_session_revision === undefined ||
      envelope.source_ref === envelope.target_ref) &&
    uniqueRefs(envelope.catalog_documents ?? []) &&
    (envelope.catalog_documents ?? []).every(
      (item) => item.ref.startsWith("user:") && isComponentRef(item.ref),
    ) &&
    new Set(envelope.reserved_authoring_ids).size ===
      envelope.reserved_authoring_ids.length &&
    envelope.reserved_authoring_ids.every(
      (authoringId) => authoringId.length > 0 && authoringId.length <= 160,
    ) &&
    typeof envelope.session_yaml === "string"
  );
}

const SESSION_BUFFER_KEYS = [
  "session_name",
  "start_time",
  "step_seconds",
  "compression",
  "max_pairs_per_rule",
  "max_pairs_per_tick",
] as const;

function matchesSessionBuffer(value: unknown, workspace: Workspace): boolean {
  if (!isRecord(value)) return false;
  return (
    hasExactKeys(value, SESSION_BUFFER_KEYS) &&
    matchesRuntimeDescriptor(
      { ...workspace, ...value },
      BUILDER_VISUAL_WORKSPACE_RUNTIME_DESCRIPTOR,
    )
  );
}

type EditableTargetDescriptor =
  | { target: EditorTarget; descriptor: BuilderVisualRuntimeDescriptor; identity: string }
  | { target: EditorTarget; descriptor: "session"; identity: null };

function editableTargetDescriptor(
  value: unknown,
  workspace: Workspace,
): EditableTargetDescriptor | null {
  if (!isRecord(value) || typeof value.kind !== "string") return null;
  if (value.kind === "session") {
    return hasExactKeys(value, ["kind"])
      ? {
          target: value as unknown as EditorTarget,
          descriptor: "session",
          identity: null,
        }
      : null;
  }
  if (
    !hasExactKeys(value, ["kind", "id"]) ||
    typeof value.id !== "string" ||
    value.id.length === 0
  ) {
    return null;
  }
  const target = value as unknown as EditorTarget;
  switch (value.kind) {
    case "segment":
      return workspace.space.some((draft) => draft.segment_id === value.id)
        ? {
            target,
            descriptor: BUILDER_VISUAL_SPACE_DRAFT_RUNTIME_DESCRIPTOR,
            identity: "segment_id",
          }
        : null;
    case "ground":
      return workspace.ground.some((draft) => draft.segment_id === value.id)
        ? {
            target,
            descriptor: BUILDER_VISUAL_GROUND_DRAFT_RUNTIME_DESCRIPTOR,
            identity: "segment_id",
          }
        : null;
    case "link":
      return workspace.links.some((draft) => draft.rule_id === value.id)
        ? {
            target,
            descriptor: BUILDER_VISUAL_LINK_RULE_RUNTIME_DESCRIPTOR,
            identity: "rule_id",
          }
        : null;
    case "domain":
      return workspace.routing_domains.some((draft) => draft.domain_id === value.id)
        ? {
            target,
            descriptor: BUILDER_VISUAL_ROUTING_DOMAIN_RUNTIME_DESCRIPTOR,
            identity: "domain_id",
          }
        : null;
    case "boundary":
      return workspace.boundaries.some((draft) => draft.boundary_id === value.id)
        ? {
            target,
            descriptor: BUILDER_VISUAL_ROUTING_BOUNDARY_RUNTIME_DESCRIPTOR,
            identity: "boundary_id",
          }
        : null;
    default:
      return null;
  }
}

function matchesObjectBuffer(
  value: unknown,
  descriptor: BuilderVisualRuntimeDescriptor,
  identity: string,
  id: string,
): boolean {
  return (
    isRecord(value) &&
    value[identity] === id &&
    matchesRuntimeDescriptor(value, descriptor)
  );
}

function isEditorRecovery(
  value: unknown,
  workspace: Workspace,
): value is StructuredEditorRecoveryState {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ["windows", "buffers"]) ||
    !Array.isArray(value.windows) ||
    !isRecord(value.buffers)
  ) {
    return false;
  }
  if (value.windows.length !== Object.keys(value.buffers).length) return false;
  const windowKeys = new Set<string>();
  for (const window of value.windows) {
    if (
      !isRecord(window) ||
      !hasExactKeys(window, ["key", "target", "x", "y"]) ||
      typeof window.key !== "string" ||
      window.key.length === 0 ||
      windowKeys.has(window.key) ||
      typeof window.x !== "number" ||
      !Number.isFinite(window.x) ||
      typeof window.y !== "number" ||
      !Number.isFinite(window.y)
    ) {
      return false;
    }
    const targetDescriptor = editableTargetDescriptor(window.target, workspace);
    if (!targetDescriptor || targetKey(targetDescriptor.target) !== window.key) return false;
    const candidate = value.buffers[window.key];
    if (
      !isRecord(candidate) ||
      !hasExactKeys(candidate, ["draft", "opened", "dirty"]) ||
      candidate.dirty !== true ||
      (targetDescriptor.descriptor === "session"
        ? !matchesSessionBuffer(candidate.draft, workspace) ||
          !matchesSessionBuffer(candidate.opened, workspace)
        : !matchesObjectBuffer(
            candidate.draft,
            targetDescriptor.descriptor,
            targetDescriptor.identity,
            (targetDescriptor.target as { id: string }).id,
          ) ||
          !matchesObjectBuffer(
            candidate.opened,
            targetDescriptor.descriptor,
            targetDescriptor.identity,
            (targetDescriptor.target as { id: string }).id,
          ))
    ) {
      return false;
    }
    windowKeys.add(window.key);
  }
  const buffers = value.buffers as BufferMap;
  return matchesRuntimeDescriptor(
    overlayBuffers(workspace, buffers),
    BUILDER_VISUAL_WORKSPACE_RUNTIME_DESCRIPTOR,
  );
}

function isCatalogDraftEnvelope(value: unknown): value is CatalogComponentDraftEnvelope {
  if (
    !matchesRuntimeDescriptor(
      value,
      CATALOG_COMPONENT_DRAFT_ENVELOPE_RUNTIME_DESCRIPTOR,
    )
  ) {
    return false;
  }
  const envelope = value as CatalogComponentDraftEnvelope;
  const expectedFamilyPath = `:${envelope.family}/`;
  const sourceRef = envelope.source_ref ?? null;
  const expectedSourceRevision = envelope.expected_source_revision ?? null;
  const sourceIsConsistent =
    sourceRef === null
      ? expectedSourceRevision === null
      : sourceRef.includes(expectedFamilyPath) && expectedSourceRevision !== null;
  return (
    envelope.target_ref.startsWith(`user:${envelope.family}/`) &&
    sourceIsConsistent &&
    envelope.issues.every((issue) => {
      const blocks = new Set(issue.blocks);
      if (blocks.size !== issue.blocks.length || blocks.size === 0) return false;
      if (issue.stage === "runtime_support") {
        return issue.blocks.length === 1 && issue.blocks[0] === "deploy";
      }
      return blocks.size === 2 && blocks.has("save") && blocks.has("deploy");
    })
  );
}

function isCatalogDraftEditorRecovery(
  value: unknown,
): value is SerializedCatalogDraftEditorRecovery {
  return (
    isRecord(value) &&
    isCatalogDraftEnvelope(value.draft) &&
    isRecord(value.baseline_document) &&
    isJsonValue(value.baseline_document) &&
    isRecord(value.working_document) &&
    isJsonValue(value.working_document) &&
    typeof value.yaml_text === "string" &&
    typeof value.applied_yaml_text === "string" &&
    typeof value.canonicalization_required === "boolean" &&
    typeof value.canonicalization_accepted === "boolean"
  );
}

export function createStructuredRecovery(input: {
  authoringContextBinding: string;
  workspace: Workspace | null;
  visualDraft: BuilderVisualDraftEnvelope | null;
  yaml: StructuredYamlRecoveryState;
  windows: readonly EditorWindow[];
  buffers: BufferMap;
}): StructuredDraftRecovery | null {
  if (!input.workspace || !input.visualDraft) return null;
  const buffers = Object.fromEntries(
    Object.entries(input.buffers)
      .filter(([, buffer]) => buffer.dirty)
      .map(([key, buffer]) => [key, structuredClone(buffer)]),
  );
  const keys = new Set(Object.keys(buffers));
  const windows = input.windows
    .filter((window) => keys.has(window.key))
    .map((window) => structuredClone(window));
  return {
    authoringContextBinding: input.authoringContextBinding,
    workspace: structuredClone(input.workspace),
    visualDraft: structuredClone(input.visualDraft),
    yaml: structuredClone(input.yaml),
    editor: { windows, buffers },
  };
}

export function serializeStructuredRecovery(recovery: StructuredDraftRecovery): string {
  return JSON.stringify({
    v: STRUCTURED_RECOVERY_VERSION,
    kind: "structured",
    authoring_context_binding: recovery.authoringContextBinding,
    workspace: recovery.workspace,
    visual_draft: recovery.visualDraft,
    yaml: recovery.yaml,
    editor: recovery.editor,
  });
}

function isStructuredYamlRecovery(value: unknown): value is StructuredYamlRecoveryState {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, [
      "text",
      "appliedText",
      "generation",
      "canonicalizationRequired",
      "canonicalizationAccepted",
      "issues",
    ]) ||
    typeof value.text !== "string" ||
    typeof value.appliedText !== "string" ||
    !Number.isSafeInteger(value.generation) ||
    (value.generation as number) < 0 ||
    typeof value.canonicalizationRequired !== "boolean" ||
    typeof value.canonicalizationAccepted !== "boolean" ||
    !Array.isArray(value.issues)
  ) {
    return false;
  }
  return value.issues.every(
    (issue) =>
      isRecord(issue) &&
      typeof issue.code === "string" &&
      typeof issue.stage === "string" &&
      typeof issue.severity === "string" &&
      typeof issue.message === "string",
  );
}

export function readStructuredRecovery(
  raw: string,
  expectedAuthoringContextBinding?: string,
): StructuredRecoveryReadResult {
  try {
    const value: unknown = JSON.parse(raw);
    if (!isRecord(value)) return { ok: false, reason: "the saved draft could not be read" };
    if (value.v !== STRUCTURED_RECOVERY_VERSION) {
      return { ok: false, reason: `draft recovery version ${String(value.v)} is not supported` };
    }
    if (
      Object.keys(value).length !== 7 ||
      value.kind !== "structured" ||
      typeof value.authoring_context_binding !== "string" ||
      value.authoring_context_binding.length === 0 ||
      (expectedAuthoringContextBinding !== undefined &&
        value.authoring_context_binding !== expectedAuthoringContextBinding) ||
      !matchesRuntimeDescriptor(
        value.workspace,
        BUILDER_VISUAL_WORKSPACE_RUNTIME_DESCRIPTOR,
      ) ||
      !isVisualDraftEnvelope(value.visual_draft) ||
      !isStructuredYamlRecovery(value.yaml) ||
      !isEditorRecovery(value.editor, value.workspace as Workspace)
    ) {
      return { ok: false, reason: "the saved structured draft is incomplete or invalid" };
    }
    return {
      ok: true,
      recovery: {
        authoringContextBinding: value.authoring_context_binding,
        workspace: structuredClone(value.workspace) as Workspace,
        visualDraft: structuredClone(value.visual_draft),
        yaml: structuredClone(value.yaml),
        editor: structuredClone(value.editor),
      },
    };
  } catch {
    return { ok: false, reason: "the saved draft could not be read" };
  }
}

export function writeStructuredAutosave(
  recovery: StructuredDraftRecovery,
  scope: RecoveryStorageScope,
): boolean {
  try {
    if (recovery.authoringContextBinding !== scope.authoringContextBinding) return false;
    const targetRef = recovery.visualDraft.target_ref;
    const storageKey = recoveryStorageKey(scope, "autosave", targetRef);
    localStorage.setItem(
      storageKey,
      serializeStructuredRecovery(recovery),
    );
    setCurrentRecoveryPointer(scope, "autosave", targetRef, storageKey);
    return true;
  } catch {
    return false;
  }
}

export function hasStructuredRecovery(
  slot: StructuredRecoverySlot,
  scope: RecoveryStorageScope,
): boolean {
  try {
    const pointer = currentRecoveryPointer(scope, slot);
    return pointer !== null && localStorage.getItem(pointer.storageKey) !== null;
  } catch {
    return false;
  }
}

export function stashStructuredRecovery(
  recovery: StructuredDraftRecovery | null,
  scope: RecoveryStorageScope,
  options?: { force?: boolean },
): StructuredStashOutcome {
  try {
    if (recovery && recovery.authoringContextBinding !== scope.authoringContextBinding) {
      return "refused";
    }
    const autosavePointer = currentRecoveryPointer(scope, "autosave");
    const candidate = recovery
      ? serializeStructuredRecovery(recovery)
      : autosavePointer
        ? localStorage.getItem(autosavePointer.storageKey)
        : null;
    if (candidate === null) return "skipped";
    const parsed = readStructuredRecovery(candidate, scope.authoringContextBinding);
    if (!parsed.ok) return "refused";
    const targetRef = parsed.recovery.visualDraft.target_ref;
    const existingPointer = currentRecoveryPointer(scope, "backup");
    const existing = existingPointer
      ? localStorage.getItem(existingPointer.storageKey)
      : null;
    if (existing === candidate) return "skipped";
    if (existing !== null && options?.force !== true) return "refused";
    const storageKey = recoveryStorageKey(scope, "backup", targetRef);
    localStorage.setItem(storageKey, candidate);
    setCurrentRecoveryPointer(scope, "backup", targetRef, storageKey);
    return "stashed";
  } catch {
    return "skipped";
  }
}

export function restoreStructuredRecovery(
  slot: StructuredRecoverySlot,
  scope: RecoveryStorageScope,
  options?: { consume?: boolean },
): StructuredRecoveryReadResult {
  let raw: string | null;
  let pointer: RecoveryPointer | null;
  try {
    pointer = currentRecoveryPointer(scope, slot);
    raw = pointer ? localStorage.getItem(pointer.storageKey) : null;
  } catch {
    return { ok: false, reason: "browser storage is unavailable" };
  }
  if (raw === null) return { ok: false, reason: "there is no structured draft to restore" };
  const result = readStructuredRecovery(raw, scope.authoringContextBinding);
  if (!result.ok) return result;
  if (options?.consume) {
    try {
      if (pointer !== null) localStorage.removeItem(pointer.storageKey);
      sessionStorage.removeItem(currentTargetKey(scope, slot));
    } catch {
      // The validated recovery was adopted even if the consumed slot cannot be removed.
    }
  }
  return result;
}

export function clearStructuredRecoveryScope(scope: RecoveryStorageScope): void {
  try {
    const prefix = `${recoveryScopePrefix(scope)}:`;
    const keys = Array.from({ length: localStorage.length }, (_unused, index) =>
      localStorage.key(index),
    ).filter((key): key is string => key !== null && key.startsWith(prefix));
    for (const key of keys) localStorage.removeItem(key);
    for (const kind of ["autosave", "backup", "catalog"] as const) {
      const pointer = currentRecoveryPointer(scope, kind);
      if (pointer) localStorage.removeItem(pointer.storageKey);
      sessionStorage.removeItem(currentTargetKey(scope, kind));
    }
  } catch {
    return;
  }
}

export function serializeCatalogDraftRecovery(
  recovery: CatalogDraftEditorRecovery,
  authoringContextBinding = "unbound-recovery",
): string {
  return JSON.stringify({
    v: CATALOG_DRAFT_RECOVERY_VERSION,
    kind: "catalog-draft",
    authoring_context_binding: authoringContextBinding,
    draft: recovery.draft,
    baseline_document: recovery.baselineDocument,
    working_document: recovery.workingDocument,
    yaml_text: recovery.yamlText,
    applied_yaml_text: recovery.appliedYamlText,
    canonicalization_required: recovery.canonicalizationRequired,
    canonicalization_accepted: recovery.canonicalizationAccepted,
  });
}

export function readCatalogDraftRecovery(
  raw: string,
  expectedAuthoringContextBinding?: string,
): CatalogDraftRecoveryReadResult {
  try {
    const value: unknown = JSON.parse(raw);
    if (!isRecord(value)) return { ok: false, reason: "the component draft could not be read" };
    if (value.v !== CATALOG_DRAFT_RECOVERY_VERSION) {
      return {
        ok: false,
        reason: `component draft recovery version ${String(value.v)} is not supported`,
      };
    }
    if (
      Object.keys(value).length !== 10 ||
      value.kind !== "catalog-draft" ||
      typeof value.authoring_context_binding !== "string" ||
      value.authoring_context_binding.length === 0 ||
      (expectedAuthoringContextBinding !== undefined &&
        value.authoring_context_binding !== expectedAuthoringContextBinding) ||
      !isCatalogDraftEditorRecovery(value)
    ) {
      return { ok: false, reason: "the saved component draft is incomplete or invalid" };
    }
    return {
      ok: true,
      recovery: {
        draft: structuredClone(value.draft),
        baselineDocument: structuredClone(value.baseline_document),
        workingDocument: structuredClone(value.working_document),
        yamlText: value.yaml_text,
        appliedYamlText: value.applied_yaml_text,
        canonicalizationRequired: value.canonicalization_required,
        canonicalizationAccepted: value.canonicalization_accepted,
      },
    };
  } catch {
    return { ok: false, reason: "the component draft could not be read" };
  }
}

export function loadCatalogDraftRecovery(
  scope: RecoveryStorageScope,
): CatalogDraftRecoveryReadResult {
  try {
    const pointer = currentRecoveryPointer(scope, "catalog");
    const raw = pointer ? localStorage.getItem(pointer.storageKey) : null;
    return raw === null
      ? { ok: false, reason: "there is no component draft to restore" }
      : readCatalogDraftRecovery(raw, scope.authoringContextBinding);
  } catch {
    return { ok: false, reason: "browser storage is unavailable" };
  }
}

export function writeCatalogDraftRecovery(
  recovery: CatalogDraftEditorRecovery,
  scope: RecoveryStorageScope,
): boolean {
  try {
    const storageKey = recoveryStorageKey(scope, "catalog", recovery.draft.target_ref);
    localStorage.setItem(
      storageKey,
      serializeCatalogDraftRecovery(recovery, scope.authoringContextBinding),
    );
    setCurrentRecoveryPointer(scope, "catalog", recovery.draft.target_ref, storageKey);
    return true;
  } catch {
    return false;
  }
}

export function clearCatalogDraftRecovery(scope: RecoveryStorageScope): void {
  try {
    const pointer = currentRecoveryPointer(scope, "catalog");
    if (pointer !== null) localStorage.removeItem(pointer.storageKey);
    sessionStorage.removeItem(currentTargetKey(scope, "catalog"));
  } catch {
    return;
  }
}
