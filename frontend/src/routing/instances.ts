// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** The one frontend reader of a node's routing facts.
 *
 *  Roles, instances, areas, area border routers and AS boundary routers come
 *  from the backend. An area is identified by its protocol, its instance and
 *  its id: IS-IS and OSPF areas are different things, and two instances may
 *  number their areas alike. Nothing here infers a role or an area from
 *  links, planes or peers.
 */

import { tokens } from "../styles/tokens";
import type { NodeRole, NodeRoutingInstance, NodeState, RoutingProtocol } from "../types";

const PROTOCOL_LABELS: Record<RoutingProtocol, string> = {
  isis: "IS-IS",
  ospf: "OSPF",
  static: "Static",
  bgp: "BGP",
};

const ROLE_LABELS: Record<NodeRole, string> = {
  router: "Router",
  host: "Host",
  forwarding_only: "Forwarding only",
};

export function protocolLabel(protocol: RoutingProtocol): string {
  return PROTOCOL_LABELS[protocol];
}

export function roleLabel(role: NodeRole): string {
  return ROLE_LABELS[role];
}

/** An IS-IS or OSPF instance present in a snapshot. */
export interface AreaInstance {
  domainId: string;
  protocol: RoutingProtocol;
}

export function instanceLabel(instance: Pick<AreaInstance, "domainId" | "protocol">): string {
  return `${protocolLabel(instance.protocol)} ${instance.domainId}`;
}

function hasAreas(instance: NodeRoutingInstance): boolean {
  return instance.protocol === "isis" || instance.protocol === "ospf";
}

/** The IS-IS and OSPF instances the nodes participate in, in first-seen order. */
export function areaInstances(nodes: readonly NodeState[]): AreaInstance[] {
  const seen = new Map<string, AreaInstance>();
  for (const node of nodes) {
    for (const instance of node.routing_instances) {
      if (hasAreas(instance) && !seen.has(instance.domain_id)) {
        seen.set(instance.domain_id, { domainId: instance.domain_id, protocol: instance.protocol });
      }
    }
  }
  return [...seen.values()];
}

export function nodeInstance(
  node: Pick<NodeState, "routing_instances">,
  domainId: string,
): NodeRoutingInstance | undefined {
  return node.routing_instances.find((instance) => instance.domain_id === domainId);
}

/** A node's areas in one instance as text, e.g. "49.0001" or "0.0.0.0, 0.0.0.1". */
export function areasText(instance: NodeRoutingInstance): string {
  return instance.areas.join(", ");
}

/** A node's areas and border roles in one instance, e.g. "areas 0.0.0.0, 0.0.0.1 · ABR". */
export function instanceDetail(instance: NodeRoutingInstance): string {
  const parts: string[] = [];
  if (instance.areas.length > 0) {
    parts.push(`area${instance.areas.length > 1 ? "s" : ""} ${areasText(instance)}`);
  }
  if (instance.area_border) parts.push("ABR");
  if (instance.as_boundary) parts.push("ASBR");
  return parts.length > 0 ? parts.join(" · ") : "participant";
}

/** One line per instance: protocol, instance, areas and border roles. */
export function instanceSummary(instance: NodeRoutingInstance): string {
  const label = instanceLabel({ domainId: instance.domain_id, protocol: instance.protocol });
  return `${label} · ${instanceDetail(instance)}`;
}

/** Every instance of a node, one summary each; "no routing instance" when none. */
export function routingSummary(node: Pick<NodeState, "routing_instances">): string {
  if (node.routing_instances.length === 0) return "no routing instance";
  return node.routing_instances.map(instanceSummary).join("; ");
}

/** The instances a node runs on one interface, with the interface's OSPF area. */
export function interfaceInstances(
  node: Pick<NodeState, "routing_instances">,
  interfaceName: string,
): { instance: NodeRoutingInstance; areaId: string | null }[] {
  const result: { instance: NodeRoutingInstance; areaId: string | null }[] = [];
  for (const instance of node.routing_instances) {
    const entry = instance.interfaces.find((item) => item.name === interfaceName);
    if (entry) result.push({ instance, areaId: entry.area_id });
  }
  return result;
}

// The first area colors, then evenly spread hues for further areas.
const BASE_AREA_COLORS = [tokens.areaRed, tokens.areaGreen, tokens.areaBlue, tokens.areaAmber];

function hslToHex(hue: number, saturation: number, lightness: number): number {
  const chroma = (1 - Math.abs(2 * lightness - 1)) * saturation;
  const section = hue / 60;
  const x = chroma * (1 - Math.abs((section % 2) - 1));
  const [r, g, b] =
    section < 1 ? [chroma, x, 0]
    : section < 2 ? [x, chroma, 0]
    : section < 3 ? [0, chroma, x]
    : section < 4 ? [0, x, chroma]
    : section < 5 ? [x, 0, chroma]
    : [chroma, 0, x];
  const m = lightness - chroma / 2;
  const channel = (value: number) => Math.round((value + m) * 255);
  return (channel(r) << 16) | (channel(g) << 8) | channel(b);
}

export function areaColorAt(index: number): number {
  if (index < BASE_AREA_COLORS.length) return BASE_AREA_COLORS[index]!;
  // Golden-angle hue steps keep neighbouring areas apart.
  const hue = ((index - BASE_AREA_COLORS.length) * 137.508 + 20) % 360;
  return hslToHex(hue, 0.5, 0.55);
}

/** How a node is colored when areas of one instance are colored. */
export type AreaMark =
  | { kind: "outside" }
  | { kind: "border" }
  | { kind: "area"; area: string };

export function areaMark(node: Pick<NodeState, "routing_instances">, domainId: string): AreaMark {
  const instance = nodeInstance(node, domainId);
  if (!instance) return { kind: "outside" };
  if (instance.area_border) return { kind: "border" };
  return { kind: "area", area: areasText(instance) };
}

export interface AreaLegendEntry {
  label: string;
  color: number;
}

/** Coloring by the areas of one IS-IS or OSPF instance. */
export interface AreaColoring {
  instances: AreaInstance[];
  /** The instance being colored; null when the snapshot has none. */
  instance: AreaInstance | null;
  colorOf: (node: Pick<NodeState, "routing_instances">) => number;
  /** The legend entry a node falls under; null outside the instance. */
  entryOf: (node: Pick<NodeState, "routing_instances">) => AreaLegendEntry | null;
  legend: AreaLegendEntry[];
}

/** Coloring for the chosen instance, or the first one when none is chosen
 *  or the chosen one is not in this snapshot. */
export function buildAreaColoring(
  nodes: readonly NodeState[],
  chosenDomainId: string | null,
): AreaColoring {
  const instances = areaInstances(nodes);
  const instance =
    instances.find((item) => item.domainId === chosenDomainId) ?? instances[0] ?? null;
  if (instance === null) {
    return {
      instances,
      instance,
      colorOf: () => tokens.areaOutside,
      entryOf: () => null,
      legend: [{ label: "No IS-IS or OSPF instance", color: tokens.areaOutside }],
    };
  }
  const areas = new Set<string>();
  let hasBorder = false;
  let hasOutside = false;
  for (const node of nodes) {
    const mark = areaMark(node, instance.domainId);
    if (mark.kind === "area") areas.add(mark.area);
    else if (mark.kind === "border") hasBorder = true;
    else hasOutside = true;
  }
  const areaEntries = new Map(
    [...areas].sort().map((area, index) => [area, { label: `Area ${area}`, color: areaColorAt(index) }]),
  );
  const borderEntry: AreaLegendEntry = { label: "Area border router", color: tokens.areaBorder };
  const outsideEntry: AreaLegendEntry = { label: "Outside this instance", color: tokens.areaOutside };
  const legend: AreaLegendEntry[] = [...areaEntries.values()];
  if (hasBorder) legend.push(borderEntry);
  if (hasOutside) legend.push(outsideEntry);
  const entryOf = (node: Pick<NodeState, "routing_instances">): AreaLegendEntry | null => {
    const mark = areaMark(node, instance.domainId);
    if (mark.kind === "outside") return null;
    if (mark.kind === "border") return borderEntry;
    const entry = areaEntries.get(mark.area);
    if (entry === undefined) {
      throw new Error(`area ${mark.area} of ${instance.domainId} was not in the coloring`);
    }
    return entry;
  };
  return {
    instances,
    instance,
    colorOf: (node) => (entryOf(node) ?? outsideEntry).color,
    entryOf,
    legend,
  };
}
