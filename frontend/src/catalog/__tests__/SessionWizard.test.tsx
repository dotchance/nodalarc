// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** The wizard's shipped-session launcher: catalog sessions deploy as-is.
 *
 * The session list and switch endpoints are the launch path for worked
 * examples; the wizard must surface them (the wiring was once dropped and
 * left the UI with no way to start a shipped session).
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import type { SessionInfo } from "../../types";

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
    active: true,
  },
  {
    name: "earth-geo-tdrs",
    file: "catalog/nodalarc/sessions/earth-geo-tdrs.yaml",
    constellation: "geo",
    routing_stack: "isis",
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
