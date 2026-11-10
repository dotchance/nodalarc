// Copyright 2024-2026 .chance (dotchance)
// SPDX-License-Identifier: AGPL-3.0-only
/** The Builder's anatomy guide says of every shipped session what the backend resolved.
 *
 * fixtures/shipped holds, per shipped session, the parts of the compile result the Builder
 * receives when it opens and compiles the session: the canonical session document and the
 * world the backend resolves for it (scripts/gen_builder_shipped_drafts.py writes both
 * in-process; the pytest contract test keeps them current). The guide is rendered from that
 * compile result the way BuilderView renders it, and each row that makes a claim the resolved
 * world can answer is checked against the world.
 */
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { BuildGuide } from "../BuildGuide";
import type { BuilderWorld, JsonValue } from "../generated/builderApi";

interface ShippedSession {
  source_ref: string;
  compile: {
    canonical_session_json: Readonly<Record<string, JsonValue>>;
    resolved_preview: Pick<BuilderWorld, "session" | "nodes" | "segments" | "link_rules">;
  };
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

function detail(label: string): string {
  const element = screen
    .getByText(label)
    .closest(".builder-guide-row")
    ?.querySelector(".builder-guide-detail");
  if (!element) throw new Error(`no guide detail for ${label}`);
  return element.textContent ?? "";
}

function renderGuide(session: ShippedSession): void {
  render(
    <BuildGuide
      compiled={session.compile}
      refusal={null}
      hasAcceptedSession
      placeholderName={null}
      saved={null}
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
  const world = session.compile.resolved_preview;

  it("counts the space segments the backend resolved", () => {
    renderGuide(session);
    const resolved = new Set(
      world.nodes.filter((node) => node.kind === "satellite").map((n) => n.segment_id),
    ).size;
    expect(detail("Space segments")).toBe(`${resolved} segment${resolved === 1 ? "" : "s"} · add more`);
  });

  it("counts the link rules the backend resolved", () => {
    renderGuide(session);
    const resolved = world.link_rules.length;
    expect(detail("Comms intent")).toBe(`${resolved} rule${resolved === 1 ? "" : "s"}`);
  });

  it("names every routing domain the backend resolved, with its protocol and participants", () => {
    renderGuide(session);
    const expected = new Map<string, { protocol: string; participants: number }>();
    for (const node of world.nodes) {
      for (const instance of node.routing_instances) {
        const domain = expected.get(instance.domain_id) ?? {
          protocol: instance.protocol,
          participants: 0,
        };
        domain.participants += 1;
        expected.set(instance.domain_id, domain);
      }
    }
    const named = new Set(
      [...document.querySelectorAll(".builder-guide-domain")].map((el) => el.textContent),
    );
    const wanted = new Set(
      [...expected].map(
        ([id, domain]) =>
          `${id} · ${domain.protocol} · ${domain.participants} node${domain.participants === 1 ? "" : "s"}`,
      ),
    );
    expect(named).toEqual(wanted);
  });

  it("says routing was omitted exactly when the session declares none", () => {
    renderGuide(session);
    const declared = session.compile.canonical_session_json.routing !== undefined;
    if (declared) {
      expect(row("Routing")).not.toContain("routing omitted");
    } else {
      expect(row("Routing")).toContain("routing omitted");
    }
  });

  it("names the session the backend resolved", () => {
    renderGuide(session);
    expect(detail("Identity & time")).toBe(world.session.name);
  });
});
