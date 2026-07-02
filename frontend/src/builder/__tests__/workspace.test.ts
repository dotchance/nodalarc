// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Workspace serializer — the ONE serializer from drafts to the grammar.
 *
 *  Pins: circular and elliptical orbit shapes emit the grammar's OrbitShape
 *  variants; ground placement emits from_site_set with the explicit default
 *  scheduling block; identifier normalization; warn-not-block orbit
 *  findings.
 */

import { describe, expect, it } from "vitest";
import {
  defaultDraftOrbit,
  identifier,
  newDraftConstellation,
  newWorkspace,
  orbitWarnings,
  toSessionDocument,
} from "../workspace";

function draftWorkspace() {
  const workspace = newWorkspace("My Test Session");
  workspace.space.push(newDraftConstellation("nodalarc:nodes/space/starlink-v2-mesh.yaml"));
  return workspace;
}

describe("identifier", () => {
  it("normalizes display strings into grammar identifiers", () => {
    expect(identifier("My Test Session")).toBe("my-test-session");
    // Underscores are grammar (Identifier allows [a-z0-9_-]) and must survive.
    expect(identifier("isl_optical")).toBe("isl_optical");
    expect(identifier("  weird__chars!! ")).toBe("weird__chars");
    expect(identifier("---")).toBe("");
  });
});

describe("toSessionDocument", () => {
  it("emits a circular orbit as the CircularShape variant", () => {
    const workspace = draftWorkspace();
    const doc = toSessionDocument(workspace) as any;
    const constellation = doc.segments[0].source.constellation;
    expect(constellation.orbit.shape).toEqual({ altitude_km: 550 });
    expect(constellation.node).toBe("nodalarc:nodes/space/starlink-v2-mesh.yaml");
    expect(doc.session.name).toBe("my-test-session");
    expect(doc.time.start_time).toBe(workspace.start_time);
  });

  it("emits an elliptical orbit as the PerigeeApogeeShape variant", () => {
    const workspace = draftWorkspace();
    workspace.space[0]!.orbit = {
      ...defaultDraftOrbit(),
      shape_kind: "elliptical",
      perigee_altitude_km: 600,
      apogee_altitude_km: 39700,
      argument_of_perigee_deg: 270,
    };
    const doc = toSessionDocument(workspace) as any;
    const orbit = doc.segments[0].source.constellation.orbit;
    expect(orbit.shape).toEqual({ perigee_altitude_km: 600, apogee_altitude_km: 39700 });
    expect(orbit.orientation.argument_of_perigee_deg).toBe(270);
    expect(orbit.shape.altitude_km).toBeUndefined();
  });

  it("emits ground placement with the explicit default scheduling block", () => {
    const workspace = draftWorkspace();
    workspace.ground_site_set_ref =
      "nodalarc:site-sets/earth/leo/earth-leo-starlink-pop-sites.yaml";
    const doc = toSessionDocument(workspace) as any;
    const ground = doc.segments.find((s: any) => s.id === "ground");
    expect(ground.placement.from_site_set).toBe(workspace.ground_site_set_ref);
    // The default is explicit and visible, never a hidden fallback.
    expect(ground.apply.scheduling.handover_mode).toBe("mbb");
    expect(ground.apply.scheduling.selection_policy).toEqual({ highest_elevation: {} });
  });

  it("omits ground when no site set is chosen", () => {
    const doc = toSessionDocument(draftWorkspace()) as any;
    expect(doc.segments.find((s: any) => s.id === "ground")).toBeUndefined();
  });
});

describe("orbitWarnings", () => {
  it("warns on sub-surface and atmospheric orbits without blocking", () => {
    const orbit = defaultDraftOrbit();
    expect(orbitWarnings(orbit)).toEqual([]);
    expect(orbitWarnings({ ...orbit, altitude_km: -10 })).toEqual([
      "orbit is below the surface",
    ]);
    expect(orbitWarnings({ ...orbit, altitude_km: 120 })).toEqual([
      "inside the upper atmosphere — rapid decay",
    ]);
  });

  it("flags elliptical perigee findings and swapped apsides", () => {
    const orbit = {
      ...defaultDraftOrbit(),
      shape_kind: "elliptical" as const,
      perigee_altitude_km: 100,
      apogee_altitude_km: 50,
    };
    const warnings = orbitWarnings(orbit);
    expect(warnings).toContain("perigee inside the upper atmosphere — rapid decay");
    expect(warnings).toContain("apogee is below perigee — swap them");
  });
});

describe("node drafts", () => {
  it("serializes a node draft inline, overriding the reference", async () => {
    const { draftNodeFromDocument, nodeObjectFromDraft } = await import("../workspace");
    const workspace = draftWorkspace();
    const draft = draftNodeFromDocument({
      node: {
        id: "starlink-v2-mesh",
        display_name: "Starlink v2 routed spacecraft",
        forwarding: "routed",
        ethernet: [],
        terminals: [
          { id: "access_ka", role: "access", terminal: "nodalarc:terminals/rf/x.yaml", count: 1 },
          { id: "isl_optical", role: "isl", terminal: "nodalarc:terminals/optical/y.yaml", count: 4 },
        ],
        payloads: [],
      },
    });
    expect(draft.terminals).toHaveLength(2);
    workspace.space[0]!.node_draft = { ...draft, ethernet: ["terr0"] };
    const doc = toSessionDocument(workspace) as any;
    const node = doc.segments[0].source.constellation.node;
    expect(typeof node).toBe("object");
    expect(node.ethernet).toEqual([{ id: "terr0" }]);
    expect(node.terminals[1]).toEqual({
      id: "isl_optical",
      role: "isl",
      terminal: "nodalarc:terminals/optical/y.yaml",
      count: 4,
    });
    // Round-trip: the emitted object forks back into an equal draft.
    const roundTrip = draftNodeFromDocument({ node: nodeObjectFromDraft(workspace.space[0]!.node_draft!) });
    expect(roundTrip.terminals).toEqual(workspace.space[0]!.node_draft!.terminals);
    expect(roundTrip.ethernet).toEqual(["terr0"]);
  });

  it("refuses fork of grammar the editor cannot represent yet", async () => {
    const { draftNodeFromDocument } = await import("../workspace");
    expect(() =>
      draftNodeFromDocument({
        node: { id: "p", payloads: [{ id: "pl", payload: "x", count: 1 }] },
      }),
    ).toThrow(/payload/);
    expect(() =>
      draftNodeFromDocument({
        node: {
          id: "inline-terminal",
          terminals: [{ id: "t", role: "access", terminal: { id: "inline" }, count: 1 }],
        },
      }),
    ).toThrow(/inline-terminal editing/);
  });
});

describe("terminal drafts", () => {
  it("round-trips rf physics through the grammar object", async () => {
    const { defaultDraftTerminal, terminalObjectFromDraft, draftTerminalFromDocument } =
      await import("../workspace");
    const draft = { ...defaultDraftTerminal(), frequency_ghz: 29.5, band: "Ka" };
    const object = terminalObjectFromDraft(draft) as any;
    expect(object.signal).toEqual({ band: "ka", frequency_hz: 29.5e9 });
    expect(object.limits.elevation_deg).toEqual({ min: 20, max: 90 });
    const back = draftTerminalFromDocument({ terminal: object });
    expect(back.frequency_ghz).toBeCloseTo(29.5);
    expect(back.medium).toBe("rf");
    expect(back.transmit_mbps).toBe(500);
  });

  it("serializes optical signal without rf fields", async () => {
    const { defaultDraftTerminal, terminalObjectFromDraft } = await import("../workspace");
    const draft = { ...defaultDraftTerminal(), medium: "optical" as const, wavelength_nm: 1550 };
    const object = terminalObjectFromDraft(draft) as any;
    expect(object.signal).toEqual({ wavelength_nm: 1550 });
    expect(object.medium).toBe("optical");
  });

  it("warns on inverted limit ranges without blocking", async () => {
    const { defaultDraftTerminal, terminalWarnings } = await import("../workspace");
    const draft = {
      ...defaultDraftTerminal(),
      elevation_min_deg: 80,
      elevation_max_deg: 20,
    };
    expect(terminalWarnings(draft)).toContain("elevation min is above max — swap them");
    expect(terminalWarnings(defaultDraftTerminal())).toEqual([]);
  });
});
