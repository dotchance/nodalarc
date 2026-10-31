// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Session sections, completion counts, and available editing actions.
 *  Rows remain visible regardless of authoring order. Every count and claim
 *  comes from the backend's last compile of the draft: declared counts from
 *  its canonical document, resolved facts from its world. */

import type { ReactNode } from "react";
import { distinctGroundStationSites } from "./builderSnapshot";
import type {
  BuilderCompileResult,
  BuilderWorldNode,
  JsonValue,
  NodeRoutingInstance,
} from "./generated/builderApi";

/** The resolved node facts the overview reads. */
export type GuideWorldNode = Pick<BuilderWorldNode, "kind" | "namespace"> & {
  readonly routing_instances: ReadonlyArray<Pick<NodeRoutingInstance, "domain_id" | "protocol">>;
};

interface GuideRow {
  key: string;
  label: string;
  /** Structural presence — never a health claim. */
  done: boolean;
  /** Count/state text when done; what-and-why text when pending. */
  detail: ReactNode;
  /** Full-sentence why for the tooltip, both literacies. */
  why: string;
  action: (() => void) | null;
}

interface BuildGuideProps {
  /** The backend's last compile of the draft (the parts the overview
   *  reads); null before one has answered. */
  compiled: {
    readonly canonical_session_json?: BuilderCompileResult["canonical_session_json"];
    readonly resolved_preview?: { readonly nodes: ReadonlyArray<GuideWorldNode> } | null;
  } | null;
  /** The refusal the last compile or apply answered, if any. */
  refusal: string | null;
  /** Whether the draft holds an accepted session. A new draft holds none
   *  until its first segment is accepted; until then, and before the backend
   *  has answered anything, the overview says what to add first. */
  hasAcceptedSession: boolean;
  /** The generated name the backend gave a new draft, while the draft still
   *  carries it; null once the session has a name the user chose. */
  placeholderName: string | null;
  /** The saved session, with the deploy gate's reason when it refuses. */
  saved: { name: string; deployRefusal: string | null } | null;
  onAddConstellation: () => void;
  onAddGround: () => void;
  onAddDomain: () => void;
  onOpenSession: () => void;
  onOpenSegment: (id: string) => void;
}

interface CanonicalSegment {
  id: string;
  ground: boolean;
}

interface CanonicalFacts {
  name: string;
  segments: CanonicalSegment[];
  linkRuleCount: number;
  routingDeclared: boolean;
}

interface ResolvedDomain {
  domainId: string;
  protocol: string;
  participants: number;
}

function isJsonList(value: JsonValue | undefined): value is ReadonlyArray<JsonValue> {
  return Array.isArray(value);
}

function jsonObject(value: JsonValue | undefined, what: string): Readonly<Record<string, JsonValue>> {
  if (value === undefined || value === null || typeof value !== "object" || isJsonList(value)) {
    throw new Error(`canonical session ${what} is not an object`);
  }
  return value;
}

function jsonArray(value: JsonValue | undefined, what: string): ReadonlyArray<JsonValue> {
  if (!isJsonList(value)) throw new Error(`canonical session ${what} is not a list`);
  return value;
}

function jsonString(value: JsonValue | undefined, what: string): string {
  if (typeof value !== "string") throw new Error(`canonical session ${what} is not a string`);
  return value;
}

/** Read the declared facts the overview counts from the canonical session the
 *  backend accepted. The canonical document omits a field the session does not
 *  declare, so an absent `link_rules` or `routing` means none is declared. */
function canonicalFacts(document: Readonly<Record<string, JsonValue>>): CanonicalFacts {
  const segments = jsonArray(document.segments, "segments").map((value, index) => {
    const segment = jsonObject(value, `segment ${index}`);
    return { id: jsonString(segment.id, `segment ${index} id`), ground: "placement" in segment };
  });
  return {
    name: jsonString(jsonObject(document.session, "session").name, "session name"),
    segments,
    linkRuleCount:
      document.link_rules === undefined ? 0 : jsonArray(document.link_rules, "link_rules").length,
    routingDeclared: document.routing !== undefined,
  };
}

/** Group the resolved world's routing instances by domain. */
export function resolvedDomains(nodes: ReadonlyArray<GuideWorldNode>): ResolvedDomain[] {
  const domains = new Map<string, ResolvedDomain>();
  for (const node of nodes) {
    for (const instance of node.routing_instances) {
      const domain = domains.get(instance.domain_id);
      if (domain) {
        domain.participants += 1;
      } else {
        domains.set(instance.domain_id, {
          domainId: instance.domain_id,
          protocol: instance.protocol,
          participants: 1,
        });
      }
    }
  }
  return [...domains.values()].sort((a, b) => a.domainId.localeCompare(b.domainId));
}

function plural(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? "" : "s"}`;
}

export function BuildGuide({
  compiled,
  refusal,
  hasAcceptedSession,
  placeholderName,
  saved,
  onAddConstellation,
  onAddGround,
  onAddDomain,
  onOpenSession,
  onOpenSegment,
}: BuildGuideProps) {
  const canonicalDocument = compiled?.canonical_session_json ?? null;
  const declared = canonicalDocument === null ? null : canonicalFacts(canonicalDocument);
  const world = compiled?.resolved_preview ?? null;
  // A row whose fact is not stated says why: a new draft the backend has not
  // answered for holds nothing yet, so its rows say what to add first;
  // otherwise the backend has not answered yet, or it refused.
  const empty = compiled === null && refusal === null && !hasAcceptedSession;
  const unstated = refusal ? `not resolved — ${refusal}` : "not checked yet";
  const pending = (whenEmpty: string) => (empty ? whenEmpty : unstated);

  const spaceCount =
    declared === null ? null : declared.segments.filter((segment) => !segment.ground).length;
  const ruleCount = declared === null ? null : declared.linkRuleCount;
  const firstSegment = declared === null ? null : (declared.segments[0] ?? null);
  const siteCount = world === null ? null : distinctGroundStationSites(world.nodes);
  const domains = world === null ? null : resolvedDomains(world.nodes);
  const named = declared !== null && declared.name !== placeholderName;

  const routingDetail = (): ReactNode => {
    if (declared === null) return pending("add links first");
    const omitted = declared.routingDeclared ? "" : " · routing omitted";
    if (domains === null) return `${unstated}${omitted}`;
    if (domains.length === 0) return `none${omitted}, and no node runs a routing workload`;
    return (
      <>
        {`${plural(domains.length, "domain")}: `}
        {domains.map((domain, index) => (
          <span key={domain.domainId}>
            {index > 0 ? "; " : ""}
            <span className="builder-guide-domain">
              {`${domain.domainId} · ${domain.protocol} · ${plural(domain.participants, "node")}`}
            </span>
          </span>
        ))}
        {omitted && `${omitted}, the resolver's default`}
      </>
    );
  };

  const rows: GuideRow[] = [
    {
      key: "space",
      label: "Space segments",
      done: spaceCount !== null && spaceCount > 0,
      detail:
        spaceCount === null
          ? pending("none yet — satellites carry the traffic")
          : spaceCount > 0
            ? `${plural(spaceCount, "segment")} · add more`
            : "none yet — satellites carry the traffic",
      why: "Constellations in orbit are the moving network. Add one and the orbit sliders shape it live.",
      action: onAddConstellation,
    },
    {
      key: "ground",
      label: "Ground sites",
      done: siteCount !== null && siteCount > 0,
      detail:
        siteCount === null
          ? pending("none yet — where traffic enters and exits")
          : siteCount > 0
            ? `${plural(siteCount, "site")} · add more`
            : "none yet — where traffic enters and exits",
      why: "Surface gateways are where traffic enters and leaves the constellation. Paste sites as name, lat, lon.",
      action: onAddGround,
    },
    {
      key: "links",
      label: "Comms intent",
      done: ruleCount !== null && ruleCount > 0,
      detail:
        ruleCount === null
          ? pending("add a segment first")
          : ruleCount > 0
            ? plural(ruleCount, "rule")
            : "none yet — nothing may communicate",
      why: "Link rules say who may talk to whom, within a segment or between two; role, band, and reach derive from the terminals both sides carry. Use + link to… on any segment.",
      action:
        firstSegment === null
          ? null
          : () => onOpenSegment(firstSegment.id),
    },
    {
      key: "routing",
      label: "Routing",
      done: domains !== null && domains.length > 0,
      detail: routingDetail(),
      why: "A routing domain names the nodes that run one routing protocol. With routing omitted the resolver runs IS-IS over every router, and a session with no router runs no routing.",
      action: ruleCount !== null && ruleCount > 0 ? onAddDomain : null,
    },
    {
      key: "session",
      label: "Identity & time",
      done: named,
      detail:
        declared === null
          ? pending("name it — real time unless you say otherwise")
          : named
            ? declared.name
            : "name it — real time unless you say otherwise",
      why: "The session's name, start time, and time rate. One second per second unless you explicitly change it.",
      action: onOpenSession,
    },
    {
      key: "save",
      label: "Save & deploy",
      done: saved !== null,
      detail: saved
        ? saved.deployRefusal
          ? `saved as ${saved.name} — ${saved.deployRefusal}`
          : `saved as ${saved.name}`
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
