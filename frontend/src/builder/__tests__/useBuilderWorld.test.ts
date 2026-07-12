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
vi.mock("../../ui/downloadBlob", () => ({ downloadBlob: vi.fn() }));

const {
  claimOutlineReveal,
  exportSessionYaml,
  importSessionYamlFiles,
  requestLibraryReveal,
  requestOutlineReveal,
  useBuilderWorld,
  useLibraryReveal,
  useOutlineReveal,
} = await import("../useBuilderWorld");
const { downloadBlob } = await import("../../ui/downloadBlob");

interface Deferred<T> {
  promise: Promise<T>;
  resolve(value: T): void;
}

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

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

  it("opens a stored session as a graphical projection and compiles the same revision", async () => {
    const opened = draft();
    fetchMock.mockImplementation((url: string) => {
      if (url.includes("/builder/catalog/list")) return Promise.resolve(sessionsResponse);
      if (url.includes("/builder/draft/open")) return Promise.resolve(response(opened));
      if (url.includes("/builder/draft/compile")) {
        return Promise.resolve(response(assembly(opened)));
      }
      throw new Error(`unexpected request ${url}`);
    });
    const { result } = renderHook(() => useBuilderWorld());
    await act(async () => undefined);

    await act(async () => {
      const openedResult = await result.current.openSession({
        ref: "user:sessions/coordinated.yaml",
        namespace: "user",
        family: "sessions",
        revision: "session-revision",
        size_bytes: 32,
        display_name: "Coordinated",
        summary: null,
      });
      expect(openedResult.ok).toBe(true);
    });

    expect(result.current.visualDraft?.authoring_workspace?.session_name).toBe("coordinated");
    expect(result.current.visualDraft?.draft_revision).toBe(4);
    expect(result.current.yamlBuffer.text).toBe(opened.session_yaml);
    expect((result.current.world as { marker?: string })?.marker).toBe("revision-4");
  });

  it("compiles a newly created graphical draft before exposing assembly facts", async () => {
    const created = draft(0);
    fetchMock.mockImplementation((url: string) => {
      if (url.includes("/builder/catalog/list")) return Promise.resolve(sessionsResponse);
      if (url.includes("/builder/draft/new")) return Promise.resolve(response(created));
      if (url.includes("/builder/draft/compile")) {
        return Promise.resolve(response(assembly(created, "created")));
      }
      throw new Error(`unexpected request ${url}`);
    });
    const { result } = renderHook(() => useBuilderWorld());
    await act(async () => undefined);
    await act(async () => {
      await result.current.createDraft({ session_name: "coordinated" });
    });
    expect(result.current.assemblyResult).not.toBeNull();
    expect((result.current.world as { marker?: string })?.marker).toBe("created");
  });

  it("keeps exact opened YAML and requires canonicalization before graphical edits", async () => {
    const opened = { ...draft(), session_yaml: "# hand formatted\nsession: {name: coordinated}\n" };
    fetchMock.mockImplementation((url: string) => {
      if (url.includes("/builder/catalog/list")) return Promise.resolve(sessionsResponse);
      if (url.includes("/builder/draft/open")) return Promise.resolve(response(opened));
      if (url.includes("/builder/draft/compile")) {
        return Promise.resolve(response(assembly(opened, "canonical")));
      }
      throw new Error(`unexpected request ${url}`);
    });
    const { result } = renderHook(() => useBuilderWorld());
    await act(async () => undefined);
    await act(async () => {
      await result.current.openSession({
        ref: "user:sessions/coordinated.yaml",
        namespace: "user",
        family: "sessions",
        revision: "session-revision",
        size_bytes: 32,
        display_name: "Coordinated",
        summary: null,
      });
    });
    expect(result.current.yamlBuffer.text).toBe(opened.session_yaml);
    expect(result.current.yamlBuffer.canonicalizationRequired).toBe(true);
  });

  it("discards an open response superseded by a newer session epoch", async () => {
    const slow = deferred<ReturnType<typeof response>>();
    const second: BuilderVisualDraftEnvelope = {
      ...draft(8),
      target_ref: "user:sessions/second.yaml",
      source_ref: "user:sessions/second.yaml",
    };
    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (url.includes("/builder/catalog/list")) return Promise.resolve(sessionsResponse);
      if (url.includes("/builder/draft/open")) {
        const body = JSON.parse(String(init?.body)) as { source_ref: string };
        return body.source_ref.endsWith("/first.yaml")
          ? slow.promise
          : Promise.resolve(response(second));
      }
      if (url.includes("/builder/draft/compile")) {
        return Promise.resolve(response(assembly(second, "second")));
      }
      throw new Error(`unexpected request ${url}`);
    });
    const { result } = renderHook(() => useBuilderWorld());
    await act(async () => undefined);
    let first!: ReturnType<typeof result.current.openSession>;
    let latest!: ReturnType<typeof result.current.openSession>;
    act(() => {
      first = result.current.openSession({
        ref: "user:sessions/first.yaml",
        namespace: "user",
        family: "sessions",
        revision: "first",
        size_bytes: 10,
        display_name: "First",
        summary: null,
      });
      latest = result.current.openSession({
        ref: "user:sessions/second.yaml",
        namespace: "user",
        family: "sessions",
        revision: "second",
        size_bytes: 10,
        display_name: "Second",
        summary: null,
      });
    });
    await act(async () => {
      expect(await latest).toMatchObject({ ok: true });
    });
    expect(result.current.visualDraft?.target_ref).toBe("user:sessions/second.yaml");
    expect((result.current.world as { marker?: string })?.marker).toBe("second");
    await act(async () => {
      slow.resolve(response(draft(2)));
      expect(await first).toMatchObject({ ok: false });
    });
    expect(result.current.visualDraft?.target_ref).toBe("user:sessions/second.yaml");
  });

  it("lets a newer keystroke supersede an in-flight YAML application", async () => {
    const original = draft();
    const pending = deferred<ReturnType<typeof response>>();
    fetchMock.mockImplementation((url: string) => {
      if (url.includes("/builder/catalog/list")) return Promise.resolve(sessionsResponse);
      if (url.includes("/builder/draft/apply-yaml")) return pending.promise;
      throw new Error(`unexpected request ${url}`);
    });
    const { result } = renderHook(() => useBuilderWorld());
    await act(async () => undefined);
    act(() => result.current.adoptRecoveredStructuredDraft(original));
    act(() => {
      result.current.editYamlBuffer("session:\n  name: first\n");
    });
    let application!: Promise<unknown>;
    act(() => {
      application = result.current.applyYamlBuffer(1);
    });
    act(() => {
      expect(result.current.editYamlBuffer("session:\n  name: second\n")).toBe(true);
    });
    await act(async () => {
      pending.resolve(
        response({
          draft: { ...original, session_yaml: "session:\n  name: first\n" },
          buffer_generation: 1,
          yaml_text: "session:\n  name: first\n",
          applied: false,
          canonicalization_required: false,
          issues: [
            {
              code: "builder.yaml.invalid",
              stage: "structural",
              severity: "error",
              message: "invalid",
              blocks: ["save", "deploy"],
            },
          ],
        }),
      );
      await expect(application).rejects.toThrow("session changed");
    });
    expect(result.current.yamlBuffer.text).toContain("second");
    expect(result.current.yamlBuffer.generation).toBe(2);
    expect(result.current.visualDraft?.draft_revision).toBe(4);
  });

  it("preserves the last valid world when YAML is refused", async () => {
    const original = draft();
    const compiled = assembly(original, "last-valid");
    fetchMock.mockImplementation((url: string) => {
      if (url.includes("/builder/catalog/list")) return Promise.resolve(sessionsResponse);
      if (url.includes("/builder/draft/compile")) return Promise.resolve(response(compiled));
      if (url.includes("/builder/draft/apply-yaml")) {
        return Promise.resolve(
          response({
            draft: {
              ...original,
              projection_status: "pending_authoring",
              session_yaml: ": [unfinished",
              authoring_workspace: {
                ...original.authoring_workspace,
                projection_revision: null,
              },
            },
            buffer_generation: 1,
            yaml_text: ": [unfinished",
            applied: false,
            canonicalization_required: false,
            issues: [
              {
                code: "builder.yaml.syntax",
                stage: "structural",
                severity: "error",
                message: "expected a closing bracket",
                source_line: 1,
                source_column: 3,
                blocks: ["save", "deploy"],
              },
            ],
          }),
        );
      }
      throw new Error(`unexpected request ${url}`);
    });
    const { result } = renderHook(() => useBuilderWorld());
    await act(async () => undefined);
    act(() => result.current.adoptRecoveredStructuredDraft(original));
    await act(async () => {
      await result.current.compileCurrent();
    });
    act(() => result.current.editYamlBuffer(": [unfinished"));
    await act(async () => {
      await result.current.applyYamlBuffer(1);
    });

    expect((result.current.world as { marker?: string })?.marker).toBe("last-valid");
    expect(result.current.visualDraft?.applied_revision).toBe(4);
    expect(result.current.yamlBuffer).toMatchObject({
      text: ": [unfinished",
      appliedText: original.session_yaml,
      generation: 1,
      dirty: true,
      applied: false,
    });
    expect(result.current.yamlBuffer.issues[0]).toMatchObject({
      source_line: 1,
      source_column: 3,
    });
  });

  it("clears resolved facts when a current compile fails", async () => {
    const original = draft();
    let compileCount = 0;
    fetchMock.mockImplementation((url: string) => {
      if (url.includes("/builder/catalog/list")) return Promise.resolve(sessionsResponse);
      if (url.includes("/builder/draft/compile")) {
        compileCount += 1;
        return compileCount === 1
          ? Promise.resolve(response(assembly(original, "valid")))
          : Promise.resolve({
              ok: false,
              status: 422,
              json: () => Promise.resolve({ detail: "the session does not resolve" }),
            });
      }
      throw new Error(`unexpected request ${url}`);
    });
    const { result } = renderHook(() => useBuilderWorld());
    await act(async () => undefined);
    act(() => result.current.adoptRecoveredStructuredDraft(original));
    await act(async () => {
      await result.current.compileCurrent();
    });
    expect(result.current.world).not.toBeNull();
    await act(async () => {
      await expect(result.current.compileCurrent()).rejects.toThrow(
        "the session does not resolve",
      );
    });
    expect(result.current.world).toBeNull();
    expect(result.current.settledDocumentDigest).toBeNull();
    expect(result.current.error).toBe("the session does not resolve");
  });

  it("clear invalidates an in-flight compile before it can repaint", async () => {
    const pending = deferred<ReturnType<typeof response>>();
    fetchMock.mockImplementation((url: string) => {
      if (url.includes("/builder/catalog/list")) return Promise.resolve(sessionsResponse);
      if (url.includes("/builder/draft/compile")) return pending.promise;
      throw new Error(`unexpected request ${url}`);
    });
    const original = draft();
    const { result } = renderHook(() => useBuilderWorld());
    await act(async () => undefined);
    act(() => result.current.adoptRecoveredStructuredDraft(original));
    let compiling!: Promise<unknown>;
    act(() => {
      compiling = result.current.compileCurrent();
      result.current.clear();
    });
    await act(async () => {
      pending.resolve(response(assembly(original, "late")));
      await expect(compiling).rejects.toThrow("session changed");
    });
    expect(result.current.visualDraft).toBeNull();
    expect(result.current.world).toBeNull();
  });

  it("applies a graphical workspace without minting a client revision", async () => {
    const original = draft();
    const next: BuilderVisualDraftEnvelope = {
      ...draft(5),
      authoring_workspace: {
        ...draft(5).authoring_workspace!,
        description: "server adopted",
      },
    };
    const applied = assembly(next, "workspace");
    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (url.includes("/builder/catalog/list")) return Promise.resolve(sessionsResponse);
      if (url.includes("/builder/draft/apply-workspace")) {
        const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
        expect(body.expected_draft_revision).toBe(4);
        expect(body.draft).toMatchObject({ draft_revision: 4 });
        return Promise.resolve(response(applied));
      }
      throw new Error(`unexpected request ${url}`);
    });
    const { result } = renderHook(() => useBuilderWorld());
    await act(async () => undefined);
    act(() => result.current.adoptRecoveredStructuredDraft(original));
    await act(async () => {
      await result.current.applyWorkspace({
        ...original.authoring_workspace!,
        description: "server adopted",
      });
    });
    expect(result.current.visualDraft?.draft_revision).toBe(5);
    expect(result.current.yamlBuffer.text).toContain("# workspace");
  });

  it("routes scalar, choice, sequence, and map controls through one fenced mutation", async () => {
    const original = draft();
    const next: BuilderVisualDraftEnvelope = {
      ...draft(5),
      authoring_workspace: {
        ...draft(5).authoring_workspace!,
        description: "control-mutated",
      },
    };
    const commands = [
      { operation: "set_scalar" as const, control_id: "ctl_scalar", value: "changed" },
      { operation: "select_choice" as const, control_id: "ctl_choice", branch_id: "branch" },
      { operation: "insert_item" as const, control_id: "ctl_sequence", index: 2 },
      {
        operation: "insert_map_entry" as const,
        control_id: "ctl_map",
        key: "entry",
      },
    ];
    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (url.includes("/builder/catalog/list")) return Promise.resolve(sessionsResponse);
      if (url.includes("/builder/draft/control-mutate")) {
        expect(JSON.parse(String(init?.body))).toEqual({
          draft: original,
          expected_draft_revision: 4,
          commands,
        });
        return Promise.resolve(response(assembly(next, "control-mutated")));
      }
      throw new Error(`unexpected request ${url}`);
    });
    const { result } = renderHook(() => useBuilderWorld());
    await act(async () => undefined);
    act(() => result.current.adoptRecoveredStructuredDraft(original));
    await act(async () => {
      await result.current.mutateControls(commands);
    });
    expect(result.current.visualDraft?.draft_revision).toBe(5);
    expect(result.current.visualDraft?.authoring_workspace?.description).toBe(
      "control-mutated",
    );
    expect(result.current.yamlBuffer.text).toContain("# control-mutated");
  });

  it("adopts a typed command and its compile facts atomically", async () => {
    const original = draft();
    const commanded: BuilderVisualDraftEnvelope = {
      ...draft(5),
      authoring_workspace: {
        ...draft(5).authoring_workspace!,
        description: "commanded",
      },
    };
    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (url.includes("/builder/catalog/list")) return Promise.resolve(sessionsResponse);
      if (url.includes("/builder/draft/command")) {
        const body = JSON.parse(String(init?.body));
        expect(body).toMatchObject({
          expected_draft_revision: 4,
          command: { operation: "add_generated_space" },
        });
        return Promise.resolve(
          response({
            contract_version: 1,
            operation: "add_generated_space",
            base_draft_revision: 4,
            draft: commanded,
            affected_kind: "space",
            affected_id: "space-1",
            notice: null,
          }),
        );
      }
      if (url.includes("/builder/draft/compile")) {
        return Promise.resolve(response(assembly(commanded, "commanded")));
      }
      throw new Error(`unexpected request ${url}`);
    });
    const { result } = renderHook(() => useBuilderWorld());
    await act(async () => undefined);
    act(() => result.current.adoptRecoveredStructuredDraft(original));
    await act(async () => {
      await result.current.runVisualCommand({
        operation: "add_generated_space",
        phasing_mode: "walker_delta",
      });
    });
    expect(result.current.visualDraft?.draft_revision).toBe(5);
    expect(result.current.visualDraft?.authoring_workspace?.description).toBe("commanded");
    expect((result.current.world as { marker?: string })?.marker).toBe("commanded");
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

  it("does not let a delayed save reopen overwrite a newer epoch", async () => {
    const original = draft();
    const delayedOpen = deferred<ReturnType<typeof response>>();
    fetchMock.mockImplementation((url: string) => {
      if (url.includes("/builder/catalog/list")) return Promise.resolve(sessionsResponse);
      if (url.includes("/builder/session/save")) {
        return Promise.resolve(
          response({
            session: {
              ref: original.target_ref,
              family: "sessions",
              canonical_yaml: original.session_yaml,
              canonical_json: original.applied_session,
              content_digest: "document-saved",
              revision: "saved-revision",
            },
            digests: { document: "document-saved", dependency: "dependency-saved" },
            dependency_closure: {
              entries: [],
              file_count: 0,
              total_bytes: 0,
              closure_digest: "dependency-saved",
            },
            deploy_verdict: {
              allowed: true,
              session_ref: original.target_ref,
              session_revision: "saved-revision",
              digests: { document: "document-saved", dependency: "dependency-saved" },
              blockers: [],
            },
            issues: [],
          }),
        );
      }
      if (url.includes("/builder/draft/open")) return delayedOpen.promise;
      throw new Error(`unexpected request ${url}`);
    });
    const { result } = renderHook(() => useBuilderWorld());
    await act(async () => undefined);
    act(() => result.current.adoptRecoveredStructuredDraft(original));
    const compiled = assembly(original, "save");
    act(() => {
      // Capture the compile facts that bind the save request.
      result.current.adoptRecoveredStructuredDraft(original);
    });
    const capture = result.current.captureCoordinator();
    let saving!: Promise<unknown>;
    act(() => {
      saving = result.current.saveSession(compiled.save_request, capture);
    });
    await act(async () => undefined);
    act(() => result.current.clear());
    await act(async () => {
      delayedOpen.resolve(response(original));
      await expect(saving).resolves.toMatchObject({
        reopenedDraft: null,
        postCommitError: "the session changed while this operation was running",
      });
    });
    expect(result.current.visualDraft).toBeNull();
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

  it("adopts a customized root while preserving relationship identities", async () => {
    const workspace = newWorkspace("coordinated");
    workspace.projection_revision = 4;
    workspace.space_refs = [
      {
        segment_id: "segment-stable",
        source_ref: "nodalarc:constellations/root.yaml",
        label: "Root",
      },
    ];
    workspace.links = [
      {
        rule_id: "rule-stable",
        label: "Rule",
        enabled: true,
        a: {
          segment_id: "segment-stable",
          tag: null,
          role: "isl",
          medium: "optical",
          min_elevation_deg: null,
        },
        b: {
          segment_id: "segment-stable",
          tag: null,
          role: "isl",
          medium: "optical",
          min_elevation_deg: null,
        },
        topology_mode: "nearest_n",
        topology_n: 2,
        max_range_km: null,
      },
    ];
    const original: BuilderVisualDraftEnvelope = {
      ...draft(),
      authoring_workspace: workspace,
      applied_workspace: workspace,
    };
    const customizedWorkspace = structuredClone(workspace);
    customizedWorkspace.projection_revision = 5;
    customizedWorkspace.space_refs[0]!.source_ref =
      "user:constellations/coordinated/root.yaml";
    const customized: BuilderVisualDraftEnvelope = {
      ...original,
      draft_revision: 5,
      catalog_documents: [
        {
          ref: "user:constellations/coordinated/root.yaml",
          document: { constellation: { id: "root" } },
          origin: "customized",
        },
      ],
      authoring_workspace: customizedWorkspace,
      applied_workspace: customizedWorkspace,
      applied_revision: 5,
    };
    fetchMock.mockImplementation((url: string) => {
      if (url.includes("/builder/catalog/list")) return Promise.resolve(sessionsResponse);
      if (url.includes("/builder/draft/customize-chain")) {
        return Promise.resolve(
          response({
            applied: true,
            draft: customized,
            root_source_ref: "nodalarc:constellations/root.yaml",
            root_target_ref: "user:constellations/coordinated/root.yaml",
            forked_chain: [],
            issues: [],
          }),
        );
      }
      if (url.includes("/builder/draft/compile")) {
        return Promise.resolve(response(assembly(customized, "customized")));
      }
      throw new Error(`unexpected request ${url}`);
    });
    const { result } = renderHook(() => useBuilderWorld());
    await act(async () => undefined);
    act(() => result.current.adoptRecoveredStructuredDraft(original));
    await act(async () => {
      await result.current.customizeChain({
        segment_id: "segment-stable",
        leaf_ref: "nodalarc:nodes/space/leaf.yaml",
      });
    });
    expect(result.current.visualDraft?.authoring_workspace).toMatchObject({
      space_refs: [
        {
          segment_id: "segment-stable",
          source_ref: "user:constellations/coordinated/root.yaml",
        },
      ],
      links: [{ rule_id: "rule-stable" }],
    });
    expect(result.current.visualDraft?.catalog_documents).toHaveLength(1);
  });
});

describe("ordinary YAML session transfer", () => {
  beforeEach(() => {
    vi.mocked(downloadBlob).mockReset();
    delete (globalThis as typeof globalThis & { showDirectoryPicker?: unknown }).showDirectoryPicker;
  });

  it("writes the backend-owned directory tree when the browser supports it", async () => {
    const writes: Array<[string, string]> = [];
    const directories = new Map<string, unknown>();
    const directory = (prefix: string): unknown => ({
      getDirectoryHandle: async (name: string) => {
        const path = `${prefix}${name}/`;
        if (!directories.has(path)) directories.set(path, directory(path));
        return directories.get(path);
      },
      getFileHandle: async (name: string) => ({
        createWritable: async () => ({
          write: async (content: string) => {
            writes.push([`${prefix}${name}`, content]);
          },
          close: async () => undefined,
        }),
      }),
    });
    const selected = {
      getDirectoryHandle: async (name: string, options: { create: boolean }) => {
        if (!options.create) throw new DOMException("missing", "NotFoundError");
        return directory(`${name}/`);
      },
    };
    (globalThis as typeof globalThis & { showDirectoryPicker?: unknown }).showDirectoryPicker =
      vi.fn(async () => selected);
    globalThis.fetch = vi.fn(() =>
      Promise.resolve(
        response({
          session_ref: "user:sessions/exported.yaml",
          files: [
            {
              logical_path: "catalog/user/sessions/exported.yaml",
              yaml_text: "session:\n  name: exported\n",
            },
            {
              logical_path: "catalog/user/terminals/rf.yaml",
              yaml_text: "terminal:\n  id: rf\n",
            },
          ],
        }),
      ),
    ) as unknown as typeof fetch;

    await exportSessionYaml({
      ref: "user:sessions/exported.yaml",
      namespace: "user",
      family: "sessions",
      revision: "revision",
      size_bytes: 10,
      display_name: "Exported",
      summary: null,
    });

    expect(writes).toEqual([
      [
        "exported-nodalarc-session/catalog/user/sessions/exported.yaml",
        "session:\n  name: exported\n",
      ],
      [
        "exported-nodalarc-session/catalog/user/terminals/rf.yaml",
        "terminal:\n  id: rf\n",
      ],
    ]);
    expect(downloadBlob).not.toHaveBeenCalled();
  });

  it("refuses to overwrite an existing export directory", async () => {
    const selected = {
      getDirectoryHandle: vi.fn(async () => ({})),
    };
    (globalThis as typeof globalThis & { showDirectoryPicker?: unknown }).showDirectoryPicker =
      vi.fn(async () => selected);
    globalThis.fetch = vi.fn(() =>
      Promise.resolve(
        response({
          session_ref: "user:sessions/exported.yaml",
          files: [{
            logical_path: "catalog/user/sessions/exported.yaml",
            yaml_text: "session: {}\n",
          }],
        }),
      ),
    ) as unknown as typeof fetch;
    await expect(
      exportSessionYaml({
        ref: "user:sessions/exported.yaml",
        namespace: "user",
        family: "sessions",
        revision: "revision",
        size_bytes: 10,
        display_name: "Exported",
        summary: null,
      }),
    ).rejects.toThrow("export directory already exists");
    expect(downloadBlob).not.toHaveBeenCalled();
  });

  it("falls back to one YAML download per file without inventing a container", async () => {
    globalThis.fetch = vi.fn(() =>
      Promise.resolve(
        response({
          session_ref: "user:sessions/exported.yaml",
          files: [
            {
              logical_path: "catalog/user/sessions/exported.yaml",
              yaml_text: "session: {}\n",
            },
            {
              logical_path: "catalog/user/nodes/a__b/node.yaml",
              yaml_text: "node: {}\n",
            },
          ],
        }),
      ),
    ) as unknown as typeof fetch;
    await exportSessionYaml({
      ref: "user:sessions/exported.yaml",
      namespace: "user",
      family: "sessions",
      revision: "revision",
      size_bytes: 10,
      display_name: "Exported",
      summary: null,
    });
    expect(downloadBlob).toHaveBeenNthCalledWith(
      1,
      "session: {}\n",
      "catalog%2Fuser%2Fsessions%2Fexported.yaml",
    );
    expect(downloadBlob).toHaveBeenNthCalledWith(
      2,
      "node: {}\n",
      "catalog%2Fuser%2Fnodes%2Fa__b%2Fnode.yaml",
    );
  });

  it("imports only YAML texts and commit intent", async () => {
    let posted: Record<string, unknown> | null = null;
    globalThis.fetch = vi.fn((_url: string, init?: RequestInit) => {
      posted = JSON.parse(String(init?.body));
      return Promise.resolve(
        response({
          outcome: "unchanged",
          generation: "generation",
          session_ref: "user:sessions/imported.yaml",
          proposed_writes: [],
          identical_refs: [],
          collisions: [],
        }),
      );
    }) as unknown as typeof fetch;
    await importSessionYamlFiles(
      [
        { yaml_text: "session:\n  name: imported\n" },
        { yaml_text: "terminal:\n  id: rf\n" },
      ],
      null,
    );
    expect(posted).toEqual({
      yaml_files: [
        { yaml_text: "session:\n  name: imported\n" },
        { yaml_text: "terminal:\n  id: rf\n" },
      ],
      commit: false,
    });
  });

  it("commits only with the exact reviewed proposal token", async () => {
    let posted: Record<string, unknown> | null = null;
    globalThis.fetch = vi.fn((url: string, init?: RequestInit) => {
      if (!url.includes("/builder/session/yaml/import")) {
        return Promise.resolve(response({ generation: "next-generation", entries: [] }));
      }
      posted = JSON.parse(String(init?.body));
      return Promise.resolve(
        response({
          outcome: "committed",
          generation: "next-generation",
          session_ref: "user:sessions/imported.yaml",
          proposed_writes: [
            {
              ref: "user:sessions/imported.yaml",
              family: "sessions",
              logical_path: "catalog/user/sessions/imported.yaml",
              canonical_yaml: "session:\n  name: imported\n",
              canonicalization_changed: false,
            },
          ],
          identical_refs: [],
          collisions: [],
        }),
      );
    }) as unknown as typeof fetch;
    await importSessionYamlFiles(
      [{ yaml_text: "session:\n  name: imported\n" }],
      "reviewed-proposal-token",
    );
    expect(posted).toEqual({
      yaml_files: [{ yaml_text: "session:\n  name: imported\n" }],
      commit: true,
      proposal_token: "reviewed-proposal-token",
    });
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
