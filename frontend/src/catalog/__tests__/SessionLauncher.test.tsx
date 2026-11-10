// Copyright 2024-2026 .chance (dotchance)
// SPDX-License-Identifier: AGPL-3.0-only
/** The catalog-session launcher: catalog sessions deploy as-is.
 *
 * The session list and switch endpoints are the launch path for worked
 * examples; the launcher must surface them (the wiring was once dropped and
 * left the UI with no way to start a shipped session).
 */
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react";
import type { CatalogSessionSummary } from "../../builder/generated/builderApi";

afterEach(cleanup);

vi.mock("../../config", () => ({
  REST_URL: "http://test:8080",
  authHeaders: (extra?: Record<string, string>) => ({ ...extra }),
}));

const { SessionLauncher } = await import("../SessionLauncher");

const SESSIONS: CatalogSessionSummary[] = [
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
    blockers: [],
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
    blockers: [],
    source_revision: "sha256:0000000000000000000000000000000000000000000000000000000000000002",
    document_digest: "sha256:0000000000000000000000000000000000000000000000000000000000000002",
    dependency_digest: "sha256:0000000000000000000000000000000000000000000000000000000000000022",
    active: false,
  },
];

describe("SessionLauncher catalog sessions", () => {
  it("launches an inactive catalog session via the switch path", async () => {
    const onLaunchSession = vi.fn().mockResolvedValue({ ok: true });
    const onDeployStarted = vi.fn();
    render(
      <SessionLauncher
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
      <SessionLauncher
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
      <SessionLauncher
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
      <SessionLauncher
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
      <SessionLauncher
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
});

