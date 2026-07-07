// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** BuilderView surface contract (P2): the on-screen world is the resolver's
 *  expansion of the current draft, never a stale frame or a false "resolves".
 *  These pins RENDER the view (Scene mocked to a null render, fetch stubbed,
 *  localStorage isolated) and assert the emitted DOM — the data-layer hook
 *  test alone cannot see the UI state P2 fixes.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup, within } from "@testing-library/react";

vi.mock("../../globe/r3f/Scene", () => ({ Scene: () => null }));
vi.mock("../../config", () => ({
  REST_URL: "http://test:8080",
  authHeaders: (extra?: Record<string, string>) => ({ ...extra }),
}));

const { BuilderView } = await import("../BuilderView");
const { catalogEarthFrame } = await import("../../sim/__tests__/bodyModelFixture");

const PROPS = {
  active: true,
  colorMode: "regime",
  globeMode: "blue-marble",
  referenceFrame: "earth-fixed",
  showSatPaths: false,
  showIslLinks: false,
  showGroundLinks: false,
  showGroundTracks: false,
  showTrails: false,
  actionsRef: { current: null },
} as const;

function jsonResponse(body: unknown, ok = true, status = 200) {
  return { ok, status, json: () => Promise.resolve(body) };
}

const GROUND_NODE = "nodalarc:nodes/ground/gateway.yaml";

/** A fetch stub covering every endpoint BuilderView touches on this branch.
 *  `sessions` and `resolveWorld` are overridable per test; catalog fetches
 *  return a single ground node so the add-ground gesture has a default model. */
function stubFetch(options?: {
  sessions?: unknown[];
  resolveWorld?: () => ReturnType<typeof jsonResponse>;
}) {
  const sessions = options?.sessions ?? [];
  const fetchMock = vi.fn((url: string) => {
    if (url.includes("/api/v1/sessions")) return Promise.resolve(jsonResponse(sessions));
    if (url.includes("/builder/catalog/object")) {
      return Promise.resolve(
        jsonResponse({ ref: GROUND_NODE, family_wrapper: "node", document: { node: { terminals: [] } } }),
      );
    }
    if (url.includes("/builder/catalog")) {
      const family = new URL(url).searchParams.get("family");
      return Promise.resolve(
        jsonResponse(
          family === "nodes"
            ? [{ ref: GROUND_NODE, family: "nodes", id: "gateway", display_name: "Gateway" }]
            : [],
        ),
      );
    }
    if (url.includes("/builder/resolve-world")) {
      return Promise.resolve((options?.resolveWorld ?? (() => jsonResponse({})))());
    }
    return Promise.resolve(jsonResponse({}));
  });
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  return fetchMock;
}

const resolveWorldCalls = (fetchMock: ReturnType<typeof vi.fn>) =>
  fetchMock.mock.calls.filter((c: unknown[]) => String(c[0]).includes("/builder/resolve-world"));
const sessionsCalls = (fetchMock: ReturnType<typeof vi.fn>) =>
  fetchMock.mock.calls.filter((c: unknown[]) => String(c[0]).includes("/api/v1/sessions"));

beforeEach(() => {
  localStorage.clear();
});
afterEach(() => {
  cleanup();
  localStorage.clear();
});

describe("BuilderView — resolve-loop and world honesty (P2)", () => {
  it("renders the start card with no session loaded", async () => {
    stubFetch();
    render(<BuilderView {...PROPS} />);
    expect(await screen.findByTestId("builder-start")).toBeTruthy();
  });

  it("(B2) an all-held-back workspace fires no resolve, shows held-out — never resolves", async () => {
    const fetchMock = stubFetch();
    render(<BuilderView {...PROPS} />);

    // New session → the guide; then add a ground segment (memberless → held back).
    const start = await screen.findByTestId("builder-start");
    fireEvent.click(within(start).getByRole("button", { name: /New session/i }));
    const addGround = await screen.findByText("Ground sites");
    fireEvent.click(addGround);

    // The held-out canvas copy appears; no resolve request was ever fired.
    await waitFor(() =>
      expect(screen.getByTestId("builder-canvas").textContent).toContain(
        "Nothing to emit",
      ),
    );
    expect(resolveWorldCalls(fetchMock)).toHaveLength(0);
    const canvas = screen.getByTestId("builder-canvas").textContent ?? "";
    expect(canvas).not.toContain("Resolving draft");
    expect(canvas).not.toContain("does not resolve");
    const status = screen.getByTestId("builder-status").textContent ?? "";
    expect(status).toContain("nothing to emit — content held out");
    expect(status).not.toContain("✓ resolves");
    // The hold-back is stated on the rail, never silent.
    expect(screen.getByTestId("builder-rail").textContent).toContain("held out of the artifact");
  });

  it("(B3) an inactive (hidden) builder does not auto-import a running session", async () => {
    const fetchMock = stubFetch({
      sessions: [
        {
          name: "hidden-running",
          file: "catalog/nodalarc/sessions/hidden-running.yaml",
          source: "nodalarc",
          active: true,
          constellation: "x",
        },
      ],
    });
    render(<BuilderView {...PROPS} active={false} />);
    await waitFor(() => expect(sessionsCalls(fetchMock).length).toBeGreaterThanOrEqual(1));
    // A hidden builder is never a background importer: the auto-import is
    // gated on `active`, so no resolve-world load ever fires.
    await new Promise((resolve) => setTimeout(resolve, 60));
    expect(resolveWorldCalls(fetchMock)).toHaveLength(0);
  });

  it("(N15) opening the picker refetches the sessions list", async () => {
    const fetchMock = stubFetch();
    render(<BuilderView {...PROPS} />);
    await waitFor(() => expect(sessionsCalls(fetchMock).length).toBeGreaterThanOrEqual(1));
    const before = sessionsCalls(fetchMock).length;
    fireEvent.click(await screen.findByRole("button", { name: /Open a session/i }));
    await waitFor(() => expect(sessionsCalls(fetchMock).length).toBe(before + 1));
  });

  it("(resolved-but-preview-pending) a satellite-less resolved world shows the nudge, not a wall", async () => {
    const groundOnlyWorld = jsonResponse({
      world: {
        session: { name: "ground-only" },
        nodes: [
          {
            node_id: "ground-gw1",
            local_node_id: "gw1",
            segment_id: "ground",
            namespace: "ground",
            kind: "ground_station",
            plane: null,
            slot: null,
            tags: [],
            surface_position: { lat_deg: 0, lon_deg: 0, alt_m: 0 },
            forwarding: "routed",
            terminal_inventory: [],
            interfaces: [],
            originated_prefixes: [],
          },
        ],
        link_rules: [],
        segments: [{ segment_id: "ground", display_name: "Ground" }],
        allocations: [],
        link_candidates: [],
        ephemeris: {
          epoch_id: 0,
          sim_time: "2000-01-01T12:00:00Z",
          epoch_unix: 0,
          nodes: {},
          body_frames: { earth: catalogEarthFrame() },
        },
        epoch_unix: 0,
      },
      document_yaml: "session:\n  name: ground-only\n",
      document: { session: { name: "ground-only" } },
      artifact_sha256: "sha-ground",
      deploy_ready: false,
      deploy_blockers: ["no satellites — the session cannot start on the cluster"],
    });
    stubFetch({
      // A running session auto-imports on entry; the resolve returns a
      // satellite-less world, so the world renders even though the document
      // is not fully editable — the RESOLVED-but-preview-pending state.
      sessions: [
        {
          name: "ground-only",
          file: "catalog/nodalarc/sessions/ground-only.yaml",
          source: "nodalarc",
          active: true,
          constellation: "ground",
        },
      ],
      resolveWorld: () => groundOnlyWorld,
    });
    render(<BuilderView {...PROPS} />);

    // Auto-import is a multi-step async chain (sessions fetch → import →
    // resolve-world → world); allow generous time under parallel-suite load.
    await waitFor(
      () => expect(screen.getByTestId("builder-status").textContent).toContain("✓ resolves"),
      { timeout: 3000 },
    );
    const status = screen.getByTestId("builder-status").textContent ?? "";
    expect(status).toContain("no satellites yet — add one to run contact previews");
    // A valid ground-only session is NOT a resolver refusal.
    expect(status).not.toContain("does not resolve");
  });
});
