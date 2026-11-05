// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { NetworkSummary } from "../NetworkSummary";
import type { NodeState, StateSnapshot } from "../../generated/vsApi";
import type { NodeRole, NodeRoutingInstance } from "../../builder/generated/builderApi";

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
      draining_directions: [],
      contact_losses: [],
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

  it("lists each draining direction with the time to its last arrival", () => {
    const snapshot = {
      nodes: [],
      links: [],
      wall_time: "2027-02-19T00:00:00Z",
      draining_directions: [
        {
          contact_id: "0@2027-02-19T00:00:00.000000Z",
          transmitter_node_id: "mars-relay",
          transmitter_interface: "isl0",
          receiver_node_id: "earth-relay",
          receiver_interface: "isl3",
          stopped_at: "2027-02-18T23:50:00Z",
          last_arrival_at: "2027-02-19T00:08:33.451Z",
        },
      ],
      contact_losses: [],
    } as unknown as StateSnapshot;

    render(<NetworkSummary snapshot={snapshot} />);

    expect(value("mars-relay → earth-relay")).toBe("last arrival in 8.56 min");
  });

  it("labels each end's discarded packets as a count or a sample, with its cause", () => {
    const snapshot = {
      nodes: [],
      links: [],
      draining_directions: [],
      contact_losses: [
        {
          node_a: "earth-relay",
          node_b: "mars-relay",
          contact_id: "0@2027-02-19T00:00:00.000000Z",
          reason: "vis_lost",
          sim_time: "2027-02-19T06:00:00Z",
          wall_time: "2027-02-19T00:20:00Z",
          ends: [
            {
              node_id: "earth-relay",
              interface: "isl3",
              cause: "drain_deadline",
              refused_before_open: 12,
              untransmitted_at_stop: 135,
              admission_taken: 412,
              transmit_dropped: 4,
              queue_dropped: 3,
              discarded_at_close: 0,
              discarded_exact: true,
            },
          ],
        },
        {
          node_a: "sat-1",
          node_b: "sat-2",
          contact_id: null,
          reason: "vis_lost",
          sim_time: "2027-02-19T06:01:00Z",
          wall_time: "2027-02-19T00:21:00Z",
          ends: [
            {
              node_id: "sat-1",
              interface: "isl0",
              cause: "end_of_contact",
              refused_before_open: 0,
              untransmitted_at_stop: 0,
              admission_taken: 0,
              transmit_dropped: 0,
              queue_dropped: 0,
              discarded_at_close: 17,
              discarded_exact: false,
            },
          ],
        },
      ],
    } as unknown as StateSnapshot;

    render(<NetworkSummary snapshot={snapshot} />);

    expect(value("earth-relay/isl3")).toBe(
      "drain deadline: 12 refused before open, 135 untransmitted at contact end, " +
        "412 refused after the stop, 4 dropped at the transmit queue limit, " +
        "3 dropped at the receive queue limit, 0 discarded (count)",
    );
    expect(value("sat-1/isl0")).toBe(
      "contact ended: 17 discarded (sample), 0 dropped at the queue limit",
    );
    // Newest first.
    const pairs = screen.getAllByText(/ ↔ /).map((element) => element.textContent);
    expect(pairs).toEqual(["sat-1 ↔ sat-2", "earth-relay ↔ mars-relay"]);
  });
});
