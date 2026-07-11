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

export const STRUCTURED_AUTOSAVE_KEY = "nodalarc-builder-draft";
export const STRUCTURED_BACKUP_KEY = "nodalarc-builder-draft-previous";
export const STRUCTURED_RECOVERY_VERSION = 4;
export const CATALOG_DRAFT_RECOVERY_KEY = "nodalarc-builder-catalog-draft";
export const CATALOG_DRAFT_RECOVERY_VERSION = 1;

export interface StructuredEditorRecoveryState {
  windows: EditorWindow[];
  buffers: BufferMap;
}

export interface StructuredDraftRecovery {
  workspace: Workspace;
  visualDraft: BuilderVisualDraftEnvelope;
  editor: StructuredEditorRecoveryState;
}

export type StructuredRecoveryReadResult =
  | { ok: true; recovery: StructuredDraftRecovery }
  | { ok: false; reason: string };

export type StructuredStashOutcome = "stashed" | "skipped" | "refused";

export interface CatalogDraftEditorRecovery {
  draft: CatalogComponentDraftEnvelope;
  workingDocument: Readonly<Record<string, JsonValue>>;
  advanced: boolean;
  advancedText: string;
}

interface SerializedCatalogDraftEditorRecovery {
  draft: CatalogComponentDraftEnvelope;
  working_document: Readonly<Record<string, JsonValue>>;
  advanced: boolean;
  advanced_text: string;
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

export function isVisualDraftEnvelopeForMode(
  value: unknown,
  mode: BuilderVisualDraftEnvelope["mode"],
): value is BuilderVisualDraftEnvelope {
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
    envelope.mode === mode &&
    envelope.target_ref.startsWith("user:sessions/") &&
    uniqueRefs(envelope.expected_catalog_revisions ?? []) &&
    (envelope.expected_catalog_revisions ?? []).every((item) =>
      isComponentRef(item.ref),
    ) &&
    uniqueRefs(envelope.catalog_documents ?? []) &&
    (envelope.catalog_documents ?? []).every(
      (item) => item.ref.startsWith("user:") && isComponentRef(item.ref),
    ) &&
    new Set(envelope.reserved_authoring_ids).size ===
      envelope.reserved_authoring_ids.length &&
    envelope.reserved_authoring_ids.every(
      (authoringId) => authoringId.length > 0 && authoringId.length <= 160,
    ) &&
    (mode === "structured"
      ? envelope.workspace !== null && envelope.session_yaml === null
      : envelope.workspace === null && typeof envelope.session_yaml === "string")
  );
}

function isStructuredVisualDraft(value: unknown): value is BuilderVisualDraftEnvelope {
  return isVisualDraftEnvelopeForMode(value, "structured");
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
    isRecord(value.working_document) &&
    isJsonValue(value.working_document) &&
    typeof value.advanced === "boolean" &&
    typeof value.advanced_text === "string"
  );
}

export function createStructuredRecovery(input: {
  workspace: Workspace | null;
  visualDraft: BuilderVisualDraftEnvelope | null;
  windows: readonly EditorWindow[];
  buffers: BufferMap;
}): StructuredDraftRecovery | null {
  if (!input.workspace || input.visualDraft?.mode !== "structured") return null;
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
    workspace: structuredClone(input.workspace),
    visualDraft: structuredClone(input.visualDraft),
    editor: { windows, buffers },
  };
}

export function serializeStructuredRecovery(recovery: StructuredDraftRecovery): string {
  return JSON.stringify({
    v: STRUCTURED_RECOVERY_VERSION,
    kind: "structured",
    workspace: recovery.workspace,
    visual_draft: recovery.visualDraft,
    editor: recovery.editor,
  });
}

export function readStructuredRecovery(raw: string): StructuredRecoveryReadResult {
  try {
    const value: unknown = JSON.parse(raw);
    if (!isRecord(value)) return { ok: false, reason: "the saved draft could not be read" };
    if (value.v !== STRUCTURED_RECOVERY_VERSION) {
      return { ok: false, reason: `draft recovery version ${String(value.v)} is not supported` };
    }
    if (
      Object.keys(value).length !== 5 ||
      value.kind !== "structured" ||
      !matchesRuntimeDescriptor(
        value.workspace,
        BUILDER_VISUAL_WORKSPACE_RUNTIME_DESCRIPTOR,
      ) ||
      !isStructuredVisualDraft(value.visual_draft) ||
      !isEditorRecovery(value.editor, value.workspace as Workspace)
    ) {
      return { ok: false, reason: "the saved structured draft is incomplete or invalid" };
    }
    return {
      ok: true,
      recovery: {
        workspace: structuredClone(value.workspace) as Workspace,
        visualDraft: structuredClone(value.visual_draft),
        editor: structuredClone(value.editor),
      },
    };
  } catch {
    return { ok: false, reason: "the saved draft could not be read" };
  }
}

export function writeStructuredAutosave(recovery: StructuredDraftRecovery): boolean {
  try {
    localStorage.setItem(STRUCTURED_AUTOSAVE_KEY, serializeStructuredRecovery(recovery));
    return true;
  } catch {
    return false;
  }
}

export function hasStructuredRecovery(key: string): boolean {
  try {
    return localStorage.getItem(key) !== null;
  } catch {
    return false;
  }
}

export function stashStructuredRecovery(
  recovery: StructuredDraftRecovery | null,
  options?: { force?: boolean },
): StructuredStashOutcome {
  try {
    const candidate = recovery
      ? serializeStructuredRecovery(recovery)
      : localStorage.getItem(STRUCTURED_AUTOSAVE_KEY);
    if (candidate === null) return "skipped";
    const existing = localStorage.getItem(STRUCTURED_BACKUP_KEY);
    if (existing === candidate) return "skipped";
    if (existing !== null && options?.force !== true) return "refused";
    localStorage.setItem(STRUCTURED_BACKUP_KEY, candidate);
    return "stashed";
  } catch {
    return "skipped";
  }
}

export function restoreStructuredRecovery(
  key: string,
  options?: { consume?: boolean },
): StructuredRecoveryReadResult {
  let raw: string | null;
  try {
    raw = localStorage.getItem(key);
  } catch {
    return { ok: false, reason: "browser storage is unavailable" };
  }
  if (raw === null) return { ok: false, reason: "there is no structured draft to restore" };
  const result = readStructuredRecovery(raw);
  if (!result.ok) return result;
  if (options?.consume) {
    try {
      localStorage.removeItem(key);
    } catch {
      // The validated recovery was adopted even if the consumed slot cannot be removed.
    }
  }
  return result;
}

export function serializeCatalogDraftRecovery(recovery: CatalogDraftEditorRecovery): string {
  return JSON.stringify({
    v: CATALOG_DRAFT_RECOVERY_VERSION,
    kind: "catalog-draft",
    draft: recovery.draft,
    working_document: recovery.workingDocument,
    advanced: recovery.advanced,
    advanced_text: recovery.advancedText,
  });
}

export function readCatalogDraftRecovery(raw: string): CatalogDraftRecoveryReadResult {
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
      Object.keys(value).length !== 6 ||
      value.kind !== "catalog-draft" ||
      !isCatalogDraftEditorRecovery(value)
    ) {
      return { ok: false, reason: "the saved component draft is incomplete or invalid" };
    }
    return {
      ok: true,
      recovery: {
        draft: structuredClone(value.draft),
        workingDocument: structuredClone(value.working_document),
        advanced: value.advanced,
        advancedText: value.advanced_text,
      },
    };
  } catch {
    return { ok: false, reason: "the component draft could not be read" };
  }
}

export function loadCatalogDraftRecovery(): CatalogDraftRecoveryReadResult {
  try {
    const raw = localStorage.getItem(CATALOG_DRAFT_RECOVERY_KEY);
    return raw === null
      ? { ok: false, reason: "there is no component draft to restore" }
      : readCatalogDraftRecovery(raw);
  } catch {
    return { ok: false, reason: "browser storage is unavailable" };
  }
}

export function writeCatalogDraftRecovery(recovery: CatalogDraftEditorRecovery): boolean {
  try {
    localStorage.setItem(CATALOG_DRAFT_RECOVERY_KEY, serializeCatalogDraftRecovery(recovery));
    return true;
  } catch {
    return false;
  }
}

export function clearCatalogDraftRecovery(): void {
  try {
    localStorage.removeItem(CATALOG_DRAFT_RECOVERY_KEY);
  } catch {
    return;
  }
}
