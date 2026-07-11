/** Lossless browser recovery for structured Builder application state. */

import type {
  BuilderVisualDraftEnvelope,
  CatalogComponentDraftEnvelope,
  JsonValue,
} from "./generated/builderApi";
import type { EditorWindow } from "./useEditorWindows";
import type { BufferMap } from "./useWorkspace";
import {
  hasRequiredAuthoringState,
  isCurrentWorkspace,
  type Workspace,
} from "./workspace";

export const STRUCTURED_AUTOSAVE_KEY = "nodalarc-builder-draft";
export const STRUCTURED_BACKUP_KEY = "nodalarc-builder-draft-previous";
export const STRUCTURED_RECOVERY_VERSION = 2;
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

function isStructuredVisualDraft(value: unknown): value is BuilderVisualDraftEnvelope {
  if (!isRecord(value)) return false;
  return (
    value.contract_version === 1 &&
    typeof value.draft_revision === "number" &&
    value.mode === "structured" &&
    typeof value.target_ref === "string" &&
    value.target_ref.startsWith("user:sessions/") &&
    (value.source_ref === undefined || value.source_ref === null || typeof value.source_ref === "string") &&
    (value.expected_session_revision === undefined ||
      value.expected_session_revision === null ||
      typeof value.expected_session_revision === "string") &&
    (value.expected_catalog_revisions === undefined ||
      Array.isArray(value.expected_catalog_revisions)) &&
    (value.catalog_documents === undefined || Array.isArray(value.catalog_documents)) &&
    isRecord(value.workspace) &&
    hasRequiredAuthoringState(value.workspace) &&
    (value.session_yaml === undefined || value.session_yaml === null)
  );
}

function isEditorRecovery(value: unknown): value is StructuredEditorRecoveryState {
  if (!isRecord(value) || !Array.isArray(value.windows) || !isRecord(value.buffers)) {
    return false;
  }
  const buffers = value.buffers;
  for (const [key, candidate] of Object.entries(buffers)) {
    if (
      !isRecord(candidate) ||
      candidate.dirty !== true ||
      !("draft" in candidate) ||
      !("opened" in candidate) ||
      !value.windows.some((window) => isRecord(window) && window.key === key)
    ) {
      return false;
    }
  }
  return value.windows.every(
    (window) =>
      isRecord(window) &&
      typeof window.key === "string" &&
      typeof window.x === "number" &&
      typeof window.y === "number" &&
      isRecord(window.target) &&
      typeof window.target.kind === "string" &&
      window.key in buffers,
  );
}

function isCatalogDraftEnvelope(value: unknown): value is CatalogComponentDraftEnvelope {
  if (!isRecord(value)) return false;
  return (
    value.contract_version === 1 &&
    typeof value.draft_revision === "number" &&
    typeof value.family === "string" &&
    typeof value.target_ref === "string" &&
    value.target_ref.startsWith("user:") &&
    (value.source_ref === undefined || value.source_ref === null || typeof value.source_ref === "string") &&
    (value.expected_source_revision === undefined ||
      value.expected_source_revision === null ||
      typeof value.expected_source_revision === "string") &&
    (value.expected_target_revision === undefined ||
      value.expected_target_revision === null ||
      typeof value.expected_target_revision === "string") &&
    isRecord(value.document) &&
    Array.isArray(value.issues)
  );
}

function isCatalogDraftEditorRecovery(
  value: unknown,
): value is SerializedCatalogDraftEditorRecovery {
  return (
    isRecord(value) &&
    isCatalogDraftEnvelope(value.draft) &&
    isRecord(value.working_document) &&
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
      !isCurrentWorkspace(value.workspace) ||
      !isStructuredVisualDraft(value.visual_draft) ||
      !isEditorRecovery(value.editor)
    ) {
      return { ok: false, reason: "the saved structured draft is incomplete or invalid" };
    }
    return {
      ok: true,
      recovery: {
        workspace: structuredClone(value.workspace),
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
