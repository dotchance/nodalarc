import { beforeEach, describe, expect, it } from "vitest";
import type { BuilderVisualDraftEnvelope } from "../generated/builderApi";
import {
  clearCatalogDraftRecovery,
  createStructuredRecovery,
  hasStructuredRecovery,
  loadCatalogDraftRecovery,
  readCatalogDraftRecovery,
  readStructuredRecovery,
  recoveryStorageKey,
  restoreStructuredRecovery,
  serializeCatalogDraftRecovery,
  serializeStructuredRecovery,
  stashStructuredRecovery,
  STRUCTURED_RECOVERY_VERSION,
  writeStructuredAutosave,
  writeCatalogDraftRecovery,
} from "../structuredDraftRecovery";
import {
  defaultDraftNode,
  newDraftConstellation,
  newDraftGroundSet,
  newWorkspace,
} from "./fixtures/workspaceFixtures";
import { routingWarnings } from "../workspace";

const AUTHORING_CONTEXT = "structured-recovery-test-context";
const RECOVERY_SCOPE = {
  authoringContextBinding: AUTHORING_CONTEXT,
  tabBinding: "structured-recovery-test-tab",
};

function recoveryFixture() {
  const workspace = newWorkspace("recover-exact");
  workspace.projection_revision = 12;
  const space = newDraftConstellation("nodalarc:nodes/space/recover.yaml");
  space.node_draft = defaultDraftNode();
  workspace.space.push(space);
  workspace.ground.push(newDraftGroundSet("nodalarc:nodes/ground/recover.yaml", {}));
  const visualDraft: BuilderVisualDraftEnvelope = {
    contract_version: 2,
    draft_revision: 12,
    projection_status: "pending_authoring",
    target_ref: "user:sessions/recover-exact.yaml",
    source_ref: "user:sessions/recover-exact.yaml",
    expected_session_revision: "session-revision",
    catalog_documents: [],
    session_name_is_placeholder: false,
    reserved_authoring_ids: [],
    session_yaml: "# unfinished\nsession:\n  name: recover-exact\n  bad: [\n",
    authoring_workspace: { ...workspace, projection_revision: null },
    applied_workspace: workspace,
    applied_revision: 12,
    applied_session: { session: { name: "recover-exact" } },
  };
  const recovery = createStructuredRecovery({
    authoringContextBinding: AUTHORING_CONTEXT,
    workspace,
    visualDraft,
    yaml: {
      text: visualDraft.session_yaml,
      appliedText: "session:\n  name: recover-exact\n",
      generation: 7,
      canonicalizationRequired: false,
      canonicalizationAccepted: false,
      issues: [
        {
          code: "builder.yaml.syntax",
          stage: "structural",
          severity: "error",
          message: "expected a closing bracket",
          source_line: 4,
          source_column: 8,
          blocks: ["save", "deploy"],
        },
      ],
    },
    windows: [],
    buffers: {},
  });
  if (!recovery) throw new Error("expected recovery fixture");
  return recovery;
}

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
});

describe("session Builder recovery v2", () => {
  it("round-trips the envelope, exact and applied YAML, generation, and issues", () => {
    const recovery = recoveryFixture();
    const result = readStructuredRecovery(serializeStructuredRecovery(recovery));
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.recovery.visualDraft).toEqual(recovery.visualDraft);
    expect(result.recovery.yaml).toEqual(recovery.yaml);
    expect(result.recovery.yaml.text).toContain("# unfinished");
    expect(result.recovery.yaml.appliedText).not.toBe(result.recovery.yaml.text);
  });

  it("refuses every prior recovery version without compatibility", () => {
    const serialized = JSON.parse(serializeStructuredRecovery(recoveryFixture())) as Record<
      string,
      unknown
    >;
    serialized.v = STRUCTURED_RECOVERY_VERSION - 1;
    expect(readStructuredRecovery(JSON.stringify(serialized))).toEqual({
      ok: false,
      reason: `draft recovery version ${STRUCTURED_RECOVERY_VERSION - 1} is not supported`,
    });
  });

  it("refuses recovery that omits the YAML coordination state", () => {
    const serialized = JSON.parse(serializeStructuredRecovery(recoveryFixture())) as Record<
      string,
      unknown
    >;
    delete serialized.yaml;
    expect(readStructuredRecovery(JSON.stringify(serialized))).toEqual({
      ok: false,
      reason: "the saved structured draft is incomplete or invalid",
    });
  });

  it("autosaves and stashes only the current v2 document", () => {
    const recovery = recoveryFixture();
    expect(writeStructuredAutosave(recovery, RECOVERY_SCOPE)).toBe(true);
    expect(stashStructuredRecovery(null, RECOVERY_SCOPE)).toBe("stashed");
    const autosaveKey = recoveryStorageKey(
      RECOVERY_SCOPE,
      "autosave",
      recovery.visualDraft.target_ref,
    );
    const backupKey = recoveryStorageKey(
      RECOVERY_SCOPE,
      "backup",
      recovery.visualDraft.target_ref,
    );
    expect(localStorage.getItem(autosaveKey)).not.toBeNull();
    expect(localStorage.getItem(backupKey)).not.toBeNull();
    const restored = restoreStructuredRecovery("backup", RECOVERY_SCOPE, { consume: true });
    expect(restored.ok).toBe(true);
    expect(localStorage.getItem(backupKey)).toBeNull();
  });

  it("refuses a recovery bound to a different backend authoring context", () => {
    const raw = serializeStructuredRecovery(recoveryFixture());
    expect(readStructuredRecovery(raw, "different-context")).toEqual({
      ok: false,
      reason: "the saved structured draft is incomplete or invalid",
    });
  });

  it("refuses a persisted-session fence attached to a different source ref", () => {
    const serialized = JSON.parse(
      serializeStructuredRecovery(recoveryFixture()),
    ) as Record<string, unknown>;
    (serialized.visual_draft as Record<string, unknown>).source_ref =
      "nodalarc:sessions/earth-leo-simple.yaml";
    expect(readStructuredRecovery(JSON.stringify(serialized), AUTHORING_CONTEXT)).toEqual({
      ok: false,
      reason: "the saved structured draft is incomplete or invalid",
    });
  });

  it("isolates mutable recovery slots across browser tabs and context changes", () => {
    const first = recoveryFixture();
    const second = structuredClone(first);
    second.yaml.text = `${second.yaml.text}# second tab\n`;
    const otherTab = { ...RECOVERY_SCOPE, tabBinding: "other-tab" };
    expect(writeStructuredAutosave(first, RECOVERY_SCOPE)).toBe(true);
    const firstKey = recoveryStorageKey(
      RECOVERY_SCOPE,
      "autosave",
      first.visualDraft.target_ref,
    );
    expect(writeStructuredAutosave(second, otherTab)).toBe(true);
    const secondKey = recoveryStorageKey(
      otherTab,
      "autosave",
      second.visualDraft.target_ref,
    );
    const firstRestored = readStructuredRecovery(
      localStorage.getItem(firstKey)!,
      AUTHORING_CONTEXT,
    );
    const secondRestored = readStructuredRecovery(
      localStorage.getItem(secondKey)!,
      AUTHORING_CONTEXT,
    );
    expect(firstRestored.ok && firstRestored.recovery.yaml.text).toBe(first.yaml.text);
    expect(secondRestored.ok && secondRestored.recovery.yaml.text).toBe(second.yaml.text);
    expect(firstKey).not.toBe(secondKey);
    expect(hasStructuredRecovery("autosave", {
      ...RECOVERY_SCOPE,
      authoringContextBinding: "different-context",
    })).toBe(false);
  });

  it("keeps the last valid workspace and applied revision beside invalid YAML", () => {
    const result = readStructuredRecovery(serializeStructuredRecovery(recoveryFixture()));
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.recovery.visualDraft.projection_status).toBe("pending_authoring");
    expect(result.recovery.visualDraft.applied_revision).toBe(12);
    expect(result.recovery.workspace.session_name).toBe("recover-exact");
    expect(result.recovery.yaml.issues[0]?.source_line).toBe(4);
  });

  it("round-trips dirty editor buffers without flattening them into the workspace", () => {
    const base = recoveryFixture();
    const sessionFields = {
      session_name: base.workspace.session_name,
      start_time: base.workspace.start_time,
      step_seconds: base.workspace.step_seconds,
      compression: base.workspace.compression,
      max_pairs_per_rule: base.workspace.max_pairs_per_rule,
      max_pairs_per_tick: base.workspace.max_pairs_per_tick,
    };
    const recovery = createStructuredRecovery({
      authoringContextBinding: AUTHORING_CONTEXT,
      workspace: base.workspace,
      visualDraft: base.visualDraft,
      yaml: base.yaml,
      windows: [{ key: "session", target: { kind: "session" }, x: 10, y: 20 }],
      buffers: {
        session: {
          opened: sessionFields,
          draft: { ...sessionFields, session_name: "dirty-name" },
          dirty: true,
        },
      },
    });
    if (!recovery) throw new Error("expected dirty recovery");
    const result = readStructuredRecovery(serializeStructuredRecovery(recovery));
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.recovery.workspace.session_name).toBe("recover-exact");
    expect(result.recovery.editor.buffers.session?.draft).toMatchObject({
      session_name: "dirty-name",
    });
  });

  it("accepts an explicitly incomplete phase offset", () => {
    const recovery = recoveryFixture();
    recovery.workspace.space[0]!.phase_offset_deg = null;
    recovery.visualDraft = {
      ...recovery.visualDraft,
      authoring_workspace: {
        ...recovery.visualDraft.authoring_workspace!,
        space: [{ ...recovery.workspace.space[0]! }],
      },
      applied_workspace: recovery.workspace,
    };
    expect(readStructuredRecovery(serializeStructuredRecovery(recovery)).ok).toBe(true);
  });

  it.each([
    ["numeric terminal role", "role", 42],
    ["string terminal count", "count", "1"],
  ] as const)("refuses %s in every graphical projection", (_label, field, invalid) => {
    const recovery = recoveryFixture();
    const serialized = JSON.parse(serializeStructuredRecovery(recovery)) as Record<
      string,
      unknown
    >;
    const workspaces = [
      serialized.workspace,
      (serialized.visual_draft as Record<string, unknown>).authoring_workspace,
      (serialized.visual_draft as Record<string, unknown>).applied_workspace,
    ] as Array<Record<string, unknown>>;
    for (const workspace of workspaces) {
      const space = (workspace.space as Array<Record<string, unknown>>)[0]!;
      const node = space.node_draft as Record<string, unknown>;
      const mount: Record<string, unknown> = {
        mount_id: "access-1",
        role: "access",
        terminal_ref: "nodalarc:terminals/rf/access.yaml",
        count: 1,
        boresight: { mode: "nadir" },
      };
      mount[field] = invalid;
      node.terminals = [mount];
    }
    expect(readStructuredRecovery(JSON.stringify(serialized))).toEqual({
      ok: false,
      reason: "the saved structured draft is incomplete or invalid",
    });
  });

  it.each(["draft", "opened"] as const)(
    "refuses malformed session buffer %s values",
    (side) => {
      const base = recoveryFixture();
      const fields = {
        session_name: base.workspace.session_name,
        start_time: base.workspace.start_time,
        step_seconds: base.workspace.step_seconds,
        compression: base.workspace.compression,
        max_pairs_per_rule: base.workspace.max_pairs_per_rule,
        max_pairs_per_tick: base.workspace.max_pairs_per_tick,
      };
      const recovery = createStructuredRecovery({
        authoringContextBinding: AUTHORING_CONTEXT,
        workspace: base.workspace,
        visualDraft: base.visualDraft,
        yaml: base.yaml,
        windows: [{ key: "session", target: { kind: "session" }, x: 0, y: 0 }],
        buffers: {
          session: { draft: { ...fields }, opened: { ...fields }, dirty: true },
        },
      });
      if (!recovery) throw new Error("expected session recovery");
      const serialized = JSON.parse(serializeStructuredRecovery(recovery)) as Record<
        string,
        unknown
      >;
      const buffer = (
        (serialized.editor as { buffers: { session: Record<string, unknown> } }).buffers
          .session[side] as Record<string, unknown>
      );
      buffer.step_seconds = "1";
      expect(readStructuredRecovery(JSON.stringify(serialized))).toEqual({
        ok: false,
        reason: "the saved structured draft is incomplete or invalid",
      });
    },
  );

  it("refuses malformed object buffers", () => {
    const base = recoveryFixture();
    const segment = structuredClone(base.workspace.space[0]!);
    const key = `segment:${segment.segment_id}`;
    const recovery = createStructuredRecovery({
      authoringContextBinding: AUTHORING_CONTEXT,
      workspace: base.workspace,
      visualDraft: base.visualDraft,
      yaml: base.yaml,
      windows: [{ key, target: { kind: "segment", id: segment.segment_id }, x: 0, y: 0 }],
      buffers: {
        [key]: { draft: segment, opened: structuredClone(segment), dirty: true },
      },
    });
    if (!recovery) throw new Error("expected object recovery");
    const serialized = JSON.parse(serializeStructuredRecovery(recovery)) as Record<
      string,
      unknown
    >;
    const buffer = (
      (serialized.editor as { buffers: Record<string, { draft: Record<string, unknown> }> })
        .buffers[key]!.draft
    );
    buffer.planes = "3";
    expect(readStructuredRecovery(JSON.stringify(serialized))).toEqual({
      ok: false,
      reason: "the saved structured draft is incomplete or invalid",
    });
  });

  it("preserves dangling topology for graphical warnings", () => {
    const recovery = recoveryFixture();
    recovery.workspace.boundaries = [
      {
        boundary_id: "dangling",
        over_rule_id: "missing-rule",
        adapter: "static_ip",
        from_domain_id: "missing-a",
        to_domain_id: "missing-b",
        export_node_loopbacks: true,
      },
    ];
    recovery.visualDraft = {
      ...recovery.visualDraft,
      authoring_workspace: recovery.workspace,
      applied_workspace: recovery.workspace,
    };
    const result = readStructuredRecovery(serializeStructuredRecovery(recovery));
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(routingWarnings(result.recovery.workspace)).toEqual(
      expect.arrayContaining([
        "a boundary rides a link rule that is no longer in the session",
        "a boundary references a routing domain that no longer exists",
      ]),
    );
  });

  it.each([
    ["corrupt workspace", (value: Record<string, unknown>) => {
      (value.workspace as Record<string, unknown>).session_name = { invalid: true };
    }],
    ["duplicate authoring ids", (value: Record<string, unknown>) => {
      (value.visual_draft as Record<string, unknown>).reserved_authoring_ids = ["x", "x"];
    }],
    ["unknown envelope field", (value: Record<string, unknown>) => {
      (value.visual_draft as Record<string, unknown>).future_field = true;
    }],
    ["non-editable window target", (value: Record<string, unknown>) => {
      value.editor = {
        windows: [{ key: "catalog", target: { kind: "catalog" }, x: 0, y: 0 }],
        buffers: {
          catalog: { opened: {}, draft: {}, dirty: true },
        },
      };
    }],
  ] as const)("refuses %s in a recovery document", (_label, mutate) => {
    const serialized = JSON.parse(
      serializeStructuredRecovery(recoveryFixture()),
    ) as Record<string, unknown>;
    mutate(serialized);
    expect(readStructuredRecovery(JSON.stringify(serialized))).toEqual({
      ok: false,
      reason: "the saved structured draft is incomplete or invalid",
    });
  });

  it("preserves an invalid stored slot when restoration refuses it", () => {
    const recovery = recoveryFixture();
    expect(writeStructuredAutosave(recovery, RECOVERY_SCOPE)).toBe(true);
    const autosaveKey = recoveryStorageKey(
      RECOVERY_SCOPE,
      "autosave",
      recovery.visualDraft.target_ref,
    );
    const raw = JSON.stringify({ v: 999, kind: "structured" });
    localStorage.setItem(autosaveKey, raw);
    expect(restoreStructuredRecovery("autosave", RECOVERY_SCOPE)).toEqual({
      ok: false,
      reason: "draft recovery version 999 is not supported",
    });
    expect(localStorage.getItem(autosaveKey)).toBe(raw);
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
      projected_yaml: "terminal:\n  id: recovered\n  display_name: Saved baseline\n",
      control_tree: {
        projection_revision: 9,
        root: {
          control_id: "ctl_root",
          json_pointer: "",
          label: "Terminal",
          required: true,
          present: true,
          model_name: "Terminal",
          fields: [],
        },
      },
      issues: [],
    },
    baselineDocument: {
      terminal: { id: "recovered", display_name: "Saved baseline" },
    },
    workingDocument: {
      terminal: { id: "recovered", display_name: "Dirty recovered value" },
    },
    yamlText: "# exact\nterminal:\n  id: recovered\n",
    appliedYamlText: "terminal:\n  id: recovered\n",
    canonicalizationRequired: true,
    canonicalizationAccepted: false,
  };

  it("round-trips fences, working copy, exact YAML, and canonicalization state", () => {
    const result = readCatalogDraftRecovery(serializeCatalogDraftRecovery(recovery));
    expect(result).toEqual({ ok: true, recovery });
  });

  it("refuses malformed catalog draft identity without compatibility", () => {
    const serialized = JSON.parse(
      serializeCatalogDraftRecovery(recovery),
    ) as Record<string, unknown>;
    (serialized.draft as Record<string, unknown>).target_ref =
      "nodalarc:terminals/recovered.yaml";
    expect(readCatalogDraftRecovery(JSON.stringify(serialized))).toEqual({
      ok: false,
      reason: "the saved component draft is incomplete or invalid",
    });
  });

  it.each([
    [
      "unknown issue stage",
      {
        code: "catalog.invalid",
        stage: "semantic",
        message: "invalid",
        pointer: "/terminal",
        blocks: ["save", "deploy"],
      },
    ],
    [
      "duplicate issue blocks",
      {
        code: "catalog.invalid",
        stage: "structural",
        message: "invalid",
        pointer: "/terminal",
        blocks: ["save", "save"],
      },
    ],
    [
      "unknown issue field",
      {
        code: "catalog.invalid",
        stage: "structural",
        message: "invalid",
        pointer: "/terminal",
        blocks: ["save", "deploy"],
        future_field: true,
      },
    ],
  ] as const)("refuses catalog recovery with %s", (_label, issue) => {
    const serialized = JSON.parse(
      serializeCatalogDraftRecovery(recovery),
    ) as Record<string, unknown>;
    (serialized.draft as Record<string, unknown>).issues = [issue];
    expect(readCatalogDraftRecovery(JSON.stringify(serialized))).toEqual({
      ok: false,
      reason: "the saved component draft is incomplete or invalid",
    });
  });

  it("preserves incomplete working state and invalid exact YAML", () => {
    const incomplete = {
      ...recovery,
      workingDocument: {},
      yamlText: "{ unfinished component",
      canonicalizationRequired: false,
    };
    expect(readCatalogDraftRecovery(serializeCatalogDraftRecovery(incomplete))).toEqual({
      ok: true,
      recovery: incomplete,
    });
  });

  it("persists across reload and clears only on explicit completion", () => {
    expect(writeCatalogDraftRecovery(recovery, RECOVERY_SCOPE)).toBe(true);
    const key = recoveryStorageKey(RECOVERY_SCOPE, "catalog", recovery.draft.target_ref);
    expect(localStorage.getItem(key)).not.toBeNull();
    expect(loadCatalogDraftRecovery(RECOVERY_SCOPE)).toEqual({ ok: true, recovery });
    clearCatalogDraftRecovery(RECOVERY_SCOPE);
    expect(localStorage.getItem(key)).toBeNull();
  });
});
