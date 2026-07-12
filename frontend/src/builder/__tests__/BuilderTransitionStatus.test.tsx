import { render, renderHook, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../config", () => ({
  REST_URL: "http://test:8080",
  authHeaders: (extra?: Record<string, string>) => ({ ...extra }),
}));

const {
  BuilderTransitionStatus,
  transitionIsTerminal,
  useBuilderTransitionOperation,
} = await import("../BuilderTransitionStatus");

const DOCUMENT = `sha256:${"a".repeat(64)}`;
const CLOSURE = `sha256:${"b".repeat(64)}`;
const OTHER = `sha256:${"c".repeat(64)}`;
const SEMANTIC = `sha256:${"d".repeat(64)}`;

function operation(state: "verifying" | "succeeded" | "failed") {
  return {
    operation_id: "operation-proof",
    state,
    source: { kind: "catalog_session" as const, logical_id: "user:sessions/proof.yaml" },
    facts: {
      document_digest: DOCUMENT,
      closure_digest: state === "failed" ? OTHER : CLOSURE,
      resolved_semantic_digest: SEMANTIC,
      file_count: 4,
      total_bytes: 2048,
      release: "0.5.2-test",
      build: "build-proof",
    },
    created_at: "2026-07-10T00:00:00Z",
    updated_at: "2026-07-10T00:00:01Z",
    events: [
      { state: "reserved" as const, occurred_at: "2026-07-10T00:00:00Z" },
      { state, occurred_at: "2026-07-10T00:00:01Z" },
    ],
    failure:
      state === "failed"
        ? { code: "switch_failed", message: "Operator refused the runtime proof" }
        : null,
    runtime: state === "succeeded" ? { session_id: "proof", generation: 3 } : null,
  };
}

beforeEach(() => {
  globalThis.fetch = vi.fn() as unknown as typeof fetch;
});

describe("Builder deployment transition proof", () => {
  it("polls an accepted operation until a typed terminal state", async () => {
    const fetchMock = globalThis.fetch as ReturnType<typeof vi.fn>;
    fetchMock
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve(operation("verifying")) })
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve(operation("succeeded")) });

    const { result } = renderHook(() => useBuilderTransitionOperation("operation-proof", 1));
    await waitFor(() => expect(result.current.operation?.state).toBe("succeeded"));

    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      "http://test:8080/api/v1/session-transitions/operation-proof",
      "http://test:8080/api/v1/session-transitions/operation-proof",
    ]);
    expect(transitionIsTerminal(result.current.operation!.state)).toBe(true);
  });

  it("refuses proof returned for a different operation identity", async () => {
    const fetchMock = globalThis.fetch as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValue({
      ok: true,
      json: () =>
        Promise.resolve({
          ...operation("succeeded"),
          operation_id: "another-operation",
        }),
    });

    const { result } = renderHook(() =>
      useBuilderTransitionOperation("operation-proof", 10_000),
    );
    await waitFor(() =>
      expect(result.current.error).toContain(
        "does not match requested operation operation-proof",
      ),
    );
    expect(result.current.operation).toBeNull();
  });

  it("renders stage history, failure evidence, release/build, and digest mismatches", () => {
    render(
      <BuilderTransitionStatus
        operationId="operation-proof"
        operation={operation("failed")}
        pollError={null}
        reviewed={{
          document: DOCUMENT,
          dependency: CLOSURE,
          resolved_semantic: SEMANTIC,
        }}
      />,
    );

    const panel = screen.getByTestId("builder-transition-status");
    expect(screen.getByRole("status")).toBe(panel);
    expect(panel.textContent).toContain("stage failed");
    expect(panel.textContent).toContain("reserved → failed");
    expect(panel.textContent).toContain("switch_failed: Operator refused the runtime proof");
    expect(panel.textContent).toContain("runtime release 0.5.2-test · build build-proof");
    expect(panel.textContent).toContain("document digest · match");
    expect(panel.textContent).toContain("closure digest · MISMATCH");
    expect(panel.textContent).toContain(DOCUMENT);
    expect(panel.textContent).toContain(OTHER);
  });
});
