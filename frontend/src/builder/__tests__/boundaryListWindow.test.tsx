// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** The routing boundary list has one window and is edited as one pending
 *  edit. Each boundary shows its card while the backend states a card value
 *  for it, and its part of the list's field editor otherwise. Every change
 *  goes to the backend, and the window shows what the latest answer states. */
import { useRef, useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { BoundaryListWindow, type RoutingBoundariesChange } from "../RoutingEditor";
import { listSessionObjects } from "../sessionListing";
import type {
  BuilderControlTree,
  BuilderVisualObjectEditResult,
  BuilderVisualRoutingBoundary,
  BuilderVisualRoutingBoundaryListing,
} from "../generated/builderApi";
import { AUTHORING_FACTS } from "./fixtures/authoringFacts";
import {
  BOUNDARIES_ADDRESS,
  acceptedBoundary,
  acceptedDomain,
  acceptedRule,
  boundaryCard,
  domainCard,
  ruleCard,
  sessionListing,
} from "./fixtures/listingFixtures";

afterEach(cleanup);

const APPLIED_REVISION = 4;

/** The session's rules: a fixed rule a card writes back, a rule no card
 *  writes back, and an access rule. */
const RULES = [
  acceptedRule("leo-geo", ["leo", "geo"], ruleCard("leo-geo", "leo", "geo")),
  acceptedRule("geo-relay", ["geo", "luna"], null),
  acceptedRule("gw-access", ["gw", "leo"], ruleCard("gw-access", "gw", "leo", "access")),
];

const DOMAINS = [
  acceptedDomain("leo-core", "isis", ["leo"], domainCard("leo-core", "isis", ["leo"])),
  acceptedDomain("geo-core", "ospf", ["geo"], null),
];

const CARD = boundaryCard(0, "leo-geo", "leo-core", "geo-core");

/** The field editor the backend builds over the boundary list at `revision`:
 *  each boundary's link rule. Control ids differ per tree, as the backend's do. */
function listTree(revision: number, overs: ReadonlyArray<string>): BuilderControlTree {
  return {
    projection_revision: revision,
    root: {
      control_id: `list-${revision}-${overs.join("+")}`,
      json_pointer: "",
      label: "Routing boundaries",
      required: true,
      present: true,
      model_name: "_RoutingBoundaryList",
      fields: [
        {
          field_name: "boundaries",
          wire_name: "boundaries",
          control: {
            control_id: `boundaries-${revision}-${overs.join("+")}`,
            json_pointer: "/boundaries",
            label: "Boundaries",
            required: true,
            present: true,
            can_add: true,
            can_remove: true,
            can_reorder: true,
            items: overs.map((over, index) => ({
              index,
              control: {
                control_id: `boundary-${revision}-${index}`,
                json_pointer: `/boundaries/${index}`,
                label: `Item ${index + 1}`,
                required: true,
                present: true,
                model_name: "RoutingBoundary",
                fields: [
                  {
                    field_name: "over",
                    wire_name: "over",
                    control: {
                      control_id: `over-${revision}-${index}-${over}`,
                      json_pointer: `/boundaries/${index}/over`,
                      label: "Over",
                      required: true,
                      present: true,
                      scalar_kind: "text" as const,
                      value: over,
                    },
                  },
                ],
              },
            })),
          },
        },
      ],
    },
  };
}

/** The backend's answer for the list: the card value of each boundary (or
 *  none for the list) and the field editor over it. */
function answer(
  cards: ReadonlyArray<BuilderVisualRoutingBoundary | null> | null,
  tree: BuilderControlTree,
): BuilderVisualObjectEditResult {
  return {
    address: BOUNDARIES_ADDRESS,
    working: [],
    widget: cards === null ? null : { kind: "routing_boundaries", boundaries: cards },
    control_tree: tree,
    issues: [],
  };
}

interface Backend {
  edit: (change: RoutingBoundariesChange) => Promise<BuilderVisualObjectEditResult>;
  describe: () => Promise<BuilderVisualObjectEditResult>;
}

const NOT_DESCRIBED = () => Promise.reject(new Error("the boundary list was described"));

/** The window as the page shows it: the backend's listing of the accepted
 *  boundaries with the latest answer for the list's pending edit laid over
 *  them. Like the page, it holds an answer only when it answers the latest
 *  edit of the list. */
function BoundariesWindow({
  accepted,
  backend,
}: {
  accepted: ReadonlyArray<BuilderVisualRoutingBoundaryListing>;
  backend: Backend;
}) {
  const [pending, setPending] = useState<BuilderVisualObjectEditResult | null>(null);
  const latest = useRef(0);
  const listed = listSessionObjects(
    sessionListing({ link_rules: RULES, routing_domains: DOMAINS, boundaries: accepted }),
    pending === null ? [] : [pending],
  );
  return (
    <BoundaryListWindow
      listed={listed.boundaries}
      linkRules={listed.linkRules}
      routingDomains={listed.routingDomains}
      appliedRevision={APPLIED_REVISION}
      onHold={() => () => undefined}
      checking={false}
      onEdit={async (change) => {
        const request = ++latest.current;
        const answered = await backend.edit(change);
        if (request !== latest.current) throw new Error("a later edit of the list was made");
        setPending(answered);
        return answered;
      }}
      onDescribe={backend.describe}
      authoring={AUTHORING_FACTS}
    />
  );
}

/** A backend that states each card value it is sent as the list's card value. */
const echo = async (change: RoutingBoundariesChange) =>
  answer(
    change.kind === "routing_boundaries" ? change.boundaries : null,
    listTree(APPLIED_REVISION, []),
  );

function optionLabels(select: HTMLElement): Array<[string, boolean]> {
  return [...(select as HTMLSelectElement).options].map((option) => [
    option.textContent ?? "",
    option.disabled,
  ]);
}

describe("the card of a boundary the backend states a card value for", () => {
  it("offers the listed rules and domains, an access rule disabled", () => {
    const named = { ...CARD, to_domain_id: "meo-core" };
    render(
      <BoundariesWindow
        accepted={[acceptedBoundary(0, "leo-geo", ["leo-core", "meo-core"], named)]}
        backend={{ edit: vi.fn(echo), describe: NOT_DESCRIBED }}
      />,
    );
    // A rule no card writes back is offered; its role is not stated on the page.
    expect(optionLabels(screen.getByLabelText("Boundary link rule"))).toEqual([
      ["leo-geo", false],
      ["geo-relay", false],
      ["gw-access (access)", true],
    ]);
    expect(optionLabels(screen.getByLabelText("From domain"))).toEqual([
      ["leo-core", false],
      ["geo-core", false],
    ]);
    // A domain the session does not declare stays shown as the boundary names it.
    const to = screen.getByLabelText("To domain") as HTMLSelectElement;
    expect(to.value).toBe("meo-core");
    expect(optionLabels(to)).toEqual([
      ["leo-core", false],
      ["geo-core", false],
      ["meo-core (not in the session)", false],
    ]);
  });
});
