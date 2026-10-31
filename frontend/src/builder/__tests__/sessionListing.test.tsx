// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** The outline and the segment links card list every link rule, routing
 *  domain and boundary the backend lists for the accepted session, with the
 *  pending edits laid over them. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { LinkAndRoutingOutline } from "../LinkAndRoutingOutline";
import { SegmentLinksCard } from "../SegmentLinksCard";
import { listSessionObjects } from "../sessionListing";
import type { GuideWorldNode } from "../BuildGuide";
import { sessionSegments } from "../sessionSegments";
import {
  BOUNDARIES_ADDRESS,
  acceptedBoundary,
  acceptedDomain,
  acceptedRule,
  boundaryCard,
  domainAddress,
  domainCard,
  pendingAnswer,
  ruleAddress,
  ruleCard,
  sessionListing,
} from "./fixtures/listingFixtures";

afterEach(cleanup);

/** An accepted session with a rule and a domain each card writes back, a rule
 *  and a domain no card writes back, and a boundary of each kind. */
const LISTING = sessionListing({
  link_rules: [
    acceptedRule("leo-isl", ["leo"], ruleCard("leo-isl", "leo", "leo")),
    acceptedRule("geo-relay", ["geo", "leo"], null),
  ],
  routing_domains: [
    acceptedDomain("leo-core", "isis", ["leo"], domainCard("leo-core", "isis", ["leo"])),
    acceptedDomain("geo-core", "ospf", ["geo"], null),
  ],
  boundaries: [
    acceptedBoundary(
      0,
      "geo-relay",
      ["leo-core", "geo-core"],
      boundaryCard(0, "geo-relay", "leo-core", "geo-core"),
    ),
    acceptedBoundary(1, "leo-isl", ["leo-core", "geo-core"], null),
  ],
});

describe("listSessionObjects", () => {
  it("lists every accepted rule, domain and boundary in order, card or none", () => {
    const listed = listSessionObjects(LISTING, []);
    expect(listed.linkRules.map((rule) => [rule.id, rule.card?.rule_id ?? null])).toEqual([
      ["leo-isl", "leo-isl"],
      ["geo-relay", null],
    ]);
    expect(listed.linkRules[1]!.segmentIds).toEqual(["geo", "leo"]);
    expect(listed.routingDomains.map((domain) => [domain.id, domain.protocol])).toEqual([
      ["leo-core", "isis"],
      ["geo-core", "ospf"],
    ]);
    expect(listed.boundaries.items?.map((boundary) => boundary.overRuleId)).toEqual([
      "geo-relay",
      "leo-isl",
    ]);
  });

  it("states what a pending edit's latest answer states, never the accepted value", () => {
    const listed = listSessionObjects(LISTING, [
      pendingAnswer(ruleAddress("leo-isl"), {
        kind: "link_rule",
        rule: ruleCard("leo-isl", "leo", "meo", "isl", false),
      }),
      pendingAnswer(domainAddress("leo-core"), null),
    ]);
    const rule = listed.linkRules[0]!;
    expect(rule.pending).not.toBeNull();
    expect(rule.card?.enabled).toBe(false);
    // The accepted rule names leo; its pending card value also names meo.
    expect(rule.segmentIds).toEqual(["leo", "meo"]);
    const domain = listed.routingDomains[0]!;
    expect(domain.card).toBeNull();
    expect(domain.protocol).toBeNull();
  });

  it("adds an object only a pending edit holds after the accepted ones", () => {
    const listed = listSessionObjects(LISTING, [
      pendingAnswer(domainAddress("meo-core"), {
        kind: "routing_domain",
        domain: domainCard("meo-core", "ospf", ["meo"]),
      }),
    ]);
    expect(
      listed.routingDomains.map((domain) => [domain.id, domain.accepted, domain.protocol]),
    ).toEqual([
      ["leo-core", LISTING.routing_domains[0], "isis"],
      ["geo-core", LISTING.routing_domains[1], "ospf"],
      ["meo-core", null, "ospf"],
    ]);
  });

  it("replaces every boundary with the boundary list's pending edit", () => {
    const stated = listSessionObjects(LISTING, [
      pendingAnswer(BOUNDARIES_ADDRESS, {
        kind: "routing_boundaries",
        boundaries: [boundaryCard(0, "leo-isl", "leo-core", "geo-core")],
      }),
    ]);
    expect(stated.boundaries.items).toEqual([
      {
        accepted: null,
        card: boundaryCard(0, "leo-isl", "leo-core", "geo-core"),
        overRuleId: "leo-isl",
      },
    ]);
    const unstated = listSessionObjects(LISTING, [pendingAnswer(BOUNDARIES_ADDRESS, null)]);
    expect(unstated.boundaries.items).toBeNull();
  });

  it("lists nothing for a draft with no accepted session, where no edit can be pending", () => {
    expect(listSessionObjects(null, [])).toEqual({
      linkRules: [],
      routingDomains: [],
      boundaries: { pending: null, items: [] },
    });
    expect(() => listSessionObjects(null, [pendingAnswer(ruleAddress("leo-isl"), null)])).toThrow(
      /no accepted session/,
    );
  });

  it("refuses an answer whose card value is for another kind of object", () => {
    expect(() =>
      listSessionObjects(LISTING, [
        pendingAnswer(ruleAddress("leo-isl"), {
          kind: "routing_domain",
          domain: domainCard("leo-isl", "isis", ["leo"]),
        }),
      ]),
    ).toThrow(/link rule answered a routing_domain/);
  });
});

/** Nodes of a resolved world: three run in one domain, one in another. */
function resolvedNodes(): GuideWorldNode[] {
  const node = (domainId: string, protocol: "isis" | "ospf"): GuideWorldNode => ({
    kind: "satellite",
    namespace: "leo",
    routing_instances: [{ domain_id: domainId, protocol }],
  });
  return [
    node("made-a", "isis"),
    node("made-a", "isis"),
    node("made-a", "isis"),
    node("made-b", "ospf"),
  ];
}

function renderOutline(
  listed: ReturnType<typeof listSessionObjects>,
  options: { nodes?: GuideWorldNode[] | null; refusal?: string | null } = {},
) {
  const onOpen = vi.fn();
  render(
    <LinkAndRoutingOutline
      listed={listed}
      hasSegments
      resolvedNodes={options.nodes === undefined ? resolvedNodes() : options.nodes}
      refusal={options.refusal ?? null}
      isOpen={() => false}
      onOpen={onOpen}
    />,
  );
  return onOpen;
}

describe("the outline's Links and Routing groups", () => {
  it("marks a pending row and states only what its edit's answer states", () => {
    const onOpen = renderOutline(
      listSessionObjects(LISTING, [
        pendingAnswer(ruleAddress("leo-isl"), {
          kind: "link_rule",
          rule: ruleCard("leo-isl", "leo", "leo", "isl", false),
        }),
        pendingAnswer(domainAddress("leo-core"), null),
      ]),
    );
    const rule = screen.getByRole("button", { name: /^leo-isl/ });
    expect(rule.textContent).toContain("isl · off · pending edit");
    // A pending rule or domain opens its window, which shows the pending edit.
    fireEvent.click(rule);
    expect(onOpen).toHaveBeenCalledWith({ kind: "link", id: "leo-isl" });
    const domain = screen.getByRole("button", { name: /leo-core/ });
    expect(domain.textContent).toContain("pending edit");
    expect(domain.textContent).not.toContain("isis");
    fireEvent.click(domain);
    expect(onOpen).toHaveBeenCalledWith({ kind: "domain", id: "leo-core" });
  });

  it("shows the resolver's domains read-only when the session declares none", () => {
    renderOutline(listSessionObjects(sessionListing({}), []));
    expect(screen.getByText("Routing omitted. The resolver's default domains:")).toBeTruthy();
    expect(screen.getByText("made-a").closest(".builder-library-entry")?.textContent).toContain(
      "isis · 3 nodes",
    );
    expect(screen.getByText("made-b").closest(".builder-library-entry")?.textContent).toContain(
      "ospf · 1 node",
    );
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("shows the refusal and no count when undeclared routing does not resolve", () => {
    renderOutline(listSessionObjects(sessionListing({}), []), {
      nodes: null,
      refusal: "segment leo has no node model",
    });
    expect(
      screen.getByText("Routing omitted. Not resolved: segment leo has no node model"),
    ).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/\d+ nodes?/);
  });
});

describe("the segment links card", () => {
  it("lists and opens every rule that names the segment, a card's or not", () => {
    const placed = sessionSegments({
      segments: [
        { id: "leo", display_name: "LEO", source: "nodalarc:constellations/leo.yaml" },
        { id: "geo", display_name: "GEO", source: "nodalarc:constellations/geo.yaml" },
      ],
    });
    const onOpenRule = vi.fn();
    render(
      <SegmentLinksCard
        placed={placed}
        rules={listSessionObjects(LISTING, []).linkRules}
        segmentId="leo"
        onOpenRule={onOpenRule}
        onConnect={() => {}}
      />,
    );
    expect(screen.getByText("2 rules")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /leo-isl/ }));
    expect(onOpenRule).toHaveBeenCalledWith("leo-isl");
    expect(screen.getByText("leo-isl").closest("button")?.textContent).toContain("isl · mesh");
    // No card writes geo-relay back: the row names the other segment the rule
    // names and opens the rule's window.
    const relay = screen.getByRole("button", { name: /geo-relay/ });
    expect(relay.textContent).toContain("GEO");
    fireEvent.click(relay);
    expect(onOpenRule).toHaveBeenCalledWith("geo-relay");
  });
});
