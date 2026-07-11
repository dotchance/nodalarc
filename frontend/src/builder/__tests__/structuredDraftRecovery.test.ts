import { beforeEach, describe, expect, it } from "vitest";
import {
  createStructuredRecovery,
  clearCatalogDraftRecovery,
  loadCatalogDraftRecovery,
  readCatalogDraftRecovery,
  readStructuredRecovery,
  restoreStructuredRecovery,
  serializeStructuredRecovery,
  serializeCatalogDraftRecovery,
  stashStructuredRecovery,
  STRUCTURED_AUTOSAVE_KEY,
  STRUCTURED_BACKUP_KEY,
  writeStructuredAutosave,
  writeCatalogDraftRecovery,
  CATALOG_DRAFT_RECOVERY_KEY,
} from "../structuredDraftRecovery";
import { visualWorkspaceFromWorkspace } from "../visualWorkspace";
import {
  newDraftConstellation,
  newWorkspace,
} from "./fixtures/workspaceFixtures";

function recoveryFixture() {
  const workspace = newWorkspace("recover-exact");
  workspace.space.push(newDraftConstellation("nodalarc:nodes/space/recover.yaml"));
  const visualDraft = {
    contract_version: 1 as const,
    draft_revision: 12,
    mode: "structured" as const,
    target_ref: "user:sessions/recover-exact.yaml",
    source_ref: "nodalarc:sessions/earth-leo-simple.yaml",
    expected_session_revision: "session-revision",
    expected_catalog_revisions: [
      { ref: "user:orbits/recover/orbit.yaml", expected_revision: "orbit-revision" },
    ],
    catalog_documents: [
      {
        ref: "user:terminals/recover/terminal.yaml",
        expected_revision: "terminal-revision",
        document: { terminal: { id: "terminal", display_name: "Unsaved terminal" } },
      },
    ],
    workspace: visualWorkspaceFromWorkspace(workspace),
  };
  const recovery = createStructuredRecovery({
    workspace,
    visualDraft,
    windows: [
      { key: "session", target: { kind: "session" }, x: 20, y: 30 },
      { key: "segment:clean", target: { kind: "segment", id: "clean" }, x: 40, y: 50 },
    ],
    buffers: {
      session: {
        opened: { name: "recover-exact" },
        draft: { name: "recover-renamed" },
        dirty: true,
      },
      "segment:clean": { opened: {}, draft: {}, dirty: false },
    },
  });
  if (!recovery) throw new Error("expected structured recovery fixture");
  return recovery;
}

beforeEach(() => localStorage.clear());

describe("structured Builder draft recovery", () => {
  it("round-trips the exact visual envelope, proposals, fences, and dirty buffers", () => {
    const recovery = recoveryFixture();
    const result = readStructuredRecovery(serializeStructuredRecovery(recovery));

    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.recovery.visualDraft).toEqual(recovery.visualDraft);
    expect(result.recovery.visualDraft.catalog_documents).toEqual(
      recovery.visualDraft.catalog_documents,
    );
    expect(result.recovery.visualDraft.expected_catalog_revisions).toEqual(
      recovery.visualDraft.expected_catalog_revisions,
    );
    expect(result.recovery.visualDraft.expected_session_revision).toBe("session-revision");
    expect(result.recovery.editor.buffers).toEqual({ session: recovery.editor.buffers.session });
    expect(result.recovery.editor.windows.map((window) => window.key)).toEqual(["session"]);
  });

  it("refuses every non-current recovery version without interpretation", () => {
    const result = readStructuredRecovery(
      JSON.stringify({ v: 1, workspace: recoveryFixture().workspace }),
    );

    expect(result).toEqual({
      ok: false,
      reason: "draft recovery version 1 is not supported",
    });
  });

  it("requires the current visual draft contract version", () => {
    const serialized = JSON.parse(
      serializeStructuredRecovery(recoveryFixture()),
    ) as Record<string, unknown>;
    const visualDraft = serialized.visual_draft as Record<string, unknown>;
    delete visualDraft.contract_version;

    expect(readStructuredRecovery(JSON.stringify(serialized))).toEqual({
      ok: false,
      reason: "the saved structured draft is incomplete or invalid",
    });
  });

  it("refuses a visual envelope from before explicit phasing was authored", () => {
    const serialized = JSON.parse(
      serializeStructuredRecovery(recoveryFixture()),
    ) as Record<string, unknown>;
    const visualDraft = serialized.visual_draft as {
      workspace: { space: Array<Record<string, unknown>> };
    };
    delete visualDraft.workspace.space[0]?.phasing_mode;

    expect(readStructuredRecovery(JSON.stringify(serialized))).toEqual({
      ok: false,
      reason: "the saved structured draft is incomplete or invalid",
    });
  });

  it("refuses unknown future versions and preserves the stored slot", () => {
    const raw = JSON.stringify({ v: 999, kind: "structured" });
    localStorage.setItem(STRUCTURED_AUTOSAVE_KEY, raw);

    expect(restoreStructuredRecovery(STRUCTURED_AUTOSAVE_KEY)).toEqual({
      ok: false,
      reason: "draft recovery version 999 is not supported",
    });
    expect(localStorage.getItem(STRUCTURED_AUTOSAVE_KEY)).toBe(raw);
  });

  it("backs up the exact current envelope and consumes only a valid restored backup", () => {
    const recovery = recoveryFixture();
    expect(writeStructuredAutosave(recovery)).toBe(true);
    expect(stashStructuredRecovery(recovery)).toBe("stashed");

    const restored = restoreStructuredRecovery(STRUCTURED_BACKUP_KEY, { consume: true });
    expect(restored.ok).toBe(true);
    if (restored.ok) expect(restored.recovery.visualDraft).toEqual(recovery.visualDraft);
    expect(localStorage.getItem(STRUCTURED_BACKUP_KEY)).toBeNull();
  });
});

describe("catalog component draft recovery", () => {
  const recovery = {
    draft: {
      contract_version: 1 as const,
      draft_revision: 9,
      family: "terminals" as const,
      target_ref: "user:terminals/recovered.yaml",
      source_ref: "nodalarc:terminals/source.yaml",
      expected_source_revision: "source-revision",
      expected_target_revision: "target-revision",
      document: {
        terminal: { id: "recovered", display_name: "Saved baseline" },
      },
      issues: [],
    },
    workingDocument: {
      terminal: { id: "recovered", display_name: "Dirty recovered value" },
    },
    advanced: true,
    advancedText: '{"id":"recovered","display_name":"unfinished',
  };

  it("round-trips the backend draft, revision fences, working copy, and raw buffer", () => {
    const result = readCatalogDraftRecovery(serializeCatalogDraftRecovery(recovery));

    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.recovery).toEqual(recovery);
    expect(result.recovery.draft.draft_revision).toBe(9);
    expect(result.recovery.draft.expected_source_revision).toBe("source-revision");
    expect(result.recovery.draft.expected_target_revision).toBe("target-revision");
  });

  it("requires the current component draft contract version", () => {
    const serialized = JSON.parse(
      serializeCatalogDraftRecovery(recovery),
    ) as Record<string, unknown>;
    const draft = serialized.draft as Record<string, unknown>;
    delete draft.contract_version;

    expect(readCatalogDraftRecovery(JSON.stringify(serialized))).toEqual({
      ok: false,
      reason: "the saved component draft is incomplete or invalid",
    });
  });

  it("persists across reload and clears only on explicit completion or discard", () => {
    expect(writeCatalogDraftRecovery(recovery)).toBe(true);
    expect(localStorage.getItem(CATALOG_DRAFT_RECOVERY_KEY)).not.toBeNull();
    expect(loadCatalogDraftRecovery()).toEqual({ ok: true, recovery });

    clearCatalogDraftRecovery();
    expect(localStorage.getItem(CATALOG_DRAFT_RECOVERY_KEY)).toBeNull();
  });
});
