/** Session coordinator contract: one backend-issued revision stream. */

import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type {
  BuilderVisualDraftAssemblyResult,
  BuilderVisualDraftEnvelope,
} from "../generated/builderApi";
import { newWorkspace } from "./fixtures/workspaceFixtures";

vi.mock("../../config", () => ({
  REST_URL: "http://test:8080",
  authHeaders: (extra?: Record<string, string>) => ({ ...extra }),
}));

const {
  claimOutlineReveal,
  requestLibraryReveal,
  requestOutlineReveal,
  useBuilderWorld,
  useLibraryReveal,
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
  const workspace = newWorkspace("coordinated");
  workspace.projection_revision = revision;
  return {
    contract_version: 2,
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
    contract_version: 1 as const,
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
      canonical_session_yaml: `session:\n  name: coordinated\n# ${marker}\n`,
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
    act(() => result.current.adoptRecoveredStructuredDraft(original));
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

  it("blocks commands and customization while YAML is unapplied", async () => {
    const original = draft();
    const { result } = renderHook(() => useBuilderWorld());
    await act(async () => undefined);
    act(() => result.current.adoptRecoveredStructuredDraft(original));
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

describe("outline reveal remains separate from Library reveal", () => {
  it("consumes each outline reveal once", () => {
    const { result } = renderHook(() => useOutlineReveal());
    act(() => requestOutlineReveal("space-777"));
    expect(claimOutlineReveal("outline", result.current)?.segmentId).toBe("space-777");
    expect(claimOutlineReveal("outline", result.current)).toBeNull();
  });

  it("does not cross the Library reveal channel", () => {
    const outline = renderHook(() => useOutlineReveal());
    const beforeOutline = outline.result.current;
    act(() =>
      requestLibraryReveal({
        ref: "user:sites/x.yaml",
        namespace: "user",
        family: "sites",
        revision: "revision",
        size_bytes: 1,
        display_name: "x",
        summary: null,
      }),
    );
    expect(outline.result.current).toBe(beforeOutline);

    const library = renderHook(() => useLibraryReveal());
    const beforeLibrary = library.result.current;
    act(() => requestOutlineReveal("ground-42"));
    expect(library.result.current).toBe(beforeLibrary);
  });
});
