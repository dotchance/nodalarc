// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** A routing domain's window edits the domain as a pending edit. It shows the
 *  card while the backend states a card value for the domain and the field
 *  editor otherwise. Every change goes to the backend, and the window shows
 *  what the latest answer states. */
import { useRef, useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { RoutingDomainWindow, type RoutingDomainChange } from "../RoutingEditor";
import { listSessionObjects } from "../sessionListing";
import type {
  BuilderControlTree,
  BuilderIssue,
  BuilderVisualObjectEditResult,
  BuilderVisualRoutingDomain,
  BuilderVisualRoutingDomainListing,
} from "../generated/builderApi";
import { AUTHORING_FACTS } from "./fixtures/authoringFacts";
import { newWorkspace } from "./fixtures/workspaceFixtures";
import {
  acceptedDomain,
  domainAddress,
  domainCard,
  sessionListing,
  workingIssue,
} from "./fixtures/listingFixtures";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const APPLIED_REVISION = 6;
const CARD = domainCard("leo-core", "isis", ["leo"]);

/** The issue the backend raises on a card value that sets one timer only. */
const TIMERS_INCOMPLETE: BuilderIssue = {
  ...workingIssue(
    "Routing timers require both hello and hold intervals",
    "/timers/hold_interval_s",
  ),
  code: "builder.draft.routing_timers_incomplete",
  stage: "draft",
};

/** The field editor the backend builds over a domain's working JSON at
 *  `revision`: its two timers. Control ids differ per tree, as the backend's do. */
function timersTree(
  revision: number,
  hello: number | null,
  hold: number | null,
): BuilderControlTree {
  const timer = (name: string, label: string, value: number | null) => ({
    field_name: name,
    wire_name: name,
    control: {
      control_id: `${name}-${revision}-${hello}-${hold}`,
      json_pointer: `/timers/${name}`,
      label,
      required: false,
      present: value !== null,
      scalar_kind: "number" as const,
      number_kind: "integer" as const,
      value,
    },
  });
  return {
    projection_revision: revision,
    root: {
      control_id: `domain-${revision}-${hello}-${hold}`,
      json_pointer: "",
      label: "Routing domain",
      required: true,
      present: true,
      model_name: "RoutingDomain",
      fields: [
        timer("hello_interval_s", "Hello interval s", hello),
        timer("hold_interval_s", "Hold interval s", hold),
      ],
    },
  };
}

/** The backend's answer for the domain: the card value that writes it back (or
 *  none), the field editor over it and its issues. */
function answer(
  card: BuilderVisualRoutingDomain | null,
  tree: BuilderControlTree = timersTree(
    APPLIED_REVISION,
    card?.hello_interval_s ?? null,
    card?.hold_interval_s ?? null,
  ),
  issues: ReadonlyArray<BuilderIssue> = [],
): BuilderVisualObjectEditResult {
  return {
    address: domainAddress("leo-core"),
    working: { id: "leo-core", protocol: "isis" },
    widget: card === null ? null : { kind: "routing_domain", domain: card },
    control_tree: tree,
    issues,
  };
}

interface Backend {
  edit: (change: RoutingDomainChange) => Promise<BuilderVisualObjectEditResult>;
  describe: () => Promise<BuilderVisualObjectEditResult>;
}

const NOT_DESCRIBED = () => Promise.reject(new Error("the domain was described"));

/** The window as the page shows it: the backend's listing of the accepted
 *  domain with the latest answer for its pending edit laid over it. Like the
 *  page, it holds an answer only when it answers the latest edit of the domain. */
function DomainWindow({
  accepted,
  backend,
  pendingAtOpen = null,
  onRemove = null,
}: {
  accepted: BuilderVisualRoutingDomainListing;
  backend: Backend;
  pendingAtOpen?: BuilderVisualObjectEditResult | null;
  onRemove?: (() => void) | null;
}) {
  const [pending, setPending] = useState(pendingAtOpen);
  const latest = useRef(0);
  const workspace = newWorkspace("routing");
  workspace.space_refs = [
    { segment_id: "leo", source_ref: "nodalarc:constellations/leo.yaml", label: "LEO" },
    { segment_id: "geo", source_ref: "nodalarc:constellations/geo.yaml", label: "GEO" },
  ];
  const [listed] = listSessionObjects(
    sessionListing({ routing_domains: [accepted] }),
    pending === null ? [] : [pending],
  ).routingDomains;
  return (
    <RoutingDomainWindow
      listed={listed!}
      appliedRevision={APPLIED_REVISION}
      onEdit={async (change) => {
        const request = ++latest.current;
        const answered = await backend.edit(change);
        if (request !== latest.current) throw new Error("a later edit of the domain was made");
        setPending(answered);
        return answered;
      }}
      onDescribe={backend.describe}
      workspace={workspace}
      onRemove={onRemove}
      authoring={AUTHORING_FACTS}
    />
  );
}

/** The card's two timer inputs, hello then hold. */
function timerInputs(): [HTMLInputElement, HTMLInputElement] {
  const [hello, hold] = screen.getAllByPlaceholderText("engine default");
  return [hello as HTMLInputElement, hold as HTMLInputElement];
}

/** A backend that states each card value it is sent as the domain's card value. */
const echo = async (change: RoutingDomainChange) =>
  answer(change.kind === "routing_domain" ? change.domain : null);

describe("the card of a domain the backend states a card value for", () => {
  it("sends a member pick at once and a typed timer after a pause, each as the whole card value", async () => {
    vi.useFakeTimers();
    const edit = vi.fn(echo);
    render(
      <DomainWindow
        accepted={acceptedDomain("leo-core", "isis", ["leo"], CARD)}
        backend={{ edit, describe: NOT_DESCRIBED }}
      />,
    );

    fireEvent.click(screen.getByLabelText("GEO"));
    expect(edit.mock.calls.map(([change]) => change)).toEqual([
      { kind: "routing_domain", domain: { ...CARD, member_segment_ids: ["leo", "geo"] } },
    ]);
    await act(async () => undefined);

    const [hello] = timerInputs();
    for (const typed of ["1", "10"]) fireEvent.change(hello, { target: { value: typed } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(349);
    });
    expect(edit).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    // One request for the typing, built on the answered pick.
    expect(edit.mock.calls.map(([change]) => change)).toEqual([
      { kind: "routing_domain", domain: { ...CARD, member_segment_ids: ["leo", "geo"] } },
      {
        kind: "routing_domain",
        domain: { ...CARD, member_segment_ids: ["leo", "geo"], hello_interval_s: 10 },
      },
    ]);
    expect(screen.getByText("Pending edit. It is not applied to the session yet.")).toBeTruthy();
  });

  it("clears the timers when the protocol picked has none", async () => {
    const edit = vi.fn(echo);
    const timed = { ...CARD, hello_interval_s: 2, hold_interval_s: 6 };
    render(
      <DomainWindow
        accepted={acceptedDomain("leo-core", "isis", ["leo"], timed)}
        backend={{ edit, describe: NOT_DESCRIBED }}
      />,
    );
    expect(screen.getByText("hello 2s · hold 6s")).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Routing protocol"), { target: { value: "static" } });
    await act(async () => undefined);

    expect(edit.mock.calls.map(([change]) => change)).toEqual([
      {
        kind: "routing_domain",
        domain: { ...timed, protocol: "static", hello_interval_s: null, hold_interval_s: null },
      },
    ]);
    expect(screen.queryByText("Timers")).toBeNull();
  });

  it("shows a member the session no longer places, so it can be removed", async () => {
    const edit = vi.fn(echo);
    const card = { ...CARD, member_segment_ids: ["leo", "meo"] };
    render(
      <DomainWindow
        accepted={acceptedDomain("leo-core", "isis", ["leo", "meo"], card)}
        backend={{ edit, describe: NOT_DESCRIBED }}
      />,
    );
    expect(screen.getByText("1 of 2 segments · 1 removed")).toBeTruthy();
    const removed = screen.getByLabelText("meo (removed)") as HTMLInputElement;
    expect(removed.checked).toBe(true);

    fireEvent.click(removed);
    await act(async () => undefined);

    expect(edit.mock.calls.map(([change]) => change)).toEqual([
      { kind: "routing_domain", domain: { ...card, member_segment_ids: ["leo"] } },
    ]);
  });

  it("shows a refused edit and returns to what the backend last stated", async () => {
    const edit = vi.fn(() => Promise.reject(new Error("VS-API could not be reached")));
    render(
      <DomainWindow
        accepted={acceptedDomain("leo-core", "isis", ["leo"], CARD)}
        backend={{ edit, describe: NOT_DESCRIBED }}
      />,
    );

    fireEvent.click(screen.getByLabelText("GEO"));
    await act(async () => undefined);

    expect(screen.getByText("VS-API could not be reached")).toBeTruthy();
    expect((screen.getByLabelText("GEO") as HTMLInputElement).checked).toBe(false);
    expect(screen.queryByText(/Pending edit/)).toBeNull();
  });
});

describe("a removal of a domain with an edit", () => {
  it("waits while the domain's edit is pending, and runs on the accepted domain otherwise", () => {
    const onRemove = vi.fn();
    const accepted = acceptedDomain("leo-core", "isis", ["leo"], CARD);
    const backend = { edit: vi.fn(), describe: NOT_DESCRIBED };
    render(<DomainWindow accepted={accepted} backend={backend} onRemove={onRemove} />);
    fireEvent.click(screen.getByRole("button", { name: "Remove domain" }));
    expect(onRemove).toHaveBeenCalledTimes(1);
    cleanup();

    render(
      <DomainWindow
        accepted={accepted}
        backend={backend}
        pendingAtOpen={answer({ ...CARD, member_segment_ids: ["leo", "geo"] })}
        onRemove={onRemove}
      />,
    );
    const remove = screen.getByRole("button", { name: "Remove domain" }) as HTMLButtonElement;
    expect(remove.disabled).toBe(true);
    fireEvent.click(remove);
    expect(onRemove).toHaveBeenCalledTimes(1);
  });
});

describe("the field editor of a domain no card writes back", () => {
  it("edits the tree the backend describes and shows the answer's tree and issues", async () => {
    const describeDomain = vi.fn(async () => answer(null, timersTree(APPLIED_REVISION, 2, 6)));
    const edit = vi.fn(async () =>
      answer(null, timersTree(APPLIED_REVISION, 2, 9), [
        workingIssue("Field required", "/selectors"),
      ]),
    );
    render(
      <DomainWindow
        accepted={acceptedDomain("leo-core", "isis", ["leo", "geo"], null)}
        backend={{ edit, describe: describeDomain }}
      />,
    );

    expect(screen.getByText("Loading the field editor…")).toBeTruthy();
    await act(async () => undefined);
    expect(screen.queryByTestId("builder-domain-editor")).toBeNull();
    const hold = screen.getByLabelText("Hold interval s") as HTMLInputElement;
    expect(hold.value).toBe("6");

    fireEvent.change(hold, { target: { value: "9" } });
    fireEvent.blur(hold);
    await act(async () => undefined);

    expect(edit.mock.calls).toEqual([
      [
        {
          kind: "controls",
          commands: [{ operation: "set_scalar", control_id: "hold_interval_s-6-2-6", value: 9 }],
        },
      ],
    ]);
    expect((screen.getByLabelText("Hold interval s") as HTMLInputElement).value).toBe("9");
    expect(screen.getByText("Field required")).toBeTruthy();
    expect(screen.getByText("/selectors")).toBeTruthy();
    expect(describeDomain).toHaveBeenCalledTimes(1);
  });
});

describe("a window whose domain changes form", () => {
  it("moves to the field editor with the backend's issue when one timer is set, and back when both are", async () => {
    vi.useFakeTimers();
    const edit = vi.fn(async (change: RoutingDomainChange) =>
      change.kind === "routing_domain"
        ? // Only hello is set: the backend writes it alone, states no card
          // value and names the missing hold.
          answer(null, timersTree(APPLIED_REVISION, 5, null), [TIMERS_INCOMPLETE])
        : answer({ ...CARD, hello_interval_s: 5, hold_interval_s: 15 }),
    );
    render(
      <DomainWindow
        accepted={acceptedDomain("leo-core", "isis", ["leo"], CARD)}
        backend={{ edit, describe: NOT_DESCRIBED }}
      />,
    );
    expect(screen.getByText("engine defaults", { selector: ".builder-card-summary" })).toBeTruthy();

    const [hello] = timerInputs();
    fireEvent.change(hello, { target: { value: "5" } });
    // The card shows what was asked while the backend checks it.
    expect(screen.getByText("timers incomplete")).toBeTruthy();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(350);
    });

    expect(edit.mock.calls[0]![0]).toEqual({
      kind: "routing_domain",
      domain: { ...CARD, hello_interval_s: 5 },
    });
    expect(screen.queryByTestId("builder-domain-editor")).toBeNull();
    expect(screen.getByTestId("graphical-control-tree-editor")).toBeTruthy();
    expect(screen.getByText("Routing timers require both hello and hold intervals")).toBeTruthy();
    expect(screen.getByText("/timers/hold_interval_s")).toBeTruthy();
    expect(document.body.textContent).not.toContain("null");

    const hold = screen.getByLabelText("Hold interval s");
    fireEvent.change(hold, { target: { value: "15" } });
    fireEvent.blur(hold);
    await act(async () => undefined);

    expect(edit.mock.calls[1]![0]).toEqual({
      kind: "controls",
      commands: [{ operation: "set_scalar", control_id: "hold_interval_s-6-5-null", value: 15 }],
    });
    expect(screen.getByTestId("builder-domain-editor")).toBeTruthy();
    expect(screen.queryByTestId("graphical-control-tree-editor")).toBeNull();
    expect(screen.getByText("hello 5s · hold 15s")).toBeTruthy();
    expect(screen.queryByText("Routing timers require both hello and hold intervals")).toBeNull();
  });
});
