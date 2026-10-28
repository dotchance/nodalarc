/** Session coordinator contract: one backend-issued revision stream. */

import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type {
  BuilderVisualDraftAssemblyResult,
  BuilderVisualDraftEnvelope,
  BuilderVisualLinkRuleWidget,
  BuilderVisualObjectEditResult,
  CatalogDocumentSummary,
  JsonValue,
} from "../generated/builderApi";
import { newWorkspace } from "./fixtures/workspaceFixtures";

vi.mock("../../config", () => ({
  REST_URL: "http://test:8080",
  authHeaders: (extra?: Record<string, string>) => ({ ...extra }),
}));

const {
  claimOutlineReveal,
  requestOutlineReveal,
  useBuilderWorld,
  useOutlineReveal,
  yamlBlocksGraphicalEditing,
} = await import("../useBuilderWorld");

function response(payload: unknown) {
  return {
    ok: true,
    status: 200,
    json: () => Promise.resolve(payload),
  };
}

const sessionsResponse = response({
  generation: "catalog-generation",
  items: [],
  next_page_token: null,
});

function draft(revision = 4): BuilderVisualDraftEnvelope {
  const workspace = newWorkspace("coordinated");
  return {
    draft_revision: revision,
    projection_status: "applied",
    target_ref: "user:sessions/coordinated.yaml",
    source_ref: "user:sessions/coordinated.yaml",
    expected_session_revision: "session-revision",
    catalog_documents: [],
    session_name_is_placeholder: false,
    reserved_authoring_ids: [],
    session_yaml: "session:\n  name: coordinated\n",
    authoring_workspace: workspace,
    applied_workspace: workspace,
    applied_revision: revision,
    applied_session: { session: { name: "coordinated" } },
  };
}

function assembly(
  visualDraft: BuilderVisualDraftEnvelope,
  marker = `revision-${visualDraft.draft_revision}`,
): BuilderVisualDraftAssemblyResult {
  const assembledDraft = {
    draft_revision: visualDraft.draft_revision,
    state: {
      session: visualDraft.applied_session ?? { session: { name: "coordinated" } },
      catalog_documents: visualDraft.catalog_documents ?? [],
    },
  };
  return {
    visual_draft: visualDraft,
    assembled_draft: assembledDraft,
    save_request: {
      draft: assembledDraft,
      target_ref: visualDraft.target_ref,
      expected_session_revision: visualDraft.expected_session_revision,
    },
    compile_result: {
      draft: assembledDraft,
      target_ref: visualDraft.target_ref,
      canonical_session_yaml: visualDraft.session_yaml,
      canonical_session_json: visualDraft.applied_session,
      dependency_closure: {
        entries: [],
        file_count: 0,
        total_bytes: 0,
        closure_digest: `dependency-${marker}`,
      },
      resolved_preview: {
        marker,
        session: { name: "coordinated" },
        nodes: [],
        segments: [],
        link_rules: [],
        routing_domains: [],
        boundaries: [],
        ephemeris: { epoch: "2026-01-01T00:00:00Z", nodes: {} },
      },
      digests: {
        document: `document-${marker}`,
        dependency: `dependency-${marker}`,
      },
      issues: [],
      save_verdict: { operation: "save", allowed: true, blockers: [] },
      deploy_eligibility_after_save: {
        operation: "deploy",
        allowed: true,
        blockers: [],
      },
    },
    assembly_issues: [],
  } as unknown as BuilderVisualDraftAssemblyResult;
}

/** A link rule as the accepted session holds it. */
const ACCEPTED_RULE: Readonly<Record<string, JsonValue>> = {
  enabled: true,
  endpoints: [
    { select: { segment: "leo" }, terminal: { all: [{ role: "isl" }, { medium: "optical" }] } },
    { select: { segment: "leo" }, terminal: { all: [{ role: "isl" }, { medium: "optical" }] } },
  ],
  id: "leo_isl",
  topology: { mode: "visible_candidates" },
};

const RULE_ADDRESS = { kind: "member", list_pointer: "/link_rules", id: "leo_isl" } as const;

function ruleCard(maxRangeKm: number): BuilderVisualLinkRuleWidget {
  return {
    kind: "link_rule",
    rule: {
      rule_id: "leo_isl",
      enabled: true,
      a: { segment_id: "leo", role: "isl", medium: "optical" },
      b: { segment_id: "leo", role: "isl", medium: "optical" },
      topology_mode: "visible_candidates",
      max_range_km: maxRangeKm,
    },
  };
}

/** A draft whose accepted session holds the link rule. */
function linkDraft(revision = 4): BuilderVisualDraftEnvelope {
  return {
    ...draft(revision),
    applied_session: { session: { name: "coordinated" }, link_rules: [ACCEPTED_RULE] },
  };
}

/** The backend's answer for the rule with `maxRangeKm` set. */
function ruleAnswer(maxRangeKm: number): BuilderVisualObjectEditResult {
  return {
    address: RULE_ADDRESS,
    working: { ...ACCEPTED_RULE, max_range_km: maxRangeKm },
    widget: ruleCard(maxRangeKm),
    control_tree: {
      projection_revision: 4,
      root: {
        control_id: "link-rule",
        json_pointer: "",
        label: "Link rule",
        required: true,
        present: true,
      },
    },
    issues: [],
  };
}

function requestsTo(fetchMock: ReturnType<typeof vi.fn>, path: string): unknown[] {
  return fetchMock.mock.calls
    .filter(([url]) => String(url).includes(path))
    .map(([, init]) => JSON.parse(String((init as RequestInit).body)));
}

/** Open `opened` the way the page does: open the stored session, then compile it. */
async function openAsThePageDoes(
  hook: { current: { openSession: (entry: CatalogDocumentSummary) => Promise<unknown> } },
  fetchMock: ReturnType<typeof vi.fn>,
  opened: BuilderVisualDraftEnvelope,
): Promise<void> {
  fetchMock.mockImplementation((url: string) => {
    if (url.includes("/builder/catalog/list")) return Promise.resolve(sessionsResponse);
    if (url.includes("/builder/draft/open")) return Promise.resolve(response(opened));
    if (url.includes("/builder/draft/compile")) return Promise.resolve(response(assembly(opened)));
    throw new Error(`unexpected request ${url}`);
  });
  await act(async () => {
    await hook.current.openSession({
      ref: opened.target_ref,
      family: "sessions",
      namespace: "user",
      revision: "session-revision",
      size_bytes: opened.session_yaml.length,
      display_name: "coordinated",
      summary: "",
    });
  });
}

describe("useBuilderWorld session coordinator", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    localStorage.clear();
    fetchMock = vi.fn((url: string) => {
      if (url.includes("/builder/catalog/list")) return Promise.resolve(sessionsResponse);
      throw new Error(`unexpected request ${url}`);
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;
  });

  it("rejects a stale save capture before persistence", async () => {
    const original = draft();
    const { result } = renderHook(() => useBuilderWorld());
    await act(async () => undefined);
    await openAsThePageDoes(result, fetchMock, original);
    const capture = result.current.captureCoordinator();
    act(() => result.current.editYamlBuffer("session:\n  name: changed\n"));
    await act(async () => {
      await expect(
        result.current.saveSession(assembly(original).save_request, capture),
      ).rejects.toThrow("session changed");
    });
    expect(
      fetchMock.mock.calls.some(([url]) => String(url).includes("/builder/session/save")),
    ).toBe(false);
  });

  it("previews window edits through the compile and accepts nothing", async () => {
    const original = draft();
    const { result } = renderHook(() => useBuilderWorld());
    await act(async () => undefined);
    await openAsThePageDoes(result, fetchMock, original);
    const accepted = result.current.visualDraft;
    const acceptedAssembly = result.current.assemblyResult;
    const edited = { ...original.authoring_workspace!, display_name: "previewed" };
    fetchMock.mockImplementation((url: string) => {
      if (url.includes("/builder/draft/compile")) {
        return Promise.resolve(response(assembly(original, "preview")));
      }
      throw new Error(`unexpected request ${url}`);
    });

    await act(async () => {
      await result.current.compilePreview(edited, () => true);
    });

    const [url, init] = fetchMock.mock.calls[fetchMock.mock.calls.length - 1]!;
    expect(String(url)).toContain("/builder/draft/compile");
    const body = JSON.parse(String((init as RequestInit).body));
    expect(body.workspace.display_name).toBe("previewed");
    expect(body.draft.draft_revision).toBe(original.draft_revision);
    expect((result.current.world as unknown as { marker: string }).marker).toBe("preview");
    expect(result.current.visualDraft).toBe(accepted);
    expect(result.current.assemblyResult).toBe(acceptedAssembly);
  });

  it("drops a preview the windows moved past", async () => {
    const original = draft();
    const { result } = renderHook(() => useBuilderWorld());
    await act(async () => undefined);
    await openAsThePageDoes(result, fetchMock, original);
    const shown = result.current.world;
    fetchMock.mockImplementation((url: string) => {
      if (url.includes("/builder/draft/compile")) {
        return Promise.resolve(response(assembly(original, "preview")));
      }
      throw new Error(`unexpected request ${url}`);
    });

    await act(async () => {
      await result.current.compilePreview(original.authoring_workspace!, () => false);
    });

    expect(result.current.world).toBe(shown);
  });

  it("blocks commands and customization while YAML is unapplied", async () => {
    const original = draft();
    const { result } = renderHook(() => useBuilderWorld());
    await act(async () => undefined);
    await openAsThePageDoes(result, fetchMock, original);
    expect(yamlBlocksGraphicalEditing(true, result.current.yamlBuffer)).toBe(false);
    act(() => result.current.editYamlBuffer("session:\n  name: dirty\n"));
    await act(async () => {
      await expect(
        result.current.runVisualCommand({
          operation: "add_generated_space",
          phasing_mode: "walker_delta",
        }),
      ).rejects.toThrow("apply or canonicalize");
      await expect(
        result.current.customizeChain({
          segment_id: "space-1",
          leaf_ref: "nodalarc:nodes/space/relay.yaml",
        }),
      ).rejects.toThrow("apply or canonicalize");
    });
    expect(
      fetchMock.mock.calls.some(([url]) =>
        String(url).includes("/builder/draft/command") ||
        String(url).includes("/builder/draft/customize-chain"),
      ),
    ).toBe(false);
  });
});

describe("pending link and routing edits", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn((url: string) => {
      if (url.includes("/builder/catalog/list")) return Promise.resolve(sessionsResponse);
      throw new Error(`unexpected request ${url}`);
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;
  });

  function answerObjectEdits(...answers: BuilderVisualObjectEditResult[]): void {
    fetchMock.mockImplementation((url: string) => {
      if (url.includes("/builder/draft/object-edit")) {
        const answer = answers.shift();
        if (answer) return Promise.resolve(response(answer));
      }
      throw new Error(`unexpected request ${url}`);
    });
  }

  it("holds each object's edit beside the accepted session with its latest answer", async () => {
    const original = linkDraft();
    const { result } = renderHook(() => useBuilderWorld());
    await act(async () => undefined);
    await openAsThePageDoes(result, fetchMock, original);
    const accepted = result.current.visualDraft;
    answerObjectEdits(ruleAnswer(12345), ruleAnswer(500));

    await act(async () => {
      await result.current.editPendingObject(RULE_ADDRESS, ruleCard(12345));
    });
    await act(async () => {
      await result.current.editPendingObject(RULE_ADDRESS, ruleCard(500));
    });

    const [first, second] = requestsTo(fetchMock, "/builder/draft/object-edit");
    // The first edit starts from the rule as the accepted session holds it.
    expect(first).toEqual({
      target_ref: original.target_ref,
      applied_revision: 4,
      address: RULE_ADDRESS,
      working: ACCEPTED_RULE,
      change: ruleCard(12345),
    });
    // A later edit builds on the working JSON the latest answer holds.
    expect((second as { working: JsonValue }).working).toEqual({
      ...ACCEPTED_RULE,
      max_range_km: 12345,
    });
    expect([...result.current.pendingEdits.values()]).toEqual([
      {
        edit: {
          kind: "content",
          address: RULE_ADDRESS,
          base: ACCEPTED_RULE,
          working: { ...ACCEPTED_RULE, max_range_km: 500 },
        },
        answer: ruleAnswer(500),
      },
    ]);
    expect(result.current.visualDraft).toBe(accepted);
  });

  it("does not hold an answer a newer edit of the same object overtook", async () => {
    const { result } = renderHook(() => useBuilderWorld());
    await act(async () => undefined);
    await openAsThePageDoes(result, fetchMock, linkDraft());
    let answerFirst!: () => void;
    let calls = 0;
    fetchMock.mockImplementation((url: string) => {
      if (url.includes("/builder/draft/object-edit")) {
        calls += 1;
        if (calls === 1) {
          return new Promise((resolve) => {
            answerFirst = () => resolve(response(ruleAnswer(1)));
          });
        }
        return Promise.resolve(response(ruleAnswer(2)));
      }
      throw new Error(`unexpected request ${url}`);
    });

    let first!: Promise<unknown>;
    await act(async () => {
      first = result.current.editPendingObject(RULE_ADDRESS, ruleCard(1));
      await result.current.editPendingObject(RULE_ADDRESS, ruleCard(2));
    });
    await act(async () => {
      answerFirst();
      await expect(first).rejects.toThrow("changed while");
    });

    expect([...result.current.pendingEdits.values()].map((pending) => pending.answer)).toEqual([
      ruleAnswer(2),
    ]);
  });

  it("locks the YAML buffer and the session pane while an edit is pending", async () => {
    const { result } = renderHook(() => useBuilderWorld());
    await act(async () => undefined);
    await openAsThePageDoes(result, fetchMock, linkDraft());
    answerObjectEdits(ruleAnswer(12345));
    await act(async () => {
      await result.current.editPendingObject(RULE_ADDRESS, ruleCard(12345));
    });
    const buffer = result.current.yamlBuffer;

    act(() => {
      expect(result.current.editYamlBuffer("session:\n  name: typed\n")).toBe(false);
    });
    await act(async () => {
      await expect(result.current.applyYamlBuffer()).rejects.toThrow(
        "apply or discard the pending link or routing edit before applying the YAML",
      );
      await expect(result.current.useCanonicalYaml()).rejects.toThrow(
        "apply or discard the pending link or routing edit before applying the YAML",
      );
      await expect(
        result.current.mutateControls([
          { operation: "set_scalar", control_id: "session-description", value: "typed" },
        ]),
      ).rejects.toThrow(
        "apply or discard the pending link or routing edit before editing the session fields",
      );
    });

    expect(result.current.yamlBuffer).toBe(buffer);
    expect(requestsTo(fetchMock, "/builder/draft/apply-yaml")).toEqual([]);
    expect(requestsTo(fetchMock, "/builder/draft/control-mutate")).toEqual([]);
  });

  it("refuses an edit while the YAML buffer is unapplied", async () => {
    const { result } = renderHook(() => useBuilderWorld());
    await act(async () => undefined);
    await openAsThePageDoes(result, fetchMock, linkDraft());
    act(() => result.current.editYamlBuffer("session:\n  name: typed\n"));

    await act(async () => {
      await expect(
        result.current.editPendingObject(RULE_ADDRESS, ruleCard(12345)),
      ).rejects.toThrow("apply or canonicalize the YAML buffer");
    });

    expect(requestsTo(fetchMock, "/builder/draft/object-edit")).toEqual([]);
    expect(result.current.pendingEdits.size).toBe(0);
  });

  it("edits only objects of an accepted session", async () => {
    const { result } = renderHook(() => useBuilderWorld());
    await act(async () => undefined);
    await openAsThePageDoes(result, fetchMock, {
      ...draft(0),
      projection_status: "incomplete_authoring",
      applied_revision: null,
      applied_session: null,
    });
    await act(async () => {
      await expect(
        result.current.editPendingObject(RULE_ADDRESS, ruleCard(12345)),
      ).rejects.toThrow("edited on an accepted session");
    });

    await openAsThePageDoes(result, fetchMock, linkDraft());
    await act(async () => {
      await expect(
        result.current.editPendingObject(
          { kind: "member", list_pointer: "/routing/domains", id: "isis" },
          {
            kind: "routing_domain",
            domain: { domain_id: "isis", protocol: "isis", member_segment_ids: ["leo"] },
          },
        ),
      ).rejects.toThrow("the accepted session holds no isis in /routing/domains");
    });

    expect(requestsTo(fetchMock, "/builder/draft/object-edit")).toEqual([]);
  });

  it("drops the pending edits when another session opens", async () => {
    const { result } = renderHook(() => useBuilderWorld());
    await act(async () => undefined);
    await openAsThePageDoes(result, fetchMock, linkDraft());
    answerObjectEdits(ruleAnswer(12345));
    await act(async () => {
      await result.current.editPendingObject(RULE_ADDRESS, ruleCard(12345));
    });
    expect(result.current.pendingEdits.size).toBe(1);

    await openAsThePageDoes(result, fetchMock, draft(9));

    expect(result.current.pendingEdits.size).toBe(0);
  });
});

describe("outline reveal", () => {
  it("consumes each outline reveal once", () => {
    const { result } = renderHook(() => useOutlineReveal());
    act(() => requestOutlineReveal("space-777"));
    expect(claimOutlineReveal("outline", result.current)?.segmentId).toBe("space-777");
    expect(claimOutlineReveal("outline", result.current)).toBeNull();
  });
});
