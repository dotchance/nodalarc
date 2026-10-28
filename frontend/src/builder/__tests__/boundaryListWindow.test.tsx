// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** The routing boundary list has one window and is edited as one pending
 *  edit. Each boundary shows its card while the backend states a card value
 *  for it, and its part of the list's field editor otherwise. Every change
 *  goes to the backend, and the window shows what the latest answer states. */
import { useRef, useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { BoundaryListWindow, type RoutingBoundariesChange } from "../RoutingEditor";
import { listSessionObjects } from "../sessionListing";
import type {
  BuilderControlTree,
  BuilderIssue,
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
  workingIssue,
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

/** A carded boundary at index 0 and a boundary no card writes back at index 1. */
const MIXED = [
  acceptedBoundary(0, "leo-geo", ["leo-core", "geo-core"], CARD),
  acceptedBoundary(1, "geo-relay", ["geo-core", "leo-core"], null),
];

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
 *  none for the list), the field editor over it and its issues. */
function answer(
  cards: ReadonlyArray<BuilderVisualRoutingBoundary | null> | null,
  tree: BuilderControlTree,
  issues: ReadonlyArray<BuilderIssue> = [],
): BuilderVisualObjectEditResult {
  return {
    address: BOUNDARIES_ADDRESS,
    working: [],
    widget: cards === null ? null : { kind: "routing_boundaries", boundaries: cards },
    control_tree: tree,
    issues,
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
  pendingAtOpen = null,
}: {
  accepted: ReadonlyArray<BuilderVisualRoutingBoundaryListing>;
  backend: Backend;
  pendingAtOpen?: BuilderVisualObjectEditResult | null;
}) {
  const [pending, setPending] = useState(pendingAtOpen);
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
  it("sends a change as the card value of every boundary and shows the answer", async () => {
    const edit = vi.fn(echo);
    const describeList = vi.fn(NOT_DESCRIBED);
    render(
      <BoundariesWindow
        accepted={[acceptedBoundary(0, "leo-geo", ["leo-core", "geo-core"], CARD)]}
        backend={{ edit, describe: describeList }}
      />,
    );
    const loopbacks = screen.getByLabelText("export loopbacks") as HTMLInputElement;
    expect(loopbacks.checked).toBe(true);

    fireEvent.click(loopbacks);
    await act(async () => undefined);

    expect(edit.mock.calls.map(([change]) => change)).toEqual([
      { kind: "routing_boundaries", boundaries: [{ ...CARD, export_node_loopbacks: false }] },
    ]);
    expect((screen.getByLabelText("export loopbacks") as HTMLInputElement).checked).toBe(false);
    expect(screen.getByText("Pending edit. It is not applied to the session yet.")).toBeTruthy();
    // Every boundary has a card, so the list's field editor is never asked for.
    expect(describeList).not.toHaveBeenCalled();
  });

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

  it("shows a refused edit and returns to what the backend last stated", async () => {
    const edit = vi.fn(() => Promise.reject(new Error("VS-API could not be reached")));
    render(
      <BoundariesWindow
        accepted={[acceptedBoundary(0, "leo-geo", ["leo-core", "geo-core"], CARD)]}
        backend={{ edit, describe: NOT_DESCRIBED }}
      />,
    );

    fireEvent.change(screen.getByLabelText("Boundary adapter"), { target: { value: "bgp" } });
    await act(async () => undefined);

    expect(screen.getByText("VS-API could not be reached")).toBeTruthy();
    expect((screen.getByLabelText("Boundary adapter") as HTMLSelectElement).value).toBe(
      "static_ip",
    );
    expect(screen.queryByText(/Pending edit/)).toBeNull();
  });
});

describe("a list mixing a boundary a card writes back and one no card writes back", () => {
  it("shows one card and the other boundary's field editor, and sends each", async () => {
    const describeList = vi.fn(async () =>
      answer([CARD, null], listTree(APPLIED_REVISION, ["leo-geo", "geo-relay"])),
    );
    const edit = vi.fn(async (change: RoutingBoundariesChange) =>
      change.kind === "controls"
        ? answer([CARD, null], listTree(APPLIED_REVISION, ["leo-geo", "geo-relay-2"]), [
            workingIssue("Unknown link rule geo-relay-2", "/1/over"),
          ])
        : answer(change.boundaries, listTree(APPLIED_REVISION, ["leo-geo", "geo-relay-2"])),
    );
    render(<BoundariesWindow accepted={MIXED} backend={{ edit, describe: describeList }} />);

    expect(screen.getByText("Loading the field editor…")).toBeTruthy();
    await act(async () => undefined);
    expect(screen.getByText("Boundary 1")).toBeTruthy();
    expect(screen.getByText("Boundary 2")).toBeTruthy();
    // One card, and the field editor of the second boundary alone.
    expect(screen.getAllByTestId("builder-boundary-editor")).toHaveLength(1);
    const over = screen.getAllByLabelText("Over");
    expect(over).toHaveLength(1);
    expect((over[0] as HTMLInputElement).value).toBe("geo-relay");

    fireEvent.change(over[0]!, { target: { value: "geo-relay-2" } });
    fireEvent.blur(over[0]!);
    await act(async () => undefined);

    expect(edit.mock.calls[0]![0]).toEqual({
      kind: "controls",
      commands: [
        { operation: "set_scalar", control_id: "over-4-1-geo-relay", value: "geo-relay-2" },
      ],
    });
    expect((screen.getByLabelText("Over") as HTMLInputElement).value).toBe("geo-relay-2");
    expect(screen.getByText("Unknown link rule geo-relay-2")).toBeTruthy();
    expect(screen.getByText("/1/over")).toBeTruthy();

    fireEvent.click(screen.getByLabelText("export loopbacks"));
    await act(async () => undefined);

    // The card value of the boundary no card writes back is null, so the
    // backend leaves that boundary as the working list holds it.
    expect(edit.mock.calls[1]![0]).toEqual({
      kind: "routing_boundaries",
      boundaries: [{ ...CARD, export_node_loopbacks: false }, null],
    });
    expect(describeList).toHaveBeenCalledTimes(1);
  });

  it("makes one change of the list at a time", async () => {
    let answerField: (result: BuilderVisualObjectEditResult) => void = () => undefined;
    let answerCard: (result: BuilderVisualObjectEditResult) => void = () => undefined;
    const edit = vi.fn(
      (change: RoutingBoundariesChange) =>
        new Promise<BuilderVisualObjectEditResult>((resolve) => {
          if (change.kind === "controls") answerField = resolve;
          else answerCard = resolve;
        }),
    );
    const tree = listTree(APPLIED_REVISION, ["leo-geo", "geo-relay"]);
    render(
      <BoundariesWindow
        accepted={MIXED}
        backend={{ edit, describe: async () => answer([CARD, null], tree) }}
      />,
    );
    await act(async () => undefined);

    // While a field-editor batch is sent, the card waits.
    const over = screen.getByLabelText("Over");
    fireEvent.change(over, { target: { value: "geo-relay-2" } });
    fireEvent.blur(over);
    const loopbacks = () =>
      within(screen.getByTestId("builder-boundary-editor")).getByLabelText("export loopbacks");
    expect(loopbacks().matches(":disabled")).toBe(true);
    await act(async () => answerField(answer([CARD, null], tree)));
    expect(loopbacks().matches(":disabled")).toBe(false);

    // While a card change is checked, the field editor waits.
    fireEvent.click(loopbacks());
    expect(screen.getByLabelText("Over").matches(":disabled")).toBe(true);
    await act(async () =>
      answerCard(answer([{ ...CARD, export_node_loopbacks: false }, null], tree)),
    );
    expect(screen.getByLabelText("Over").matches(":disabled")).toBe(false);
    expect(edit).toHaveBeenCalledTimes(2);
  });
});

describe("a boundary list whose pending edit no card value states", () => {
  it("shows the field editor of the whole list", () => {
    const tree = listTree(APPLIED_REVISION, ["leo-geo", "geo-relay"]);
    render(
      <BoundariesWindow
        accepted={MIXED}
        backend={{ edit: vi.fn(echo), describe: NOT_DESCRIBED }}
        pendingAtOpen={answer(null, tree, [workingIssue("Field required", "/0/adapter")])}
      />,
    );
    expect(screen.queryByTestId("builder-boundary-editor")).toBeNull();
    expect(screen.queryByText("Boundary 1")).toBeNull();
    expect(
      screen.getAllByLabelText("Over").map((input) => (input as HTMLInputElement).value),
    ).toEqual(["leo-geo", "geo-relay"]);
    expect(screen.getByText("Field required")).toBeTruthy();
  });
});

describe("a removal of a boundary", () => {
  const ACCEPTED = [acceptedBoundary(0, "leo-geo", ["leo-core", "geo-core"], CARD)];

  it("removes the boundary's item through the list's field editor, asking for it first", async () => {
    // Every boundary has a card, so no field editor is known until the removal asks.
    const describeList = vi.fn(async () =>
      answer([CARD], listTree(APPLIED_REVISION, ["leo-geo"])),
    );
    const edit = vi.fn(async (_change: RoutingBoundariesChange) =>
      answer([], listTree(APPLIED_REVISION, [])),
    );
    render(<BoundariesWindow accepted={ACCEPTED} backend={{ edit, describe: describeList }} />);
    expect(describeList).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Remove boundary" }));
    await act(async () => undefined);

    expect(describeList).toHaveBeenCalledTimes(1);
    expect(edit.mock.calls.map(([change]) => change)).toEqual([
      {
        kind: "controls",
        commands: [{ operation: "remove_item", control_id: "boundaries-4-leo-geo", index: 0 }],
      },
    ]);
    // The removal is the list's pending edit; nothing is accepted.
    expect(screen.getByText("The list holds no boundary.")).toBeTruthy();
    expect(screen.getByText("Pending edit. It is not applied to the session yet.")).toBeTruthy();
  });

  it("removes a boundary from the list's pending edit over the field editor it answered", async () => {
    const pendingTree = listTree(APPLIED_REVISION, ["leo-geo", "geo-relay"]);
    const edit = vi.fn(async (_change: RoutingBoundariesChange) =>
      answer([{ ...CARD, adapter: "bgp" }], listTree(APPLIED_REVISION, ["leo-geo"])),
    );
    render(
      <BoundariesWindow
        accepted={ACCEPTED}
        backend={{ edit, describe: NOT_DESCRIBED }}
        pendingAtOpen={answer([{ ...CARD, adapter: "bgp" }, CARD], pendingTree)}
      />,
    );
    const [, second] = screen.getAllByRole("button", { name: "Remove boundary" });

    fireEvent.click(second!);
    await act(async () => undefined);

    expect(edit.mock.calls.map(([change]) => change)).toEqual([
      {
        kind: "controls",
        commands: [
          { operation: "remove_item", control_id: "boundaries-4-leo-geo+geo-relay", index: 1 },
        ],
      },
    ]);
    expect(screen.getAllByTestId("builder-boundary-editor")).toHaveLength(1);
  });

  it("shows a removal the backend refuses and keeps the list", async () => {
    const edit = vi.fn(() => Promise.reject(new Error("VS-API could not be reached")));
    render(
      <BoundariesWindow
        accepted={ACCEPTED}
        backend={{
          edit,
          describe: async () => answer([CARD], listTree(APPLIED_REVISION, ["leo-geo"])),
        }}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Remove boundary" }));
    await act(async () => undefined);

    expect(screen.getByText("VS-API could not be reached")).toBeTruthy();
    expect(screen.getAllByTestId("builder-boundary-editor")).toHaveLength(1);
    expect(screen.queryByText(/Pending edit/)).toBeNull();
  });

  it("waits while a change of the list is checked", () => {
    const unanswered = {
      edit: vi.fn(() => new Promise<never>(() => undefined)),
      describe: NOT_DESCRIBED,
    };
    render(<BoundariesWindow accepted={ACCEPTED} backend={unanswered} />);
    fireEvent.click(screen.getByLabelText("export loopbacks"));
    expect(screen.getByText("Checking the edit…")).toBeTruthy();
    expect(
      (screen.getByRole("button", { name: "Remove boundary" }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });
});
