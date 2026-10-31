/** Session coordinator contract: one backend-issued revision stream. */

import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type {
  BuilderCompileResult,
  BuilderVisualDraftAcceptStateRequest,
  BuilderVisualDraftAssemblyResult,
  BuilderVisualDraftCommandResult,
  BuilderVisualDraftEnvelope,
  BuilderVisualLinkRuleWidget,
  BuilderVisualObjectEditResult,
  CatalogDocumentSummary,
  JsonValue,
} from "../generated/builderApi";
import { workingIssue } from "./fixtures/listingFixtures";

vi.mock("../../config", () => ({
  REST_URL: "http://test:8080",
  authHeaders: (extra?: Record<string, string>) => ({ ...extra }),
}));

const {
  claimOutlineReveal,
  pendingEditKey,
  refusalMessage,
  requestOutlineReveal,
  useBuilderWorld,
  useOutlineReveal,
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
  return {
    draft_revision: revision,
    target_ref: "user:sessions/coordinated.yaml",
    source_ref: "user:sessions/coordinated.yaml",
    expected_session_revision: "session-revision",
    catalog_documents: [],
    session_name_is_placeholder: false,
    reserved_authoring_ids: [],
    session_yaml: "session:\n  name: coordinated\n",
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

/** The backend's description of `working` at `address`, which no card states. */
function described(
  address: BuilderVisualObjectEditResult["address"],
  working: BuilderVisualObjectEditResult["working"],
): BuilderVisualObjectEditResult {
  return {
    address,
    working,
    widget: null,
    control_tree: {
      projection_revision: 4,
      root: {
        control_id: "object",
        json_pointer: "",
        label: "Object",
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

/** The backend's answer to a command: `draft` is the next accepted revision,
 *  or, when the answer names created edits, the draft the command left as it
 *  was. */
function commandAnswer(
  draft: BuilderVisualDraftEnvelope,
  answer: Partial<BuilderVisualDraftCommandResult> = {},
): BuilderVisualDraftCommandResult {
  const created = answer.created_edits ?? [];
  return {
    operation: "add_generated_space",
    base_draft_revision: created.length > 0 ? draft.draft_revision : draft.draft_revision - 1,
    draft,
    affected_kind: "space",
    affected_id: "space-1",
    scheduling_preset: null,
    notice: null,
    created_edits: created,
    ...answer,
  };
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

  // The live VS-API answers the writers queued ahead of a save within milliseconds, so a capture that goes stale before the save's turn cannot be produced on demand.
  it("rejects a stale save capture before persistence", async () => {
    const original = draft();
    const { result } = renderHook(() => useBuilderWorld());
    await act(async () => undefined);
    await openAsThePageDoes(result, fetchMock, original);
    const capture = result.current.captureCoordinator();
    act(() => result.current.editYamlBuffer("session:\n  name: changed\n"));
    await act(async () => {
      await expect(
        result.current.saveSession(
          {
            draft: {
              draft_revision: original.draft_revision,
              state: { session: original.applied_session!, catalog_documents: [] },
            },
            target_ref: original.target_ref,
            expected_session_revision: original.expected_session_revision ?? null,
          },
          capture,
        ),
      ).rejects.toThrow("session changed");
    });
    expect(
      fetchMock.mock.calls.some(([url]) => String(url).includes("/builder/session/save")),
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

  // The live VS-API answers object edits in order within milliseconds, so a first answer that arrives after a second one cannot be produced on demand.
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
});

const RULE_KEY = pendingEditKey(RULE_ADDRESS);

describe("pending edit preview", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn((url: string) => {
      if (url.includes("/builder/catalog/list")) return Promise.resolve(sessionsResponse);
      throw new Error(`unexpected request ${url}`);
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;
  });

  /** The compile of the accepted session with the pending edits applied. */
  function previewCompile(marker: string): BuilderCompileResult {
    return {
      ...assembly(linkDraft(), marker).compile_result,
      pending_edit_outcomes: [{ status: "applied", issues: [] }],
    };
  }

  /** Open the link draft and hold one pending edit of its rule. */
  async function openWithPendingEdit(hook: {
    current: ReturnType<typeof useBuilderWorld>;
  }): Promise<void> {
    await openAsThePageDoes(hook, fetchMock, linkDraft());
    fetchMock.mockImplementation((url: string) => {
      if (url.includes("/builder/draft/object-edit")) {
        return Promise.resolve(response(ruleAnswer(12345)));
      }
      throw new Error(`unexpected request ${url}`);
    });
    await act(async () => {
      await hook.current.editPendingObject(RULE_ADDRESS, ruleCard(12345));
    });
  }

  /** Hold the preview compile until the test answers it; `otherwise` answers
   *  every other request. */
  function holdCompile(otherwise: (url: string) => Promise<unknown>): { answer: () => void } {
    let answer: (() => void) | null = null;
    fetchMock.mockImplementation((url: string) => {
      if (url.includes("/builder/compile")) {
        return new Promise((resolve) => {
          answer = () => resolve(response(previewCompile("pending-preview")));
        });
      }
      return otherwise(url);
    });
    return {
      answer: () => {
        if (answer === null) throw new Error("no preview compile was asked for");
        answer();
      },
    };
  }

  // The live VS-API answers the preview compile within milliseconds, so a preview reply held while another edit lands cannot be produced on demand.
  it("drops a preview the pending edits moved past", async () => {
    const { result } = renderHook(() => useBuilderWorld());
    await act(async () => undefined);
    await openWithPendingEdit(result);
    const shown = result.current.world;
    const compile = holdCompile((url) => {
      if (url.includes("/builder/draft/object-edit")) {
        return Promise.resolve(response(ruleAnswer(500)));
      }
      throw new Error(`unexpected request ${url}`);
    });

    let preview!: Promise<void>;
    await act(async () => {
      preview = result.current.previewPendingEdits();
      await result.current.editPendingObject(RULE_ADDRESS, ruleCard(500));
    });
    await act(async () => {
      compile.answer();
      await preview;
    });

    expect(requestsTo(fetchMock, "/builder/compile")).toHaveLength(1);
    expect(result.current.world).toBe(shown);
  });

  // The live VS-API answers the preview compile within milliseconds, so a preview reply held while a command is accepted cannot be produced on demand.
  it("drops a preview the accepted draft moved past", async () => {
    const { result } = renderHook(() => useBuilderWorld());
    await act(async () => undefined);
    await openWithPendingEdit(result);
    const commanded = linkDraft(5);
    const compile = holdCompile((url) => {
      if (url.includes("/builder/draft/command")) {
        return Promise.resolve(response(commandAnswer(commanded)));
      }
      if (url.includes("/builder/draft/compile")) {
        return Promise.resolve(response(assembly(commanded)));
      }
      throw new Error(`unexpected request ${url}`);
    });

    let preview!: Promise<void>;
    await act(async () => {
      preview = result.current.previewPendingEdits();
      await result.current.runVisualCommand({
        operation: "add_generated_space",
        phasing_mode: "walker_delta",
      });
    });
    const shown = result.current.world;
    await act(async () => {
      compile.answer();
      await preview;
    });

    expect(requestsTo(fetchMock, "/builder/compile")).toHaveLength(1);
    expect(result.current.pendingEdits.size).toBe(1);
    expect((shown as unknown as { marker: string }).marker).toBe("revision-5");
    expect(result.current.world).toBe(shown);
  });
});

describe("applying, discarding and removing pending edits", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn((url: string) => {
      if (url.includes("/builder/catalog/list")) return Promise.resolve(sessionsResponse);
      throw new Error(`unexpected request ${url}`);
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;
  });

  /** Open `opened` and hold one pending edit of its rule. */
  async function openWithPendingEdit(
    hook: { current: ReturnType<typeof useBuilderWorld> },
    opened: BuilderVisualDraftEnvelope = linkDraft(),
  ): Promise<void> {
    await openAsThePageDoes(hook, fetchMock, opened);
    fetchMock.mockImplementation((url: string) => {
      if (url.includes("/builder/draft/object-edit")) {
        return Promise.resolve(response(ruleAnswer(12345)));
      }
      throw new Error(`unexpected request ${url}`);
    });
    await act(async () => {
      await hook.current.editPendingObject(RULE_ADDRESS, ruleCard(12345));
    });
  }

  // The live VS-API answers an object edit within milliseconds, so an answer still in flight when the edit is discarded cannot be produced on demand.
  it("discards an edit, and an answer still on its way for it is not held", async () => {
    const { result } = renderHook(() => useBuilderWorld());
    await act(async () => undefined);
    await openWithPendingEdit(result);
    let answerLate!: () => void;
    fetchMock.mockImplementation((url: string) => {
      if (url.includes("/builder/draft/object-edit")) {
        return new Promise((resolve) => {
          answerLate = () => resolve(response(ruleAnswer(500)));
        });
      }
      throw new Error(`unexpected request ${url}`);
    });

    let late!: Promise<unknown>;
    act(() => {
      late = result.current.editPendingObject(RULE_ADDRESS, ruleCard(500));
    });
    act(() => result.current.discardPendingEdit(RULE_ADDRESS));
    expect(result.current.pendingEdits.size).toBe(0);
    await act(async () => {
      answerLate();
      await expect(late).rejects.toThrow("changed while");
    });

    expect(result.current.pendingEdits.size).toBe(0);
    expect(requestsTo(fetchMock, "/builder/draft/apply-edits")).toEqual([]);
  });
});

describe("commands and the pending edits", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn((url: string) => {
      if (url.includes("/builder/catalog/list")) return Promise.resolve(sessionsResponse);
      throw new Error(`unexpected request ${url}`);
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;
  });

  const REDERIVE = {
    operation: "rederive_link",
    rule_id: "leo_isl",
    side: "a",
    segment_id: "geo",
  } as const;

  /** A draft whose accepted session holds `rules`. */
  function rulesDraft(
    revision: number,
    rules: ReadonlyArray<Readonly<Record<string, JsonValue>>>,
  ): BuilderVisualDraftEnvelope {
    return {
      ...linkDraft(revision),
      session_yaml: `session:\n  name: revision-${revision}\n`,
      applied_session: { session: { name: "coordinated" }, link_rules: [...rules] },
    };
  }

  /** Hold an edit of `address`, which the backend answers with `answer`. */
  async function holdEdit(
    hook: { current: ReturnType<typeof useBuilderWorld> },
    address: BuilderVisualObjectEditResult["address"],
    change: Parameters<ReturnType<typeof useBuilderWorld>["editPendingObject"]>[1],
    answer: BuilderVisualObjectEditResult,
  ): Promise<void> {
    fetchMock.mockImplementation((url: string) => {
      if (url.includes("/builder/draft/object-edit")) return Promise.resolve(response(answer));
      throw new Error(`unexpected request ${url}`);
    });
    await act(async () => {
      await hook.current.editPendingObject(address, change);
    });
  }

  /** The rule with its second endpoint moved to `geo` and no terminal there yet. */
  const INCOMPLETE_RULE: Readonly<Record<string, JsonValue>> = {
    ...ACCEPTED_RULE,
    max_range_km: 12345,
    endpoints: [
      { select: { segment: "leo" }, terminal: { all: [{ role: "isl" }, { medium: "optical" }] } },
      { select: { segment: "geo" } },
    ],
  };

  /** Run a rederive that the backend answers with `answer` only after the
   *  rule's pending edit changed to the one answered `ruleAnswer(500)`. */
  async function rederiveWhileTheEditChanges(
    hook: { current: ReturnType<typeof useBuilderWorld> },
    answer: BuilderVisualDraftCommandResult,
  ): Promise<{ command: Promise<unknown>; changed: ReadonlyMap<string, unknown> }> {
    let answerCommand: (() => void) | null = null;
    fetchMock.mockImplementation((url: string, init: RequestInit) => {
      if (url.includes("/builder/draft/command")) {
        return new Promise((resolve) => {
          answerCommand = () => resolve(response(answer));
        });
      }
      if (url.includes("/builder/draft/compile")) {
        return Promise.resolve(response(assembly(answer.draft)));
      }
      if (url.includes("/builder/draft/object-edit")) {
        const body = JSON.parse(String(init.body)) as { change?: unknown };
        return Promise.resolve(
          response(body.change ? ruleAnswer(500) : described(RULE_ADDRESS, INCOMPLETE_RULE)),
        );
      }
      throw new Error(`unexpected request ${url}`);
    });
    let command!: Promise<unknown>;
    await act(async () => {
      command = hook.current.runVisualCommand(REDERIVE);
      command.catch(() => undefined);
    });
    await act(async () => {
      await hook.current.editPendingObject(RULE_ADDRESS, ruleCard(500));
    });
    const changed = hook.current.pendingEdits;
    await act(async () => {
      if (answerCommand === null) throw new Error("the command was not sent");
      answerCommand();
      await command.catch(() => undefined);
    });
    return { command, changed };
  }

  // The live VS-API answers a command within milliseconds, so an edit of the rule made while the command is in flight cannot be produced on demand.
  it("refuses an edit a command created over an edit that changed while it ran", async () => {
    const { result } = renderHook(() => useBuilderWorld());
    await act(async () => undefined);
    await openAsThePageDoes(result, fetchMock, linkDraft());
    await holdEdit(result, RULE_ADDRESS, ruleCard(12345), ruleAnswer(12345));
    const accepted = result.current.visualDraft!;

    const { command, changed } = await rederiveWhileTheEditChanges(
      result,
      commandAnswer(accepted, {
        operation: "rederive_link",
        affected_kind: "link",
        affected_id: "leo_isl",
        created_edits: [
          { kind: "content", address: RULE_ADDRESS, base: ACCEPTED_RULE, working: INCOMPLETE_RULE },
        ],
      }),
    );

    await expect(command).rejects.toThrow(
      "the rederive_link command created an edit of leo_isl, which holds another pending edit",
    );
    expect(result.current.pendingEdits).toBe(changed);
    expect(result.current.pendingEdits.get(RULE_KEY)?.answer).toEqual(ruleAnswer(500));
    expect(result.current.visualDraft).toBe(accepted);
  });

  // The live VS-API answers a rederive within milliseconds, so an edit of the rule made while the rederive is in flight cannot be produced on demand.
  it("keeps an edit of the rule made while an accepted rederive ran", async () => {
    const { result } = renderHook(() => useBuilderWorld());
    await act(async () => undefined);
    await openAsThePageDoes(result, fetchMock, linkDraft());
    await holdEdit(result, RULE_ADDRESS, ruleCard(12345), ruleAnswer(12345));
    const rederived = rulesDraft(5, [{ ...ACCEPTED_RULE, max_range_km: 12345 }]);

    const { command, changed } = await rederiveWhileTheEditChanges(
      result,
      commandAnswer(rederived, {
        operation: "rederive_link",
        affected_kind: "link",
        affected_id: "leo_isl",
      }),
    );

    await expect(command).resolves.toMatchObject({ operation: "rederive_link" });
    expect(result.current.visualDraft).toBe(rederived);
    expect(result.current.pendingEdits.get(RULE_KEY)).toBe(changed.get(RULE_KEY));
  });
});

describe("undo history", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn((url: string) => {
      if (url.includes("/builder/catalog/list")) return Promise.resolve(sessionsResponse);
      throw new Error(`unexpected request ${url}`);
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;
  });

  const PROPOSAL = {
    ref: "user:constellations/coordinated/leo.yaml",
    document: { constellation: { id: "leo" } },
    origin: "generated",
  } as const;

  /** The opened revision: its session holds the link rule, beside one proposal. */
  function openedDraft(): BuilderVisualDraftEnvelope {
    return { ...linkDraft(4), catalog_documents: [PROPOSAL] };
  }

  /** A fully accepted revision whose session is named `name`, with the ids the
   *  allocation history holds by then. */
  function acceptedRevision(
    revision: number,
    name: string,
    reserved: ReadonlyArray<string> = [],
  ): BuilderVisualDraftEnvelope {
    return {
      ...draft(revision),
      reserved_authoring_ids: reserved,
      session_yaml: `session:\n  name: ${name}\n`,
      applied_session: { session: { name } },
    };
  }

  /** The revision a command makes from the opened one. */
  const CHANGED = acceptedRevision(5, "changed", ["space-1"]);

  /** Answer each request with the answer of the first route its path names. */
  function answerRoutes(routes: Readonly<Record<string, unknown>>): void {
    fetchMock.mockImplementation((url: string) => {
      const route = Object.keys(routes).find((path) => url.includes(path));
      if (route === undefined) throw new Error(`unexpected request ${url}`);
      return Promise.resolve(response(routes[route]));
    });
  }

  /** The backend's answer to an undo: the stored state accepted as the next
   *  revision of the draft the request carried. */
  function acceptedStoredState(init: RequestInit): BuilderVisualDraftAssemblyResult {
    const sent = JSON.parse(String(init.body)) as BuilderVisualDraftAcceptStateRequest;
    const revision = sent.draft.draft_revision + 1;
    return assembly({
      ...sent.draft,
      draft_revision: revision,
      applied_revision: revision,
      session_yaml: `session:\n  name: undone-to-${revision}\n`,
      applied_session: sent.state.session,
      catalog_documents: sent.state.catalog_documents ?? [],
    });
  }

  /** Answer every undo with the stored state accepted as the next revision. */
  function answerUndos(): void {
    fetchMock.mockImplementation((url: string, init: RequestInit) => {
      if (url.includes("/builder/draft/accept-state")) {
        return Promise.resolve(response(acceptedStoredState(init)));
      }
      throw new Error(`unexpected request ${url}`);
    });
  }

  type Hook = { current: ReturnType<typeof useBuilderWorld> };

  /** Open the opened revision as the page does. */
  async function openTheSession(): Promise<Hook> {
    const { result } = renderHook(() => useBuilderWorld());
    await act(async () => undefined);
    await openAsThePageDoes(result, fetchMock, openedDraft());
    return result;
  }

  /** Make the revision CHANGED with a command. */
  async function changeByCommand(hook: Hook): Promise<void> {
    answerRoutes({
      "/builder/draft/command": commandAnswer(CHANGED),
      "/builder/draft/compile": assembly(CHANGED),
    });
    await act(async () => {
      await hook.current.runVisualCommand({
        operation: "add_generated_space",
        phasing_mode: "walker_delta",
      });
    });
  }

  /** Undo refuses: the history holds no step. */
  async function expectNothingToUndo(hook: Hook): Promise<void> {
    await act(async () => {
      await expect(hook.current.undo()).rejects.toThrow("there is no accepted change to undo");
    });
  }

  /** Each gesture that adopts a different draft. */
  const DIFFERENT_DRAFTS: ReadonlyArray<{ gesture: string; adopt: (hook: Hook) => Promise<unknown> }> =
    [
      // The live VS-API cannot be made to accept a save and then fail the reopen of the saved session, so a committed save whose reopen failed cannot be produced on demand.
      {
        gesture: "a save under a new name whose reopen failed",
        adopt: async (hook) => {
          const renamed = {
            ...acceptedRevision(6, "renamed"),
            target_ref: "user:sessions/renamed.yaml",
          };
          act(() => {
            hook.current.adoptCommittedRetarget(assembly(renamed), { ref: renamed.target_ref });
          });
        },
      },
    ];

  it.each(DIFFERENT_DRAFTS)("starts an empty history on $gesture", async ({ adopt }) => {
    const result = await openTheSession();
    await changeByCommand(result);

    await adopt(result);

    answerUndos();
    await expectNothingToUndo(result);
    expect(requestsTo(fetchMock, "/builder/draft/accept-state")).toEqual([]);
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

describe("the reasons a refused batch of pending edits gives", () => {
  it("names each edit's issues and the session's issues no edit owns", () => {
    expect(
      refusalMessage({
        outcomes: [
          { status: "applied", issues: [] },
          { status: "invalid", issues: [workingIssue("Field required", "/endpoints/1/select")] },
        ],
        issues: [workingIssue("domains must be declared", "/routing/domains")],
      }),
    ).toBe("Field required; domains must be declared");
  });

  it("names a refusal no edit owns", () => {
    expect(
      refusalMessage({
        outcomes: [{ status: "applied", issues: [] }],
        issues: [workingIssue("domains must be declared", "/routing/domains")],
      }),
    ).toBe("domains must be declared");
  });
});
