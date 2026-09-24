// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { NetworkSummary } from "../NetworkSummary";
import type { NodeRole, NodeRoutingInstance, NodeState, StateSnapshot } from "../../types";

function node(
  id: string,
  nodeType: string,
  role: NodeRole,
  instances: NodeRoutingInstance[],
): NodeState {
  return {
    node_id: id,
    node_type: nodeType,
    role,
    routing_instances: instances,
  } as unknown as NodeState;
}

function isis(domainId: string, area: string, extra: Partial<NodeRoutingInstance> = {}) {
  return {
    domain_id: domainId,
    protocol: "isis",
    areas: [area],
    interfaces: [],
    area_border: false,
    as_boundary: false,
    ...extra,
  } satisfies NodeRoutingInstance;
}

function value(label: string): string | null {
  return screen.getByText(label).nextElementSibling?.textContent ?? null;
}

describe("NetworkSummary", () => {
  afterEach(cleanup);

  it("counts nodes by kind and by the backend's roles", () => {
    const snapshot = {
      nodes: [
        node("sat-1", "satellite", "router", [isis("earth", "49.0001", { area_border: true })]),
        node("sat-2", "satellite", "router", [isis("earth", "49.0002")]),
        node("gw-1", "ground_station", "router", [isis("earth", "49.0002", { as_boundary: true })]),
        node("probe", "ground_station", "forwarding_only", []),
        node("host", "ground_station", "host", []),
      ],
      links: [],
      active_flows: [],
    } as unknown as StateSnapshot;

    render(<NetworkSummary snapshot={snapshot} />);

    expect(value("Satellites")).toBe("2");
    expect(value("Ground Stations")).toBe("3");
    expect(value("Routers")).toBe("3");
    expect(value("Hosts")).toBe("1");
    expect(value("Forwarding only")).toBe("1");
    expect(value("Area Border Routers")).toBe("1");
    expect(value("AS Boundary Routers")).toBe("1");
    expect(value("IS-IS earth")).toBe("3 participants");
    expect(value("Area 49.0002")).toBe("2 nodes");
  });
});
