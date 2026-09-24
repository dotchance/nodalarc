// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** The wizard's catalog-session launcher: catalog sessions deploy as-is.
 *
 * The session list and switch endpoints are the launch path for worked
 * examples; the wizard must surface them (the wiring was once dropped and
 * left the UI with no way to start a shipped session).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup, within, waitFor } from "@testing-library/react";
import type { SessionInfo } from "../../types";

afterEach(cleanup);

vi.mock("../../config", () => ({
  REST_URL: "http://test:8080",
  authHeaders: (extra?: Record<string, string>) => ({ ...extra }),
}));

const { SessionWizard } = await import("../SessionWizard");

const SESSIONS: SessionInfo[] = [
  {
    source_id: {
      kind: "catalog",
      session_ref: "nodalarc:sessions/earth-leo-polar.yaml",
    },
    name: "earth-leo-polar",
    constellation: "leo",
    routing_stack: "isis",
    source: "nodalarc",
    deploy_allowed: true,
    source_revision: "sha256:0000000000000000000000000000000000000000000000000000000000000001",
    document_digest: "sha256:0000000000000000000000000000000000000000000000000000000000000001",
    dependency_digest: "sha256:0000000000000000000000000000000000000000000000000000000000000011",
    active: true,
  },
  {
    source_id: {
      kind: "catalog",
      session_ref: "nodalarc:sessions/earth-geo-tdrs.yaml",
    },
    name: "earth-geo-tdrs",
    constellation: "geo",
    routing_stack: "isis",
    source: "nodalarc",
    deploy_allowed: true,
    source_revision: "sha256:0000000000000000000000000000000000000000000000000000000000000002",
    document_digest: "sha256:0000000000000000000000000000000000000000000000000000000000000002",
    dependency_digest: "sha256:0000000000000000000000000000000000000000000000000000000000000022",
    active: false,
  },
];

describe("SessionWizard catalog sessions", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve([]) })),
    );
  });

  it("launches an inactive catalog session via the switch path", async () => {
    const onLaunchSession = vi.fn().mockResolvedValue({ ok: true });
    const onDeployStarted = vi.fn();
    render(
      <SessionWizard
        onDeployStarted={onDeployStarted}
        onClose={undefined}
        deploying={false}
        sessions={SESSIONS}
        sessionsError={null}
        onLaunchSession={onLaunchSession}
      />,
    );

    fireEvent.click(screen.getByText("earth-geo-tdrs"));
    expect(onLaunchSession).toHaveBeenCalledWith(SESSIONS[1], false);
    await waitFor(() => expect(onDeployStarted).toHaveBeenCalled());
  });

  it("stays open and shows VS-API's reason when a switch is refused", async () => {
    const onLaunchSession = vi.fn().mockResolvedValue({
      ok: false,
      message: "Prepared session is not deploy-ready",
    });
    const onDeployStarted = vi.fn();
    render(
      <SessionWizard
        onDeployStarted={onDeployStarted}
        onClose={undefined}
        deploying={false}
        sessions={SESSIONS}
        sessionsError={null}
        onLaunchSession={onLaunchSession}
      />,
    );

    fireEvent.click(screen.getByText("earth-geo-tdrs"));

    expect(await screen.findByText("Prepared session is not deploy-ready")).toBeTruthy();
    expect(onDeployStarted).not.toHaveBeenCalled();
  });

  it("says why the session list did not load", () => {
    render(
      <SessionWizard
        onDeployStarted={vi.fn()}
        onClose={undefined}
        deploying={false}
        sessions={SESSIONS}
        sessionsError="request failed (503)"
        onLaunchSession={vi.fn()}
      />,
    );

    expect(screen.getByText("Sessions did not load from VS-API: request failed (503)")).toBeTruthy();
  });

  it("launches with history recording when the launcher toggle is set", () => {
    const onLaunchSession = vi.fn().mockResolvedValue({ ok: true });
    render(
      <SessionWizard
        onDeployStarted={vi.fn()}
        onClose={undefined}
        deploying={false}
        sessions={SESSIONS}
        sessionsError={null}
        onLaunchSession={onLaunchSession}
      />,
    );

    const toggle = screen.getByLabelText("Record session history") as HTMLInputElement;
    expect(toggle.checked).toBe(false);
    fireEvent.click(toggle);
    fireEvent.click(screen.getByText("earth-geo-tdrs"));
    expect(onLaunchSession).toHaveBeenCalledWith(SESSIONS[1], true);
  });

  it("disables the currently running session", () => {
    const onLaunchSession = vi.fn().mockResolvedValue({ ok: true });
    render(
      <SessionWizard
        onDeployStarted={vi.fn()}
        onClose={undefined}
        deploying={false}
        sessions={SESSIONS}
        sessionsError={null}
        onLaunchSession={onLaunchSession}
      />,
    );

    const runningCard = screen
      .getAllByText("earth-leo-polar")
      .map((el) => el.closest("button"))
      .find((b): b is HTMLButtonElement => b !== null);
    expect(runningCard).toBeDefined();
    expect(runningCard!.disabled).toBe(true);
    fireEvent.click(runningCard!);
    expect(onLaunchSession).not.toHaveBeenCalled();
  });

  it("keeps same-named user and shipped sessions visibly distinct", () => {
    const shipped: SessionInfo = { ...SESSIONS[1]!, active: true };
    const user: SessionInfo = {
      ...shipped,
      source_id: {
        kind: "catalog",
        session_ref: "user:sessions/earth-geo-tdrs.yaml",
      },
      source: "user",
      active: false,
    };
    const onLaunchSession = vi.fn().mockResolvedValue({ ok: true });
    render(
      <SessionWizard
        onDeployStarted={vi.fn()}
        onClose={undefined}
        deploying={false}
        sessions={[shipped, user]}
        sessionsError={null}
        onLaunchSession={onLaunchSession}
      />,
    );

    const yours = screen.getByRole("region", { name: "User-created sessions" });
    const provided = screen.getByRole("region", { name: "NodalArc defaults" });
    expect(within(yours).getByText("user:sessions/earth-geo-tdrs.yaml")).toBeTruthy();
    expect(within(yours).getByText("user: editable")).toBeTruthy();
    expect(
      within(provided).getByText("nodalarc:sessions/earth-geo-tdrs.yaml"),
    ).toBeTruthy();
    expect(within(provided).getByText("nodalarc: read-only")).toBeTruthy();
    expect(
      within(provided).getByText("earth-geo-tdrs").closest("button")?.disabled,
    ).toBe(true);
    expect(
      within(yours).getByRole("button", {
        name: "Download user:sessions/earth-geo-tdrs.yaml YAML",
      }),
    ).toBeTruthy();
    expect(
      within(provided).getByRole("button", {
        name: "Download nodalarc:sessions/earth-geo-tdrs.yaml YAML",
      }),
    ).toBeTruthy();

    fireEvent.click(within(yours).getByText("earth-geo-tdrs"));
    expect(onLaunchSession).toHaveBeenCalledWith(user, false);
  });
});

describe("SessionWizard download errors", () => {
  function renderWizard() {
    render(
      <SessionWizard
        onDeployStarted={vi.fn()}
        onClose={undefined}
        deploying={false}
        sessions={SESSIONS}
        sessionsError={null}
        onLaunchSession={vi.fn()}
      />,
    );
    return screen.getByRole("button", {
      name: "Download nodalarc:sessions/earth-leo-polar.yaml YAML",
    });
  }

  it("shows the resolver's own refusal words when a download is refused (422)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) =>
        url.includes("/sessions/yaml?session_ref=")
          ? Promise.resolve({
              ok: false,
              status: 422,
              json: () => Promise.resolve({ error: "segment 'leo' has no satellites" }),
            })
          : Promise.resolve({ ok: true, json: () => Promise.resolve([]) }),
      ),
    );
    fireEvent.click(renderWizard());
    // The envelope's error field reaches the user verbatim — not "fetch failed".
    expect(await screen.findByText("segment 'leo' has no satellites")).toBeTruthy();
  });

  it("shows the network failure's message when a download cannot reach the server", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) =>
        url.includes("/sessions/yaml?session_ref=")
          ? Promise.reject(new Error("Failed to fetch"))
          : Promise.resolve({ ok: true, json: () => Promise.resolve([]) }),
      ),
    );
    fireEvent.click(renderWizard());
    expect(await screen.findByText("Failed to fetch")).toBeTruthy();
  });
});

describe("SessionWizard authoring facts", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("shows loading, then names every source that failed, never an empty Wizard", async () => {
    const pending: ((reason: Error) => void)[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(
        () =>
          new Promise((_resolve, reject) => {
            pending.push(reject);
          }),
      ),
    );
    render(
      <SessionWizard
        onDeployStarted={vi.fn()}
        onClose={undefined}
        deploying={false}
        sessions={[]}
        sessionsError={null}
        onLaunchSession={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByText("Wizard"));

    expect(screen.getByText("Loading Wizard authoring facts from VS-API…")).toBeTruthy();
    expect(screen.queryByText(/did not load/)).toBeNull();

    for (const reject of pending) reject(new Error("VS-API unreachable"));
    expect(
      await screen.findByText(/Wizard authoring facts did not load from VS-API: .*constellation presets: VS-API unreachable/),
    ).toBeTruthy();
  });
});

