/** The Builder's anatomy guide says of every shipped session what the backend resolved.
 *
 * fixtures/shipped holds, per shipped session, the visual draft VS-API answers when the Builder
 * opens it and the world the backend resolves for it (scripts/gen_builder_shipped_drafts.py
 * writes both in-process; the pytest contract test keeps them current). The guide is rendered
 * from the draft the way BuilderView renders it, and each row that makes a claim the resolved
 * world can answer is checked against the world. A guide that reads only the draft and claims
 * something the backend resolved otherwise fails here, for whichever session shows it.
 */
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { BuildGuide } from "../BuildGuide";
import { distinctGroundStationSites } from "../builderSnapshot";
import type {
  BuilderIssue,
  BuilderVisualDraftEnvelope,
  BuilderWorld,
} from "../generated/builderApi";
import { workspaceFromVisualDraft } from "../visualWorkspace";

interface ShippedSession {
  source_ref: string;
  draft: BuilderVisualDraftEnvelope;
  world: Pick<BuilderWorld, "session" | "nodes" | "segments" | "link_rules">;
  issues: BuilderIssue[];
}

const FIXTURES = join(dirname(fileURLToPath(import.meta.url)), "fixtures/shipped");
const SESSIONS: ShippedSession[] = readdirSync(FIXTURES)
  .filter((name) => name.endsWith(".json"))
  .sort()
  .map((name) => JSON.parse(readFileSync(join(FIXTURES, name), "utf-8")) as ShippedSession);

afterEach(cleanup);

function row(label: string): string {
  const element = screen.getByText(label).closest(".builder-guide-row");
  if (!element) throw new Error(`no guide row labelled ${label}`);
  return element.textContent ?? "";
}

function renderGuide(session: ShippedSession): void {
  render(
    <BuildGuide
      workspace={workspaceFromVisualDraft(session.draft)}
      sessionNameIsPlaceholder={session.draft.session_name_is_placeholder}
      saved={null}
      deployed={false}
      resolvedSiteCount={distinctGroundStationSites(session.world.nodes)}
      onAddConstellation={() => {}}
      onAddGround={() => {}}
      onAddDomain={() => {}}
      onOpenSession={() => {}}
      onOpenSegment={() => {}}
    />,
  );
}

describe("there are shipped sessions to check", () => {
  it("found every shipped session's fixture", () => {
    expect(SESSIONS.length).toBeGreaterThanOrEqual(12);
  });
});

describe.each(SESSIONS)("$source_ref", (session) => {
  it("counts the space segments the backend resolved", () => {
    renderGuide(session);
    const resolved = new Set(
      session.world.nodes.filter((node) => node.kind === "satellite").map((n) => n.segment_id),
    ).size;
    expect(row("Space segments")).toContain(`${resolved} segment${resolved === 1 ? "" : "s"}`);
  });

  it("counts the link rules the backend resolved", () => {
    renderGuide(session);
    const resolved = session.world.link_rules.length;
    expect(row("Comms intent")).toContain(`${resolved} rule${resolved === 1 ? "" : "s"}`);
  });

  it("says routing runs exactly when the backend resolved routing instances", () => {
    renderGuide(session);
    const participants = session.world.nodes.filter((n) => n.routing_instances.length > 0);
    const shown = row("Routing");
    if (participants.length > 0) {
      const protocols = new Set(
        participants.flatMap((n) => n.routing_instances.map((i) => i.protocol)),
      );
      expect(
        shown,
        `${participants.length} nodes run ${[...protocols].join(", ")}; the guide says: ${shown}`,
      ).not.toMatch(/none yet|no routed traffic/);
    } else {
      expect(shown).toMatch(/none yet/);
    }
  });

  it("names the session the backend resolved", () => {
    renderGuide(session);
    expect(row("Identity & time")).toContain(session.world.session.name);
  });
});
