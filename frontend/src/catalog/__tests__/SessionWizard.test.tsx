// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** The wizard's shipped-session launcher: catalog sessions deploy as-is.
 *
 * The session list and switch endpoints are the launch path for worked
 * examples; the wizard must surface them (the wiring was once dropped and
 * left the UI with no way to start a shipped session).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import type { SessionInfo } from "../../types";

afterEach(cleanup);

vi.mock("../../config", () => ({
  REST_URL: "http://test:8080",
  authHeaders: (extra?: Record<string, string>) => ({ ...extra }),
}));

const { SessionWizard } = await import("../SessionWizard");

const SESSIONS: SessionInfo[] = [
  {
    name: "earth-leo-polar",
    file: "catalog/nodalarc/sessions/earth-leo-polar.yaml",
    constellation: "leo",
    routing_stack: "isis",
    source: "nodalarc",
    active: true,
  },
  {
    name: "earth-geo-tdrs",
    file: "catalog/nodalarc/sessions/earth-geo-tdrs.yaml",
    constellation: "geo",
    routing_stack: "isis",
    source: "nodalarc",
    active: false,
  },
];

describe("SessionWizard shipped sessions", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve([]) })),
    );
  });

  it("launches an inactive shipped session via the switch path", () => {
    const onLaunchSession = vi.fn();
    const onDeployStarted = vi.fn();
    render(
      <SessionWizard
        onDeployStarted={onDeployStarted}
        onClose={undefined}
        deploying={false}
        sessions={SESSIONS}
        onLaunchSession={onLaunchSession}
      />,
    );

    fireEvent.click(screen.getByText("earth-geo-tdrs"));
    expect(onLaunchSession).toHaveBeenCalledWith(
      "catalog/nodalarc/sessions/earth-geo-tdrs.yaml",
    );
    expect(onDeployStarted).toHaveBeenCalled();
  });

  it("disables the currently running session", () => {
    const onLaunchSession = vi.fn();
    render(
      <SessionWizard
        onDeployStarted={vi.fn()}
        onClose={undefined}
        deploying={false}
        sessions={SESSIONS}
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
});

describe("SessionWizard download errors", () => {
  function renderWizard() {
    render(
      <SessionWizard
        onDeployStarted={vi.fn()}
        onClose={undefined}
        deploying={false}
        sessions={SESSIONS}
        onLaunchSession={vi.fn()}
      />,
    );
    return screen.getByRole("button", { name: "Download earth-leo-polar YAML" });
  }

  it("shows the resolver's own refusal words when a download is refused (422)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) =>
        url.includes("/builder/resolve-world")
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
        url.includes("/builder/resolve-world")
          ? Promise.reject(new Error("Failed to fetch"))
          : Promise.resolve({ ok: true, json: () => Promise.resolve([]) }),
      ),
    );
    fireEvent.click(renderWizard());
    expect(await screen.findByText("Failed to fetch")).toBeTruthy();
  });
});
