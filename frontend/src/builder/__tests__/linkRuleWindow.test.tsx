// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** A link rule's window edits the rule as a pending edit. It shows the card
 *  while the backend states a card value for the rule and the field editor
 *  otherwise. Every change goes to the backend, and the window shows what the
 *  latest answer states. */
import { useRef, useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { LinkRuleWindow, type LinkRuleChange } from "../LinkRuleEditor";
import { listSessionObjects } from "../sessionListing";
import type {
  BuilderControlTree,
  BuilderIssue,
  BuilderVisualLinkRule,
  BuilderVisualLinkRuleListing,
  BuilderVisualObjectEditResult,
} from "../generated/builderApi";
import { AUTHORING_FACTS } from "./fixtures/authoringFacts";
import { newWorkspace } from "./fixtures/workspaceFixtures";
import {
  acceptedRule,
  ruleAddress,
  ruleCard,
  sessionListing,
  workingIssue,
} from "./fixtures/listingFixtures";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const APPLIED_REVISION = 4;
const CARD = ruleCard("leo-isl", "leo", "leo");

/** The field editor the backend builds over a rule's working JSON at
 *  `revision`: its max range. Control ids differ per tree, as the backend's do. */
function rangeTree(revision: number, maxRangeKm: number | null): BuilderControlTree {
  return {
    projection_revision: revision,
    root: {
      control_id: `rule-${revision}-${maxRangeKm}`,
      json_pointer: "",
      label: "Link rule",
      required: true,
      present: true,
      model_name: "LinkRule",
      fields: [
        {
          field_name: "max_range_km",
          wire_name: "max_range_km",
          control: {
            control_id: `range-${revision}-${maxRangeKm}`,
            json_pointer: "/max_range_km",
            label: "Max range km",
            required: false,
            present: maxRangeKm !== null,
            scalar_kind: "number",
            number_kind: "float",
            value: maxRangeKm,
          },
        },
      ],
    },
  };
}

/** The backend's answer for the rule: the card value that writes it back (or
 *  none), the field editor over it and its issues. */
function answer(
  card: BuilderVisualLinkRule | null,
  tree: BuilderControlTree = rangeTree(APPLIED_REVISION, card?.max_range_km ?? null),
  issues: ReadonlyArray<BuilderIssue> = [],
): BuilderVisualObjectEditResult {
  return {
    address: ruleAddress("leo-isl"),
    working: { id: "leo-isl", max_range_km: card?.max_range_km ?? null },
    widget: card === null ? null : { kind: "link_rule", rule: card },
    control_tree: tree,
    issues,
  };
}

interface Backend {
  edit: (change: LinkRuleChange) => Promise<BuilderVisualObjectEditResult>;
  describe: () => Promise<BuilderVisualObjectEditResult>;
}

const NOT_DESCRIBED = () => Promise.reject(new Error("the rule was described"));

/** The window as the page shows it: the backend's listing of the accepted rule
 *  with the latest answer for its pending edit laid over it. Like the page, it
 *  holds an answer only when it answers the latest edit of the rule. */
function RuleWindow({
  accepted,
  backend,
  pendingAtOpen = null,
  onRepoint = async () => "rederived",
  onRemove = null,
}: {
  accepted: BuilderVisualLinkRuleListing;
  backend: Backend;
  pendingAtOpen?: BuilderVisualObjectEditResult | null;
  onRepoint?: (side: "a" | "b", segmentId: string) => Promise<string>;
  onRemove?: (() => void) | null;
}) {
  const [pending, setPending] = useState(pendingAtOpen);
  const latest = useRef(0);
  const workspace = newWorkspace("links");
  workspace.space_refs = [
    { segment_id: "leo", source_ref: "nodalarc:constellations/leo.yaml", label: "LEO" },
    { segment_id: "geo", source_ref: "nodalarc:constellations/geo.yaml", label: "GEO" },
  ];
  const [listed] = listSessionObjects(
    sessionListing({ link_rules: [accepted] }),
    pending === null ? [] : [pending],
  ).linkRules;
  return (
    <LinkRuleWindow
      listed={listed!}
      appliedRevision={APPLIED_REVISION}
      onEdit={async (change) => {
        const request = ++latest.current;
        const answered = await backend.edit(change);
        if (request !== latest.current) throw new Error("a later edit of the rule was made");
        setPending(answered);
        return answered;
      }}
      onDescribe={backend.describe}
      workspace={workspace}
      segments={null}
      allocation={null}
      onRepoint={onRepoint}
      onRemove={onRemove}
      authoring={AUTHORING_FACTS}
    />
  );
}

/** Answers the window's edits one at a time, in the order the test settles them. */
function deferredBackend() {
  const waiting: Array<{
    change: LinkRuleChange;
    settle: (answered: BuilderVisualObjectEditResult) => Promise<void>;
    fail: (cause: Error) => Promise<void>;
  }> = [];
  const edit = (change: LinkRuleChange) =>
    new Promise<BuilderVisualObjectEditResult>((resolve, reject) => {
      waiting.push({
        change,
        settle: (answered) => act(async () => resolve(answered)),
        fail: (cause) => act(async () => reject(cause)),
      });
    });
  return { waiting, backend: { edit, describe: NOT_DESCRIBED } };
}

const selected = (label: string) => (screen.getByLabelText(label) as HTMLSelectElement).value;

describe("the card of a rule the backend states a card value for", () => {
  it("sends a pick at once and a typed value after a pause, each as the whole card value", async () => {
    vi.useFakeTimers();
    const edit = vi.fn(async (change: LinkRuleChange) =>
      answer(change.kind === "link_rule" ? change.rule : null),
    );
    render(
      <RuleWindow
        accepted={acceptedRule("leo-isl", ["leo"], CARD)}
        backend={{ edit, describe: NOT_DESCRIBED }}
      />,
    );

    fireEvent.change(screen.getByLabelText("Endpoint A terminal role"), {
      target: { value: "crosslink" },
    });
    expect(edit.mock.calls.map(([change]) => change)).toEqual([
      { kind: "link_rule", rule: { ...CARD, a: { ...CARD.a, role: "crosslink" } } },
    ]);
    await act(async () => undefined);

    const range = screen.getByPlaceholderText("unlimited");
    for (const typed of ["5", "50", "500"]) fireEvent.change(range, { target: { value: typed } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(349);
    });
    expect(edit).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    // One request for the typing, built on the answered pick.
    expect(edit.mock.calls.map(([change]) => change)).toEqual([
      { kind: "link_rule", rule: { ...CARD, a: { ...CARD.a, role: "crosslink" } } },
      {
        kind: "link_rule",
        rule: { ...CARD, a: { ...CARD.a, role: "crosslink" }, max_range_km: 500 },
      },
    ]);
    expect(screen.getByText("Pending edit. It is not applied to the session yet.")).toBeTruthy();
  });

  it("builds a change on one still being checked and shows only the latest answer", async () => {
    const { waiting, backend } = deferredBackend();
    render(<RuleWindow accepted={acceptedRule("leo-isl", ["leo"], CARD)} backend={backend} />);

    fireEvent.change(screen.getByLabelText("Endpoint A terminal role"), {
      target: { value: "crosslink" },
    });
    fireEvent.change(screen.getByLabelText("Endpoint A medium"), { target: { value: "rf" } });
    expect(waiting.map((request) => request.change)).toEqual([
      { kind: "link_rule", rule: { ...CARD, a: { ...CARD.a, role: "crosslink" } } },
      { kind: "link_rule", rule: { ...CARD, a: { ...CARD.a, role: "crosslink", medium: "rf" } } },
    ]);
    // The card shows what was asked while the backend checks it.
    expect(selected("Endpoint A terminal role")).toBe("crosslink");
    expect(screen.getByText("Checking the edit…")).toBeTruthy();

    // The earlier answer is not held, and the card still waits for the later one.
    await waiting[0]!.settle(answer({ ...CARD, max_range_km: 111 }));
    expect(screen.getByText("Checking the edit…")).toBeTruthy();
    expect(selected("Endpoint A medium")).toBe("rf");
    expect((screen.getByPlaceholderText("unlimited") as HTMLInputElement).value).toBe("");

    // The backend states the rule its own way.
    const stated = { ...CARD, a: { ...CARD.a, role: "crosslink" as const, medium: "rf" as const } };
    await waiting[1]!.settle(answer({ ...stated, max_range_km: 222 }));

    expect(screen.queryByText("Checking the edit…")).toBeNull();
    expect(selected("Endpoint A medium")).toBe("rf");
    expect((screen.getByPlaceholderText("unlimited") as HTMLInputElement).value).toBe("222");
  });

  it("shows a refused edit and returns to what the backend last stated", async () => {
    const { waiting, backend } = deferredBackend();
    render(<RuleWindow accepted={acceptedRule("leo-isl", ["leo"], CARD)} backend={backend} />);

    fireEvent.change(screen.getByLabelText("Endpoint A terminal role"), {
      target: { value: "crosslink" },
    });
    await waiting[0]!.fail(new Error("VS-API could not be reached"));

    expect(screen.getByText("VS-API could not be reached")).toBeTruthy();
    expect(selected("Endpoint A terminal role")).toBe("isl");
    expect(screen.queryByText(/Pending edit/)).toBeNull();
  });
});

describe("a segment change and a removal of a rule with an edit", () => {
  it("a segment change goes with the rule's pending edit; a removal waits while it is pending", async () => {
    const onRepoint = vi.fn(async () => "rederived");
    const onRemove = vi.fn();
    const backend = { edit: vi.fn(), describe: NOT_DESCRIBED };
    const accepted = acceptedRule("leo-isl", ["leo"], CARD);
    render(
      <RuleWindow accepted={accepted} backend={backend} onRepoint={onRepoint} onRemove={onRemove} />,
    );
    fireEvent.change(screen.getByLabelText("Endpoint A segment"), { target: { value: "geo" } });
    await act(async () => undefined);
    expect(onRepoint).toHaveBeenCalledWith("a", "geo");
    expect(screen.getByTestId("rederive-note").textContent).toBe("rederived");
    fireEvent.click(screen.getByRole("button", { name: "Remove rule" }));
    expect(onRemove).toHaveBeenCalledTimes(1);
    cleanup();

    onRepoint.mockClear();
    render(
      <RuleWindow
        accepted={accepted}
        backend={backend}
        pendingAtOpen={answer({ ...CARD, max_range_km: 500 })}
        onRepoint={onRepoint}
        onRemove={onRemove}
      />,
    );
    fireEvent.change(screen.getByLabelText("Endpoint A segment"), { target: { value: "geo" } });
    await act(async () => undefined);

    expect(onRepoint).toHaveBeenCalledWith("a", "geo");
    expect(screen.getByTestId("rederive-note").textContent).toBe("rederived");
    expect((screen.getByRole("button", { name: "Remove rule" }) as HTMLButtonElement).disabled).toBe(
      true,
    );
  });

  it("a segment change waits while a change of the rule is checked", async () => {
    const onRepoint = vi.fn(async () => "rederived");
    const { waiting, backend } = deferredBackend();
    render(
      <RuleWindow
        accepted={acceptedRule("leo-isl", ["leo"], CARD)}
        backend={backend}
        onRepoint={onRepoint}
      />,
    );
    fireEvent.change(screen.getByLabelText("Endpoint A terminal role"), {
      target: { value: "crosslink" },
    });
    fireEvent.change(screen.getByLabelText("Endpoint A segment"), { target: { value: "geo" } });
    await act(async () => undefined);

    expect(onRepoint).not.toHaveBeenCalled();
    expect(screen.getByTestId("rederive-note").textContent).toBe(
      "A segment change is sent with this rule's edit. Wait until the edit is checked.",
    );

    await waiting[0]!.settle(answer({ ...CARD, a: { ...CARD.a, role: "crosslink" } }));
    fireEvent.change(screen.getByLabelText("Endpoint A segment"), { target: { value: "geo" } });
    await act(async () => undefined);

    expect(onRepoint).toHaveBeenCalledWith("a", "geo");
  });
});

describe("the field editor of a rule no card writes back", () => {
  it("edits the tree the backend describes and shows the answer's tree and issues", async () => {
    const describeRule = vi.fn(async () => answer(null, rangeTree(APPLIED_REVISION, 900)));
    const edit = vi.fn(async () =>
      answer(null, rangeTree(APPLIED_REVISION, 1200), [
        workingIssue("Field required", "/endpoints/1/terminal"),
      ]),
    );
    render(
      <RuleWindow
        accepted={acceptedRule("leo-isl", ["leo", "geo"], null)}
        backend={{ edit, describe: describeRule }}
      />,
    );

    expect(screen.getByText("Loading the field editor…")).toBeTruthy();
    await act(async () => undefined);
    expect(screen.queryByTestId("builder-link-editor")).toBeNull();
    const range = screen.getByLabelText("Max range km") as HTMLInputElement;
    expect(range.value).toBe("900");

    fireEvent.change(range, { target: { value: "1200" } });
    fireEvent.blur(range);
    await act(async () => undefined);

    expect(edit.mock.calls).toEqual([
      [
        {
          kind: "controls",
          commands: [{ operation: "set_scalar", control_id: "range-4-900", value: 1200 }],
        },
      ],
    ]);
    expect((screen.getByLabelText("Max range km") as HTMLInputElement).value).toBe("1200");
    expect(screen.getByText("Field required")).toBeTruthy();
    expect(screen.getByText("/endpoints/1/terminal")).toBeTruthy();
    // The answer's tree is built at the accepted revision, so it is edited as it is.
    expect(describeRule).toHaveBeenCalledTimes(1);
  });

  it("describes a pending rule again when its tree is from an earlier accepted revision", async () => {
    const describeRule = vi.fn(async () => answer(null, rangeTree(APPLIED_REVISION, 700)));
    render(
      <RuleWindow
        accepted={acceptedRule("leo-isl", ["leo"], null)}
        backend={{ edit: vi.fn(), describe: describeRule }}
        pendingAtOpen={answer(null, rangeTree(APPLIED_REVISION - 1, 700))}
      />,
    );
    await act(async () => undefined);

    expect(describeRule).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("graphical-control-tree-editor")).toBeTruthy();
    expect((screen.getByLabelText("Max range km") as HTMLInputElement).value).toBe("700");
  });

  it("shows a failed description and asks again on retry", async () => {
    const describeRule = vi
      .fn<() => Promise<BuilderVisualObjectEditResult>>()
      .mockRejectedValueOnce(new Error("VS-API could not be reached"))
      .mockResolvedValueOnce(answer(null, rangeTree(APPLIED_REVISION, 900)));
    render(
      <RuleWindow
        accepted={acceptedRule("leo-isl", ["leo"], null)}
        backend={{ edit: vi.fn(), describe: describeRule }}
      />,
    );
    await act(async () => undefined);

    expect(
      screen.getByText(/The field editor could not be loaded: VS-API could not be reached/),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "retry" }));
    await act(async () => undefined);

    expect(describeRule).toHaveBeenCalledTimes(2);
    expect((screen.getByLabelText("Max range km") as HTMLInputElement).value).toBe("900");
  });
});

describe("a window whose rule changes form", () => {
  it("moves to the field editor when no card states the edit, and back when one does", async () => {
    const nearest = { ...CARD, topology_mode: "nearest_n" as const, topology_n: 2 };
    const edit = vi.fn(async (change: LinkRuleChange) =>
      change.kind === "link_rule"
        ? // A nearest-N rule with no N: the grammar refuses it, so no card states it.
          answer(null, rangeTree(APPLIED_REVISION, null), [
            workingIssue("Field required", "/topology/n"),
          ])
        : answer(nearest),
    );
    render(
      <RuleWindow
        accepted={acceptedRule("leo-isl", ["leo"], CARD)}
        backend={{ edit, describe: NOT_DESCRIBED }}
      />,
    );
    expect(screen.getByTestId("builder-link-editor")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "nearest N" }));
    await act(async () => undefined);

    expect(edit.mock.calls[0]![0]).toEqual({
      kind: "link_rule",
      rule: { ...CARD, topology_mode: "nearest_n" },
    });
    expect(screen.queryByTestId("builder-link-editor")).toBeNull();
    expect(screen.getByTestId("graphical-control-tree-editor")).toBeTruthy();
    expect(screen.getByText("Field required")).toBeTruthy();

    const range = screen.getByLabelText("Max range km");
    fireEvent.change(range, { target: { value: "800" } });
    fireEvent.blur(range);
    await act(async () => undefined);

    expect(edit.mock.calls[1]![0]).toEqual({
      kind: "controls",
      commands: [{ operation: "set_scalar", control_id: "range-4-null", value: 800 }],
    });
    expect(screen.getByTestId("builder-link-editor")).toBeTruthy();
    expect(screen.queryByTestId("graphical-control-tree-editor")).toBeNull();
    expect(screen.queryByText("Field required")).toBeNull();
  });
});
