import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("../../config", () => ({
  REST_URL: "http://test:8080",
  authHeaders: (extra?: Record<string, string>) => ({ ...extra }),
}));

const { BuilderTransitionStatus } = await import("../BuilderTransitionStatus");

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

describe("Builder deployment transition proof", () => {
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
