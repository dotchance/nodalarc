// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
import { describe, expect, it } from "vitest";
import { tokens } from "../../styles/tokens";
import type { NodeRoutingInstance, NodeState } from "../../types";
import {
  areaColorAt,
  areaInstances,
  buildAreaColoring,
  instanceSummary,
  interfaceInstances,
  routingSummary,
} from "../instances";

function instance(
  domainId: string,
  protocol: NodeRoutingInstance["protocol"],
  areas: string[],
  extra: Partial<NodeRoutingInstance> = {},
): NodeRoutingInstance {
  return {
    domain_id: domainId,
    protocol,
    areas,
    interfaces: [],
    area_border: false,
    as_boundary: false,
    ...extra,
  };
}

function node(id: string, instances: NodeRoutingInstance[]): NodeState {
  return { node_id: id, routing_instances: instances } as unknown as NodeState;
}

const EARTH = instance("earth_domain", "isis", ["49.0001"]);
const LUNA = instance("luna_domain", "isis", ["49.0001"]);

describe("area instances", () => {
  it("lists every IS-IS and OSPF instance once, in first-seen order, and no static one", () => {
    const nodes = [
      node("a", [EARTH, instance("lab", "static", [])]),
      node("b", [instance("terrestrial", "ospf", ["0.0.0.0"])]),
      node("c", [EARTH]),
    ];

    expect(areaInstances(nodes)).toEqual([
      { domainId: "earth_domain", protocol: "isis" },
      { domainId: "terrestrial", protocol: "ospf" },
    ]);
  });
});

describe("area coloring", () => {
  it("colors one instance at a time, so two instances' 49.0001 never merge", () => {
    const nodes = [node("earth-router", [EARTH]), node("luna-router", [LUNA])];

    const earth = buildAreaColoring(nodes, "earth_domain");
    const luna = buildAreaColoring(nodes, "luna_domain");

    expect(earth.colorOf(nodes[0]!)).toBe(areaColorAt(0));
    expect(earth.colorOf(nodes[1]!)).toBe(tokens.areaOutside);
    expect(luna.colorOf(nodes[1]!)).toBe(areaColorAt(0));
    expect(luna.colorOf(nodes[0]!)).toBe(tokens.areaOutside);
    expect(earth.legend.map((entry) => entry.label)).toEqual([
      "Area 49.0001",
      "Outside this instance",
    ]);
  });

  it("gives area border routers their own color and legend entry", () => {
    const abr = node("abr", [
      instance("core", "ospf", ["0.0.0.0", "0.0.0.1"], { area_border: true }),
    ]);
    const backbone = node("backbone", [instance("core", "ospf", ["0.0.0.0"])]);
    const edge = node("edge", [instance("core", "ospf", ["0.0.0.1"])]);

    const coloring = buildAreaColoring([abr, backbone, edge], "core");

    expect(coloring.colorOf(abr)).toBe(tokens.areaBorder);
    expect(coloring.colorOf(backbone)).toBe(areaColorAt(0));
    expect(coloring.colorOf(edge)).toBe(areaColorAt(1));
    expect(coloring.entryOf(abr)?.label).toBe("Area border router");
    expect(coloring.legend.map((entry) => entry.label)).toEqual([
      "Area 0.0.0.0",
      "Area 0.0.0.1",
      "Area border router",
    ]);
  });

  it("colors the first instance until one is chosen, and when the chosen one is gone", () => {
    const nodes = [node("a", [EARTH, instance("terrestrial", "ospf", ["0.0.0.0"])])];

    expect(buildAreaColoring(nodes, null).instance?.domainId).toBe("earth_domain");
    expect(buildAreaColoring(nodes, "terrestrial").instance?.domainId).toBe("terrestrial");
    expect(buildAreaColoring(nodes, "retired").instance?.domainId).toBe("earth_domain");
  });

  it("says so when no node runs IS-IS or OSPF", () => {
    const coloring = buildAreaColoring([node("host", [])], null);

    expect(coloring.instance).toBeNull();
    expect(coloring.legend).toEqual([
      { label: "No IS-IS or OSPF instance", color: tokens.areaOutside },
    ]);
  });

  it("keeps generating distinct colors past the base palette", () => {
    const colors = Array.from({ length: 12 }, (_, index) => areaColorAt(index));

    expect(new Set(colors).size).toBe(12);
  });
});

describe("routing text", () => {
  it("names the protocol, instance, areas and border roles of each instance", () => {
    const abr = instance("core", "ospf", ["0.0.0.0", "0.0.0.1"], {
      area_border: true,
      as_boundary: true,
    });

    expect(instanceSummary(EARTH)).toBe("IS-IS earth_domain · area 49.0001");
    expect(instanceSummary(abr)).toBe("OSPF core · areas 0.0.0.0, 0.0.0.1 · ABR · ASBR");
    expect(instanceSummary(instance("lab", "static", []))).toBe("Static lab · participant");
    expect(routingSummary(node("host", []))).toBe("no routing instance");
  });

  it("finds the instances an interface runs, with its OSPF area", () => {
    const router = node("r", [
      instance("orbital", "isis", ["49.0001"], { interfaces: [{ name: "term0", area_id: null }] }),
      instance("terrestrial", "ospf", ["0.0.0.0"], {
        interfaces: [{ name: "terr0", area_id: "0.0.0.0" }],
      }),
    ]);

    expect(
      interfaceInstances(router, "terr0").map(({ instance: item, areaId }) => [
        item.domain_id,
        areaId,
      ]),
    ).toEqual([["terrestrial", "0.0.0.0"]]);
    expect(interfaceInstances(router, "isl9")).toEqual([]);
  });
});
