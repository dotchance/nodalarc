// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** The session anatomy — what a session is made of, what this one has, and
 *  why each part matters (IG-15).
 *
 *  Order-free by construction: every row is always visible and always
 *  actionable, so nothing here imposes a sequence — it answers "what could
 *  I do next, and why would I" for a user who builds in any order. Done
 *  rows dim to counts; pending rows carry the accent and say what the part
 *  is FOR in both users' languages (the network engineer's and the
 *  orbital engineer's). States are structural presence only — the resolve
 *  status is the only green in the builder.
 */

import type { Workspace } from "./workspace";
import { placedSegments } from "./workspace";

interface GuideRow {
  key: string;
  label: string;
  /** Structural presence — never a health claim. */
  done: boolean;
  /** Count/state text when done; what-and-why text when pending. */
  detail: string;
  /** Full-sentence why for the tooltip, both literacies. */
  why: string;
  action: (() => void) | null;
}

interface BuildGuideProps {
  workspace: Workspace;
  saved: string | null;
  deployed: boolean;
  onAddConstellation: () => void;
  onAddGround: () => void;
  onAddDomain: () => void;
  onOpenSession: () => void;
  onOpenSegment: (kind: "segment" | "ground", id: string) => void;
}

export function BuildGuide({
  workspace,
  saved,
  deployed,
  onAddConstellation,
  onAddGround,
  onAddDomain,
  onOpenSession,
  onOpenSegment,
}: BuildGuideProps) {
  const spaceCount = workspace.space.length + workspace.space_refs.length;
  const siteCount =
    workspace.ground.reduce((n, g) => n + g.members.length, 0) +
    workspace.ground_refs.length;
  const placed = placedSegments(workspace);
  const firstPlaced = placed[0] ?? null;
  const named = workspace.name !== "untitled-session";

  const rows: GuideRow[] = [
    {
      key: "space",
      label: "Space segments",
      done: spaceCount > 0,
      detail:
        spaceCount > 0
          ? `${spaceCount} segment${spaceCount === 1 ? "" : "s"} · add more`
          : "none yet — satellites carry the traffic",
      why: "Constellations in orbit are the moving network. Add one and the orbit sliders shape it live.",
      action: onAddConstellation,
    },
    {
      key: "ground",
      label: "Ground sites",
      done: siteCount > 0,
      detail:
        siteCount > 0
          ? `${siteCount} site${siteCount === 1 ? "" : "s"} · add more`
          : "none yet — where traffic enters and exits",
      why: "Surface gateways are where traffic enters and leaves the constellation. Paste sites as name, lat, lon.",
      action: onAddGround,
    },
    {
      key: "links",
      label: "Comms intent",
      done: workspace.links.length > 0,
      detail:
        workspace.links.length > 0
          ? `${workspace.links.length} rule${workspace.links.length === 1 ? "" : "s"}`
          : placed.length >= 2
            ? "none yet — nothing may communicate"
            : "needs two segments first",
      why:
        placed.length >= 2
          ? "Link rules say who MAY talk to whom; role, band, and reach derive from the terminals both sides carry. Use ⇄ link to… on any segment."
          : "Link rules connect two segments, so place at least two before drawing intent.",
      action:
        firstPlaced === null
          ? null
          : () =>
              onOpenSegment(
                firstPlaced.kind === "space" ? "segment" : "ground",
                firstPlaced.segment_id,
              ),
    },
    {
      key: "routing",
      label: "Routing",
      done: workspace.routing_domains.length > 0,
      detail:
        workspace.routing_domains.length > 0
          ? `${workspace.routing_domains.length} domain${workspace.routing_domains.length === 1 ? "" : "s"}`
          : workspace.links.length > 0
            ? "none yet — links carry no routed traffic"
            : "add links first",
      why: "A routing domain runs a real IGP over the links, so paths form and traffic actually routes end to end.",
      action: workspace.links.length > 0 ? onAddDomain : null,
    },
    {
      key: "session",
      label: "Identity & time",
      done: named,
      detail: named ? `${workspace.name}` : "name it — real time unless you say otherwise",
      why: "The session's name, start time, and time rate. One second per second unless you explicitly change it.",
      action: onOpenSession,
    },
    {
      key: "save",
      label: "Save & deploy",
      done: saved !== null,
      detail: deployed
        ? "deployed to the cluster"
        : saved
          ? `saved as ${saved} — deploy when ready`
          : "when it resolves — a session file like any other",
      why: "Save writes a resolvable session file; deploy runs it on the cluster through the same switch every session uses.",
      action: null,
    },
  ];

  return (
    <div className="builder-outline-group" data-testid="builder-guide">
      <div className="builder-outline-kind" title="Build in any order — this is anatomy, not a sequence">
        Session anatomy
      </div>
      {rows.map((row) => {
        const body = (
          <>
            <span className="builder-guide-state" aria-hidden>
              {row.done ? "✓" : "+"}
            </span>
            <span className="builder-guide-label">{row.label}</span>
            <span className="builder-guide-detail">{row.detail}</span>
          </>
        );
        return row.action ? (
          <button
            key={row.key}
            className={`builder-guide-row${row.done ? " builder-guide-row--done" : ""}`}
            title={row.why}
            onClick={row.action}
          >
            {body}
          </button>
        ) : (
          <div
            key={row.key}
            className={`builder-guide-row builder-guide-row--static${row.done ? " builder-guide-row--done" : ""}`}
            title={row.why}
          >
            {body}
          </div>
        );
      })}
    </div>
  );
}
