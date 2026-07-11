// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** useBuilderWorld data-layer contract: the resolve loop keeps nothing
 *  stale on screen. A late response for a superseded edit never overwrites a
 *  newer one; a failed resolve clears the world ("the error is the state");
 *  and clear() invalidates any in-flight response so a resolve that lands
 *  after a teardown cannot repaint a world the user has left behind. */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";

vi.mock("../../config", () => ({
  REST_URL: "http://test:8080",
  authHeaders: (extra?: Record<string, string>) => ({ ...extra }),
}));

const {
  useBuilderWorld,
  requestOutlineReveal,
  useOutlineReveal,
  claimOutlineReveal,
  importSessionClosure,
  requestLibraryReveal,
  useLibraryReveal,
} = await import("../useBuilderWorld");

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason?: unknown) => void;
}
function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function request(marker: string) {
  return {
    contract_version: 1 as const,
    draft_revision: marker.charCodeAt(0),
    mode: "opaque_yaml" as const,
    target_ref: `user:sessions/${marker.toLowerCase()}.yaml`,
    session_yaml: `session:\n  name: ${marker}\n`,
  };
}

function check(marker: string) {
  const visualDraft = request(marker);
  const assembledDraft = {
    contract_version: 1 as const,
    draft_revision: visualDraft.draft_revision,
    state: { session: { session: { name: marker } }, catalog_documents: [] },
  };
  const compileResult = {
    draft: assembledDraft,
    target_ref: visualDraft.target_ref,
    canonical_session_yaml: `# ${marker}`,
    canonical_session_json: { session: { name: marker } },
    dependency_closure: {
      entries: [],
      file_count: 0,
      total_bytes: 0,
      closure_digest: `dep-${marker}`,
    },
    resolved_preview: { marker, session: { name: marker }, nodes: [] },
    digests: { document: `sha-${marker}`, dependency: `dep-${marker}` },
    issues: [],
    save_verdict: { operation: "save", allowed: true, blockers: [] },
    deploy_eligibility_after_save: {
      operation: "deploy",
      allowed: true,
      blockers: [],
    },
  };
  return {
    ok: true,
    json: () =>
      Promise.resolve({
        visual_draft: visualDraft,
        assembled_draft: assembledDraft,
        save_request: { draft: assembledDraft, target_ref: visualDraft.target_ref },
        compile_result: compileResult,
        assembly_issues: [],
      }),
  };
}

const sessionsOk = {
  ok: true,
  json: () =>
    Promise.resolve({ generation: "g1", items: [], next_page_token: null }),
};

describe("useBuilderWorld — the resolve loop keeps nothing stale", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    localStorage.clear();
    fetchMock = vi.fn();
    globalThis.fetch = fetchMock as unknown as typeof fetch;
  });

  it("a late response for a superseded resolve never overwrites the newer one", async () => {
    const slow = deferred<unknown>();
    const fast = deferred<unknown>();
    let resolveCall = 0;
    fetchMock.mockImplementation((url: string) => {
      if (url.includes("/builder/catalog/list")) return Promise.resolve(sessionsOk);
      resolveCall += 1;
      return resolveCall === 1 ? slow.promise : fast.promise;
    });

    const { result } = renderHook(() => useBuilderWorld());
    await act(async () => {}); // flush the mount sessions fetch

    // Fire A (seq N), then B (seq N+1) before A lands.
    act(() => {
      void result.current.compileDraft(request("A"));
    });
    act(() => {
      void result.current.compileDraft(request("B"));
    });

    // B lands first and is current.
    await act(async () => {
      fast.resolve(check("B"));
      await fast.promise;
    });
    expect((result.current.world as { marker?: string })?.marker).toBe("B");

    // A lands late — it is stale (seq N < N+1) and must be discarded.
    await act(async () => {
      slow.resolve(check("A"));
      await slow.promise;
    });
    expect((result.current.world as { marker?: string })?.marker).toBe("B");
    expect(result.current.settledDocumentDigest).toBe("sha-B");
  });

  it("a failed resolve clears the world — the error is the state", async () => {
    let resolveCall = 0;
    fetchMock.mockImplementation((url: string) => {
      if (url.includes("/builder/catalog/list")) return Promise.resolve(sessionsOk);
      resolveCall += 1;
      return resolveCall === 1
        ? Promise.resolve(check("A"))
        : Promise.resolve({
            ok: false,
            status: 400,
            json: () => Promise.resolve({ error: "the session does not resolve" }),
          });
    });

    const { result } = renderHook(() => useBuilderWorld());
    await act(async () => {});

    await act(async () => {
      await result.current.compileDraft(request("A"));
    });
    expect((result.current.world as { marker?: string })?.marker).toBe("A");

    await act(async () => {
      await result.current.compileDraft(request("B"));
    });
    expect(result.current.world).toBeNull();
    expect(result.current.error).toBe("the session does not resolve");
    expect(result.current.settledDocumentDigest).toBeNull();
  });

  it("clear() invalidates an in-flight response so it cannot repaint", async () => {
    const inFlight = deferred<unknown>();
    fetchMock.mockImplementation((url: string) => {
      if (url.includes("/builder/catalog/list")) return Promise.resolve(sessionsOk);
      return inFlight.promise;
    });

    const { result } = renderHook(() => useBuilderWorld());
    await act(async () => {});

    act(() => {
      void result.current.compileDraft(request("A"));
    });
    // Tear down before the response lands.
    act(() => {
      result.current.clear();
    });
    await act(async () => {
      inFlight.resolve(check("A"));
      await inFlight.promise;
    });
    expect(result.current.world).toBeNull();
    expect(result.current.settledDocumentDigest).toBeNull();
  });

  it("runs and explicitly adopts a typed backend visual command result", async () => {
    const original = {
      contract_version: 1 as const,
      draft_revision: 4,
      mode: "structured" as const,
      target_ref: "user:sessions/commanded.yaml",
      workspace: {
        session_name: "commanded",
        display_name: null,
        description: null,
        space: [],
        space_refs: [],
        ground: [],
        ground_refs: [],
        links: [],
        routing_domains: [],
        boundaries: [],
        max_pairs_per_rule: 2_000,
        max_pairs_per_tick: 10_000,
        start_time: "2026-01-01T00:00:00Z",
        step_seconds: 1,
        compression: 1,
      },
    };
    const commanded = {
      contract_version: 1 as const,
      operation: "add_generated_space" as const,
      base_draft_revision: 4,
      draft: {
        ...original,
        draft_revision: 5,
        workspace: {
          ...original.workspace,
          space: [
            {
              segment_id: "space-1",
              display_name: "Constellation 1",
              node_ref: "nodalarc:nodes/space/relay.yaml",
              node_draft: null,
              orbit: {},
              planes: 3,
              raan_spacing_deg: 60,
              slots_per_plane: 8,
              phasing_mode: "walker_delta",
              phase_offset_deg: 0,
            },
          ],
        },
      },
      affected_kind: "space" as const,
      affected_id: "space-1",
      notice: null,
    };
    fetchMock.mockImplementation((url: string) => {
      if (url.includes("/builder/catalog/list")) return Promise.resolve(sessionsOk);
      if (url.includes("/builder/draft/command")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(commanded) });
      }
      throw new Error(`unexpected request ${url}`);
    });
    const { result } = renderHook(() => useBuilderWorld());
    await act(async () => {});
    act(() => result.current.adoptRecoveredStructuredDraft(original));

    await act(async () => {
      const response = await result.current.runVisualCommand({
        draft: original,
        expected_draft_revision: 4,
        command: { operation: "add_generated_space", phasing_mode: "walker_delta" },
      });
      result.current.adoptVisualCommandResult(response);
    });

    expect(result.current.visualDraft).toEqual(commanded.draft);
    expect(result.current.currentVisualDraft()).toEqual(commanded.draft);
    const commandCall = fetchMock.mock.calls.find((call) =>
      String(call[0]).includes("/builder/draft/command"),
    );
    expect(JSON.parse(commandCall?.[1]?.body ?? "{}")).toMatchObject({
      expected_draft_revision: 4,
      command: { operation: "add_generated_space", phasing_mode: "walker_delta" },
    });
  });
});

describe("lossless YAML draft recovery", () => {
  beforeEach(() => localStorage.clear());

  it("stashes exact edited YAML synchronously and restores it with its refs", async () => {
    const sourceRef = "nodalarc:sessions/exact.yaml";
    const openedDraft = {
      contract_version: 1 as const,
      draft_revision: 0,
      mode: "opaque_yaml" as const,
      target_ref: "user:sessions/exact.yaml",
      source_ref: sourceRef,
      expected_session_revision: null,
      expected_catalog_revisions: [],
      catalog_documents: [
        {
          ref: "user:nodes/exact/custom.yaml",
          document: { node: { id: "custom" } },
        },
      ],
      workspace: null,
      session_yaml: "# exact\nsession:\n  name: exact\n",
    };
    globalThis.fetch = vi.fn((url: string) => {
      if (url.includes("/builder/catalog/list")) return Promise.resolve(sessionsOk);
      if (url.includes("/builder/draft/open")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(openedDraft) });
      }
      throw new Error(`unexpected request ${url}`);
    }) as unknown as typeof fetch;

    const { result } = renderHook(() => useBuilderWorld());
    await act(async () => {});
    await act(async () => {
      await result.current.openSession({
        ref: sourceRef,
        family: "sessions",
        namespace: "nodalarc",
        revision: "source-revision",
        size_bytes: 32,
        display_name: "Exact",
        summary: null,
      });
    });
    act(() => {
      result.current.editOpaqueYaml("# exact comment retained\nsession:\n  name: changed\n");
      result.current.stashOpaqueDraft();
      result.current.clear();
    });
    expect(result.current.visualDraft).toBeNull();

    act(() => {
      expect(result.current.restoreOpaqueAutosave()).toEqual({ ok: true });
    });
    expect(result.current.visualDraft).toMatchObject({
      mode: "opaque_yaml",
      source_ref: sourceRef,
      target_ref: "user:sessions/exact.yaml",
      session_yaml: "# exact comment retained\nsession:\n  name: changed\n",
      catalog_documents: openedDraft.catalog_documents,
    });
  });

  it("moves saved customize proposals onto their own optimistic revision fences", async () => {
    const proposalRef = "user:nodes/exact/custom.yaml";
    globalThis.fetch = vi.fn((url: string) => {
      if (url.includes("/builder/catalog/list")) return Promise.resolve(sessionsOk);
      if (url.includes("/builder/draft/open")) {
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve({
              contract_version: 1,
              draft_revision: 0,
              mode: "opaque_yaml",
              target_ref: "user:sessions/exact.yaml",
              source_ref: "nodalarc:sessions/exact.yaml",
              expected_catalog_revisions: [],
              catalog_documents: [
                { ref: proposalRef, document: { node: { id: "custom" } } },
              ],
              session_yaml: "session:\n  name: exact\n",
            }),
        });
      }
      throw new Error(`unexpected request ${url}`);
    }) as unknown as typeof fetch;
    const { result } = renderHook(() => useBuilderWorld());
    await act(async () => {});
    await act(async () => {
      await result.current.openSession({
        ref: "nodalarc:sessions/exact.yaml",
        family: "sessions",
        namespace: "nodalarc",
        revision: "source-revision",
        size_bytes: 24,
        display_name: "Exact",
        summary: null,
      });
    });
    act(() => {
      result.current.markSavedRevision("session-revision", [
        { ref: proposalRef, expected_revision: "component-revision" },
        {
          ref: "user:orbits/exact/generated.yaml",
          expected_revision: "generated-revision",
        },
      ]);
    });
    expect(result.current.visualDraft?.catalog_documents).toEqual([
      {
        ref: proposalRef,
        document: { node: { id: "custom" } },
        expected_revision: "component-revision",
      },
    ]);
    expect(result.current.visualDraft?.expected_catalog_revisions).toEqual([
      {
        ref: "user:orbits/exact/generated.yaml",
        expected_revision: "generated-revision",
      },
    ]);
  });

  it("undo restores the prior exact YAML draft", async () => {
    globalThis.fetch = vi.fn((url: string) => {
      if (url.includes("/builder/catalog/list")) return Promise.resolve(sessionsOk);
      if (url.includes("/builder/draft/open")) {
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve({
              contract_version: 1,
              draft_revision: 0,
              mode: "opaque_yaml",
              target_ref: "user:sessions/undo.yaml",
              source_ref: "user:sessions/undo.yaml",
              session_yaml: "session:\n  name: before\n",
            }),
        });
      }
      throw new Error(`unexpected request ${url}`);
    }) as unknown as typeof fetch;
    const { result } = renderHook(() => useBuilderWorld());
    await act(async () => {});
    await act(async () => {
      await result.current.openSession({
        ref: "user:sessions/undo.yaml",
        family: "sessions",
        namespace: "user",
        revision: "revision-undo",
        size_bytes: 24,
        display_name: "Undo",
        summary: null,
      });
    });
    act(() => result.current.editOpaqueYaml("session:\n  name: after\n"));
    act(() => result.current.undoOpaque());
    expect(result.current.visualDraft?.session_yaml).toBe("session:\n  name: before\n");
  });
});

describe("backend customize-chain adoption", () => {
  it("rewrites only the placed root while retaining every relationship identity", async () => {
    const originalDraft = {
      contract_version: 1 as const,
      draft_revision: 0,
      mode: "structured" as const,
      target_ref: "user:sessions/customize.yaml",
      catalog_documents: [],
      workspace: {
        session_name: "customize",
        space: [],
        space_refs: [
          {
            segment_id: "segment-stable",
            source_ref: "nodalarc:constellations/root.yaml",
            label: "Root",
          },
        ],
        ground: [],
        ground_refs: [],
        links: [
          {
            rule_id: "rule-stable",
            label: "Rule",
            enabled: true,
            a: { segment_id: "segment-stable", role: "isl", medium: "optical" },
            b: { segment_id: "segment-stable", role: "isl", medium: "optical" },
            topology_mode: "nearest_n",
            topology_n: 2,
          },
        ],
        routing_domains: [
          {
            domain_id: "domain-stable",
            label: "Domain",
            protocol: "isis",
            member_segment_ids: ["segment-stable"],
          },
        ],
        boundaries: [
          {
            boundary_id: "boundary-stable",
            over_rule_id: "rule-stable",
            adapter: "static_ip",
            from_domain_id: "domain-stable",
            to_domain_id: "domain-stable",
            export_node_loopbacks: true,
          },
        ],
        start_time: "2026-01-01T00:00:00Z",
      },
    };
    let customizeBody: Record<string, any> | null = null;
    globalThis.fetch = vi.fn((url: string, init?: { body?: string }) => {
      if (url.includes("/builder/catalog/list")) return Promise.resolve(sessionsOk);
      if (url.includes("/builder/draft/new")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(originalDraft) });
      }
      if (url.includes("/builder/draft/customize-chain")) {
        customizeBody = init?.body ? JSON.parse(init.body) : null;
        const updated = {
          ...originalDraft,
          draft_revision: 1,
          catalog_documents: [
            {
              ref: "user:constellations/customize/root.yaml",
              document: { constellation: { id: "root" } },
            },
          ],
          workspace: {
            ...originalDraft.workspace,
            space_refs: [
              {
                ...originalDraft.workspace.space_refs[0],
                source_ref: "user:constellations/customize/root.yaml",
              },
            ],
          },
        };
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve({
              applied: true,
              draft: updated,
              root_source_ref: "nodalarc:constellations/root.yaml",
              root_target_ref: "user:constellations/customize/root.yaml",
              forked_chain: [
                {
                  source_ref: "nodalarc:constellations/root.yaml",
                  target_ref: "user:constellations/customize/root.yaml",
                },
              ],
              issues: [],
            }),
        });
      }
      throw new Error(`unexpected request ${url}`);
    }) as unknown as typeof fetch;

    const { result } = renderHook(() => useBuilderWorld());
    await act(async () => {});
    await act(async () => {
      await result.current.createDraft({ session_name: "customize" });
      await result.current.customizeChain({
        segment_id: "segment-stable",
        leaf_ref: "nodalarc:nodes/space/leaf.yaml",
      });
    });

    expect(customizeBody).toMatchObject({
      segment_id: "segment-stable",
      leaf_ref: "nodalarc:nodes/space/leaf.yaml",
      draft: { target_ref: "user:sessions/customize.yaml" },
    });
    expect(result.current.visualDraft?.workspace).toMatchObject({
      space_refs: [
        {
          segment_id: "segment-stable",
          source_ref: "user:constellations/customize/root.yaml",
        },
      ],
      links: [{ rule_id: "rule-stable" }],
      routing_domains: [{ domain_id: "domain-stable" }],
      boundaries: [
        {
          boundary_id: "boundary-stable",
          over_rule_id: "rule-stable",
          from_domain_id: "domain-stable",
          to_domain_id: "domain-stable",
        },
      ],
    });
  });
});

describe("exact session closure transfer", () => {
  it("passes the exported YAML bytes and refs to backend import unchanged", async () => {
    let posted: Record<string, unknown> | null = null;
    globalThis.fetch = vi.fn((_url: string, init?: { body?: string }) => {
      posted = init?.body ? JSON.parse(init.body) : null;
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            outcome: "unchanged",
            generation: "g1",
            document_digest: "root-digest",
            closure_digest: "closure-digest",
            proposed_writes: [],
            identical_refs: ["user:sessions/test.yaml"],
            collisions: [],
          }),
      });
    }) as unknown as typeof fetch;

    await importSessionClosure(
      {
        contract_version: 1,
        session_ref: "user:sessions/test.yaml",
        session_revision: "revision-session",
        generation: "g1",
        root: {
          ref: "user:sessions/test.yaml",
          family: "sessions",
          preserved_path: "user/sessions/test.yaml",
          exact_yaml: "session:\n  name: exact\n",
          document_digest: "root-digest",
          revision: "revision-session",
        },
        entries: [
          {
            ref: "user:terminals/exact.yaml",
            family: "terminals",
            preserved_path: "user/terminals/exact.yaml",
            exact_yaml: "terminal:\n  id: exact\n",
            document_digest: "terminal-digest",
            revision: "revision-terminal",
          },
        ],
        document_digest: "root-digest",
        closure_digest: "closure-digest",
        file_count: 2,
        total_bytes: 64,
      },
      false,
    );

    expect(posted).toEqual({
      contract_version: 1,
      root_ref: "user:sessions/test.yaml",
      root_yaml: "session:\n  name: exact\n",
      document_digest: "root-digest",
      closure_digest: "closure-digest",
      entries: [
        {
          ref: "user:terminals/exact.yaml",
          exact_yaml: "terminal:\n  id: exact\n",
          document_digest: "terminal-digest",
        },
      ],
      commit: false,
    });
  });

  it("rejects portable closure carriers without contract version 1", async () => {
    await expect(importSessionClosure({
      session_ref: "user:sessions/test.yaml",
      document_digest: "root-digest",
      closure_digest: "closure-digest",
      root: { exact_yaml: "session: {}\n" },
      entries: [],
    }, false)).rejects.toThrow("missing its typed closure fields");
  });
});

describe("outline reveal is a channel separate from the Library reveal", () => {
  it("consumes an outline reveal once — claim returns it, then null (no replay)", () => {
    const { result } = renderHook(() => useOutlineReveal());
    act(() => requestOutlineReveal("space-777"));
    const reveal = result.current;
    expect(reveal?.segmentId).toBe("space-777");
    expect(claimOutlineReveal("outline", reveal)?.segmentId).toBe("space-777");
    // A second claim (e.g. a remount replaying the same reveal) yields nothing.
    expect(claimOutlineReveal("outline", reveal)).toBeNull();
  });

  it("a Library reveal never touches the outline channel", () => {
    const outline = renderHook(() => useOutlineReveal());
    const before = outline.result.current;
    act(() =>
      requestLibraryReveal({
        ref: "user:sites/x.yaml",
        family: "sites",
        id: "x",
      } as unknown as Parameters<typeof requestLibraryReveal>[0]),
    );
    // The outline store is unchanged by reference — the reveals do not cross.
    expect(outline.result.current).toBe(before);
  });

  it("an outline reveal never touches the Library channel", () => {
    const library = renderHook(() => useLibraryReveal());
    const before = library.result.current;
    act(() => requestOutlineReveal("ground-42"));
    expect(library.result.current).toBe(before);
  });
});
