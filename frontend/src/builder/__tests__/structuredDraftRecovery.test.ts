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
import {
  defaultDraftNode,
  newDraftConstellation,
  newDraftGroundSet,
  newWorkspace,
} from "./fixtures/workspaceFixtures";
import { routingWarnings } from "../workspace";

function recoveryFixture() {
  const workspace = newWorkspace("recover-exact");
  workspace.space.push(newDraftConstellation("nodalarc:nodes/space/recover.yaml"));
  workspace.space[0]!.node_draft = defaultDraftNode();
  workspace.ground.push(newDraftGroundSet("nodalarc:nodes/ground/recover.yaml", {}));
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
    session_name_is_placeholder: false,
    reserved_authoring_ids: [
      workspace.space[0]!.segment_id,
      workspace.ground[0]!.segment_id,
    ],
    workspace,
    session_yaml: null,
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
        opened: {
          session_name: "recover-exact",
          start_time: workspace.start_time,
          step_seconds: workspace.step_seconds,
          compression: workspace.compression,
          max_pairs_per_rule: workspace.max_pairs_per_rule,
          max_pairs_per_tick: workspace.max_pairs_per_tick,
        },
        draft: {
          session_name: "recover-renamed",
          start_time: workspace.start_time,
          step_seconds: workspace.step_seconds,
          compression: workspace.compression,
          max_pairs_per_rule: workspace.max_pairs_per_rule,
          max_pairs_per_tick: workspace.max_pairs_per_tick,
        },
        dirty: true,
      },
      "segment:clean": { opened: {}, draft: {}, dirty: false },
    },
  });
  if (!recovery) throw new Error("expected structured recovery fixture");
  return recovery;
}

function serializedRecoveryFixture(): Record<string, unknown> {
  return JSON.parse(
    serializeStructuredRecovery(recoveryFixture()),
  ) as Record<string, unknown>;
}

function workspaceCopies(
  serialized: Record<string, unknown>,
): Array<Record<string, unknown>> {
  const visualDraft = serialized.visual_draft as { workspace: Record<string, unknown> };
  return [serialized.workspace as Record<string, unknown>, visualDraft.workspace];
}

function firstSpaceDraft(workspace: Record<string, unknown>): Record<string, unknown> {
  return (workspace.space as Array<Record<string, unknown>>)[0]!;
}

function expectInvalidStructuredRecovery(serialized: Record<string, unknown>): void {
  expect(readStructuredRecovery(JSON.stringify(serialized))).toEqual({
    ok: false,
    reason: "the saved structured draft is incomplete or invalid",
  });
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
      JSON.stringify({ v: 3, workspace: recoveryFixture().workspace }),
    );

    expect(result).toEqual({
      ok: false,
      reason: "draft recovery version 3 is not supported",
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

  it("requires the current reserved authoring identity inventory", () => {
    const serialized = serializedRecoveryFixture();
    const visualDraft = serialized.visual_draft as Record<string, unknown>;
    delete visualDraft.reserved_authoring_ids;

    expectInvalidStructuredRecovery(serialized);
  });

  it("preserves replacement identities while a deleted topology stays visibly dangling", () => {
    const recovery = recoveryFixture();
    const spaceId = recovery.workspace.space[0]!.segment_id;
    const groundId = recovery.workspace.ground[0]!.segment_id;
    recovery.workspace.links = [
      {
        rule_id: "link-2",
        label: "replacement link",
        enabled: true,
        a: {
          segment_id: groundId,
          tag: null,
          role: "access",
          medium: "rf",
          min_elevation_deg: 25,
        },
        b: {
          segment_id: spaceId,
          tag: null,
          role: "access",
          medium: "rf",
          min_elevation_deg: null,
        },
        topology_mode: "visible_candidates",
        topology_n: 1,
        max_range_km: null,
      },
    ];
    recovery.workspace.routing_domains = [
      {
        domain_id: "domain-2",
        label: "replacement domain",
        protocol: "isis",
        member_segment_ids: [spaceId, groundId],
        hello_interval_s: null,
        hold_interval_s: null,
      },
    ];
    recovery.workspace.boundaries = [
      {
        boundary_id: "boundary-1",
        over_rule_id: "link-1",
        adapter: "static_ip",
        from_domain_id: "domain-1",
        to_domain_id: "domain-1",
        export_node_loopbacks: true,
      },
    ];
    recovery.visualDraft = {
      ...recovery.visualDraft,
      workspace: structuredClone(recovery.workspace),
      reserved_authoring_ids: [
        spaceId,
        groundId,
        "link-1",
        "domain-1",
        "boundary-1",
        "link-2",
        "domain-2",
      ],
    };

    const result = readStructuredRecovery(serializeStructuredRecovery(recovery));
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.recovery.visualDraft.reserved_authoring_ids).toContain("link-1");
    expect(result.recovery.visualDraft.reserved_authoring_ids).toContain("domain-1");
    expect(routingWarnings(result.recovery.workspace)).toEqual(
      expect.arrayContaining([
        "a boundary rides a link rule that is no longer in the session",
        "a boundary references a routing domain that no longer exists",
      ]),
    );
  });

  it("requires the backend placeholder-name fact", () => {
    const serialized = serializedRecoveryFixture();
    const visualDraft = serialized.visual_draft as Record<string, unknown>;
    delete visualDraft.session_name_is_placeholder;

    expectInvalidStructuredRecovery(serialized);
  });

  it("accepts an explicitly incomplete phase offset", () => {
    const serialized = serializedRecoveryFixture();
    for (const workspace of workspaceCopies(serialized)) {
      firstSpaceDraft(workspace).phase_offset_deg = null;
    }

    expect(readStructuredRecovery(JSON.stringify(serialized)).ok).toBe(true);
  });

  it("refuses identical object corruption in both workspace copies", () => {
    const serialized = serializedRecoveryFixture();
    for (const workspace of workspaceCopies(serialized)) {
      firstSpaceDraft(workspace).display_name = { invalid: true };
    }

    expectInvalidStructuredRecovery(serialized);
  });

  it.each([
    ["numeric role", "role", 42],
    ["string count", "count", "1"],
  ] as const)("refuses an identical terminal %s in both workspace copies", (_label, key, value) => {
    const serialized = serializedRecoveryFixture();
    for (const workspace of workspaceCopies(serialized)) {
      const node = firstSpaceDraft(workspace).node_draft as Record<string, unknown>;
      const mount: Record<string, unknown> = {
        mount_id: "access-1",
        role: "access",
        terminal_ref: "nodalarc:terminals/rf/access.yaml",
        count: 1,
        boresight: { mode: "nadir" },
      };
      mount[key] = value;
      node.terminals = [mount];
    }

    expectInvalidStructuredRecovery(serialized);
  });

  it.each(["draft", "opened"] as const)(
    "refuses malformed session buffer %s values",
    (side) => {
      const serialized = serializedRecoveryFixture();
      const editor = serialized.editor as {
        buffers: { session: Record<string, unknown> };
      };
      const buffer = editor.buffers.session[side] as Record<string, unknown>;
      buffer.step_seconds = "1";

      expectInvalidStructuredRecovery(serialized);
    },
  );

  it("refuses a session buffer missing one owned field", () => {
    const serialized = serializedRecoveryFixture();
    const editor = serialized.editor as {
      buffers: { session: { draft: Record<string, unknown> } };
    };
    delete editor.buffers.session.draft.compression;

    expectInvalidStructuredRecovery(serialized);
  });

  it.each(["draft", "opened"] as const)(
    "refuses malformed object buffer %s values",
    (side) => {
      const recovery = recoveryFixture();
      const segment = structuredClone(recovery.workspace.space[0]!);
      const key = `segment:${segment.segment_id}`;
      recovery.editor = {
        windows: [
          {
            key,
            target: { kind: "segment", id: segment.segment_id },
            x: 40,
            y: 50,
          },
        ],
        buffers: {
          [key]: {
            draft: structuredClone(segment),
            opened: structuredClone(segment),
            dirty: true,
          },
        },
      };
      const serialized = JSON.parse(
        serializeStructuredRecovery(recovery),
      ) as Record<string, unknown>;
      const editor = serialized.editor as {
        buffers: Record<string, Record<string, unknown>>;
      };
      const buffer = editor.buffers[key]![side] as Record<string, unknown>;
      buffer.planes = "3";

      expectInvalidStructuredRecovery(serialized);
    },
  );

  it("refuses buffers for non-editable window targets", () => {
    const serialized = serializedRecoveryFixture();
    const editor = serialized.editor as {
      windows: Array<Record<string, unknown>>;
      buffers: Record<string, unknown>;
    };
    const sessionBuffer = editor.buffers.session;
    editor.windows = [
      { key: "library", target: { kind: "library" }, x: 20, y: 30 },
    ];
    editor.buffers = { library: sessionBuffer };

    expectInvalidStructuredRecovery(serialized);
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

  it.each([
    ["space segment id", ["space", 0, "segment_id"]],
    ["space segment label", ["space", 0, "display_name"]],
    ["inline node ethernet", ["space", 0, "node_draft", "ethernet"]],
    ["inline node terminals", ["space", 0, "node_draft", "terminals"]],
    ["orbit central body", ["space", 0, "orbit", "central_body"]],
    ["ground stamp installations", ["ground", 0, "stamp", "installed"]],
    ["ground stamp boresights", ["ground", 0, "stamp", "boresights"]],
    ["ground member inventory", ["ground", 0, "members"]],
  ] as const)("refuses recovery missing %s from both workspace copies", (_label, path) => {
    const serialized = JSON.parse(
      serializeStructuredRecovery(recoveryFixture()),
    ) as Record<string, unknown>;
    const applied = serialized.workspace as Record<string, unknown>;
    const visualDraft = serialized.visual_draft as {
      workspace: Record<string, unknown>;
    };

    const removePath = (root: Record<string, unknown>) => {
      let current: unknown = root;
      for (const part of path.slice(0, -1)) {
        if (typeof part === "number") {
          if (!Array.isArray(current)) throw new Error("expected recovery array");
          current = current[part];
        } else {
          if (current === null || typeof current !== "object" || Array.isArray(current)) {
            throw new Error("expected recovery object");
          }
          current = (current as Record<string, unknown>)[part];
        }
      }
      const final = path[path.length - 1]!;
      if (typeof final === "number") {
        if (!Array.isArray(current)) throw new Error("expected recovery array");
        current.splice(final, 1);
      } else {
        if (current === null || typeof current !== "object" || Array.isArray(current)) {
          throw new Error("expected recovery object");
        }
        delete (current as Record<string, unknown>)[final];
      }
    };

    removePath(applied);
    removePath(visualDraft.workspace);

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

  const serializedCatalogRecovery = (): Record<string, unknown> =>
    JSON.parse(
      serializeCatalogDraftRecovery(structuredClone(recovery)),
    ) as Record<string, unknown>;

  const expectInvalidCatalogRecovery = (serialized: Record<string, unknown>) => {
    expect(readCatalogDraftRecovery(JSON.stringify(serialized))).toEqual({
      ok: false,
      reason: "the saved component draft is incomplete or invalid",
    });
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

  it("preserves incomplete working documents and invalid advanced text", () => {
    const incomplete = {
      ...recovery,
      workingDocument: {},
      advancedText: "{ unfinished component",
    };

    expect(
      readCatalogDraftRecovery(serializeCatalogDraftRecovery(incomplete)),
    ).toEqual({ ok: true, recovery: incomplete });
  });

  it.each([
    ["unknown family", (draft: Record<string, unknown>) => {
      draft.family = "routers";
    }],
    ["family/ref mismatch", (draft: Record<string, unknown>) => {
      draft.family = "nodes";
    }],
    ["non-user target", (draft: Record<string, unknown>) => {
      draft.target_ref = "nodalarc:terminals/recovered.yaml";
    }],
    ["negative revision", (draft: Record<string, unknown>) => {
      draft.draft_revision = -1;
    }],
    ["empty target revision", (draft: Record<string, unknown>) => {
      draft.expected_target_revision = "";
    }],
    ["source without revision", (draft: Record<string, unknown>) => {
      draft.expected_source_revision = null;
    }],
    ["revision without source", (draft: Record<string, unknown>) => {
      draft.source_ref = null;
    }],
    ["source family mismatch", (draft: Record<string, unknown>) => {
      draft.source_ref = "nodalarc:nodes/source.yaml";
    }],
    ["unknown envelope field", (draft: Record<string, unknown>) => {
      draft.future_field = true;
    }],
  ] as const)("refuses component recovery with %s", (_label, mutate) => {
    const serialized = serializedCatalogRecovery();
    const draft = serialized.draft as Record<string, unknown>;
    mutate(draft);

    expectInvalidCatalogRecovery(serialized);
  });

  it.each([
    ["unknown issue stage", {
      code: "catalog.invalid",
      stage: "semantic",
      message: "invalid",
      pointer: "/terminal",
      blocks: ["save", "deploy"],
    }],
    ["empty issue code", {
      code: "",
      stage: "structural",
      message: "invalid",
      pointer: "/terminal",
      blocks: ["save", "deploy"],
    }],
    ["wrong runtime-support blocks", {
      code: "catalog.unsupported",
      stage: "runtime_support",
      message: "unsupported",
      pointer: "/terminal",
      blocks: ["save"],
    }],
    ["duplicate issue blocks", {
      code: "catalog.invalid",
      stage: "structural",
      message: "invalid",
      pointer: "/terminal",
      blocks: ["save", "save"],
    }],
    ["unknown issue field", {
      code: "catalog.invalid",
      stage: "structural",
      message: "invalid",
      pointer: "/terminal",
      blocks: ["save", "deploy"],
      future_field: true,
    }],
  ] as const)("refuses component recovery with %s", (_label, issue) => {
    const serialized = serializedCatalogRecovery();
    const draft = serialized.draft as Record<string, unknown>;
    draft.issues = [issue];

    expectInvalidCatalogRecovery(serialized);
  });

  it("persists across reload and clears only on explicit completion or discard", () => {
    expect(writeCatalogDraftRecovery(recovery)).toBe(true);
    expect(localStorage.getItem(CATALOG_DRAFT_RECOVERY_KEY)).not.toBeNull();
    expect(loadCatalogDraftRecovery()).toEqual({ ok: true, recovery });

    clearCatalogDraftRecovery();
    expect(localStorage.getItem(CATALOG_DRAFT_RECOVERY_KEY)).toBeNull();
  });
});
