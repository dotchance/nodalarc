// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Workspace serializer — the one serializer from drafts to the grammar.
 *
 *  Pins: circular and elliptical orbit shapes emit the grammar's OrbitShape
 *  variants; ground placement emits from_site_set with the explicit default
 *  scheduling block; identifier normalization; warn-not-block orbit
 *  findings.
 */

import { describe, expect, it } from "vitest";
import {
  completenessFindings,
  defaultBoundary,
  bandForFrequencyGhz,
  defaultDraftOrbit,
  dwellLongitudeDeg,
  isGeosynchronous,
  meanAnomalyForDwell,
  defaultLinkRule,
  defaultRoutingDomain,
  draftGroundSetFromDocuments,
  draftSiteFromDocument,
  siteObjectFromDraft,
  groundWarnings,
  identifier,
  mintSiteMembers,
  newDraftConstellation,
  newDraftGroundSet,
  newRefGroundSet,
  linkWarnings,
  newWorkspace,
  routingWarnings,
  orbitWarnings,
  parseSiteLines,
  placedSegments,
  refGroundMember,
  reseedCounters,
  stampLanPrefix,
  stampLoopbackAddress,
  stampTerr0Address,
  toSessionDocument,
  usesNonEarthBodies,
  workspaceFromSessionDocument,
  draftGroundMember,
  newDraftSiteObject,
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

  it("emits placed site-set refs with the chosen preset's full explicit block", () => {
    const workspace = draftWorkspace();
    const placed = newRefGroundSet(
      "nodalarc:site-sets/earth/leo/earth-leo-starlink-pop-sites.yaml",
      "Starlink PoPs",
    );
    placed.scheduling_preset = "geo-longest-pass";
    workspace.ground_refs.push(placed);
    const doc = toSessionDocument(workspace) as any;
    const ground = doc.segments.find((s: any) => s.placement);
    expect(ground.placement.from_site_set).toBe(placed.ref);
    // The intent preset writes the full block — explicit, never a hidden fallback.
    expect(ground.apply.scheduling.handover_mode).toBe("bbm");
    expect(ground.apply.scheduling.selection_policy).toEqual({
      longest_remaining_pass: { lookahead_horizon_ticks: 600 },
    });
  });

  it("omits ground when nothing is placed", () => {
    const doc = toSessionDocument(draftWorkspace()) as any;
    expect(doc.segments.find((s: any) => s.placement)).toBeUndefined();
  });

  it("emits an authored ground draft as an inline site_set of defined sites", () => {
    const workspace = draftWorkspace();
    const draft = newDraftGroundSet("nodalarc:nodes/ground/leo-gateway.yaml", {
      access_ka: 2,
    });
    const { rows, errors } = parseSiteLines("Denver, 39.7, -104.9\nPerth, -31.9, 115.8, 20");
    expect(errors).toEqual([]);
    draft.members = mintSiteMembers(draft, rows);
    draft.members.push(
      refGroundMember(
        "nodalarc:sites/earth/sj/earth-sj-svalbard.yaml",
        "earth-sj-svalbard",
        "Svalbard",
        null,
      ),
    );
    draft.originated_ipv4 = ["198.51.100.0/24"];
    draft.tags = ["teleport"];
    workspace.ground.push(draft);
    const doc = toSessionDocument(workspace) as any;
    const ground = doc.segments.find((s: any) => s.placement);
    const siteSet = ground.placement.from_site_set.site_set;
    expect(siteSet.sites).toHaveLength(3);
    const denver = siteSet.sites[0].site;
    // Minted sites carry stamp-derived, explicit addressing they now own.
    expect(denver.lan.ipv4).toBe(stampLanPrefix(draft.stamp, 0));
    expect(denver.nodes[0].interfaces.terr0.ipv4).toBe(stampTerr0Address(draft.stamp, 0));
    expect(denver.nodes[0].interfaces.lo0.ipv4).toBe(stampLoopbackAddress(draft.stamp, 0));
    expect(denver.nodes[0].model).toBe("nodalarc:nodes/ground/leo-gateway.yaml");
    expect(denver.nodes[0].terminals).toEqual({ access_ka: { installed_count: 2 } });
    expect(denver.frame).toEqual({ body_fixed: { body: "nodalarc:bodies/earth.yaml" } });
    expect(denver.location).toEqual({ lat_deg: 39.7, lon_deg: -104.9, alt_m: 0 });
    expect(siteSet.sites[1].site.location.alt_m).toBe(20);
    // Referenced members serialize as references — full fidelity by ref.
    expect(siteSet.sites[2]).toBe("nodalarc:sites/earth/sj/earth-sj-svalbard.yaml");
    expect(ground.apply.originated_prefixes).toEqual({ ipv4: ["198.51.100.0/24"] });
    expect(ground.apply.tags).toEqual(["teleport"]);
    expect(ground.overrides).toBeUndefined();
  });

  it("stores per-site scheduling as sparse overrides — template stays implicit", () => {
    const workspace = draftWorkspace();
    const draft = newDraftGroundSet("nodalarc:nodes/ground/leo-gateway.yaml", {});
    draft.members = mintSiteMembers(
      draft,
      parseSiteLines("Denver, 39.7, -104.9\nPerth, -31.9, 115.8").rows,
    );
    draft.members[1]!.scheduling_override = "geo-longest-pass";
    workspace.ground.push(draft);
    const doc = toSessionDocument(workspace) as any;
    const ground = doc.segments.find((s: any) => s.placement);
    // Only the exception is stored; Denver rides the segment template.
    expect(ground.overrides).toHaveLength(1);
    expect(ground.overrides[0].match).toEqual({ site: "perth" });
    expect(ground.overrides[0].scheduling.handover_mode).toBe("bbm");
  });
});

describe("link rules", () => {
  function linkedWorkspace() {
    const workspace = draftWorkspace(); // one space draft
    const ground = newDraftGroundSet("nodalarc:nodes/ground/leo-gateway.yaml", {});
    ground.members = mintSiteMembers(ground, parseSiteLines("Denver, 39.7, -104.9").rows);
    workspace.ground.push(ground);
    return workspace;
  }

  it("defaults: same space segment twice = ISL fabric (nearest-2 optical)", () => {
    const workspace = linkedWorkspace();
    const [space] = placedSegments(workspace).filter((s) => s.kind === "space");
    const rule = defaultLinkRule(space!, space!);
    expect(rule.a.role).toBe("isl");
    expect(rule.a.medium).toBe("optical");
    expect(rule.topology_mode).toBe("nearest_n");
    expect(rule.topology_n).toBe(2);
  });

  it("defaults: ground to space = RF access, 25 deg mask on the ground side", () => {
    const workspace = linkedWorkspace();
    const placed = placedSegments(workspace);
    const space = placed.find((s) => s.kind === "space")!;
    const ground = placed.find((s) => s.kind === "ground")!;
    // Order-independent: ground lands first either way.
    const rule = defaultLinkRule(space, ground);
    expect(rule.a.segment_id).toBe(ground.segment_id);
    expect(rule.a.role).toBe("access");
    expect(rule.a.medium).toBe("rf");
    expect(rule.a.min_elevation_deg).toBe(25);
    expect(rule.b.min_elevation_deg).toBeNull();
    expect(rule.topology_mode).toBe("visible_candidates");
  });

  it("serializes to the grammar: tag-scoped selects, terminal all-blocks, masks", () => {
    const workspace = linkedWorkspace();
    const placed = placedSegments(workspace);
    const space = placed.find((s) => s.kind === "space")!;
    const ground = placed.find((s) => s.kind === "ground")!;
    const rule = defaultLinkRule(ground, space);
    rule.label = "Teleports to LEO";
    rule.a.tag = "teleport";
    rule.max_range_km = 3000;
    workspace.links.push(rule);
    const doc = toSessionDocument(workspace) as any;
    expect(doc.link_rules).toHaveLength(1);
    const emitted = doc.link_rules[0];
    expect(emitted.id).toBe("teleports-to-leo");
    expect(emitted.enabled).toBeUndefined(); // enabled=true is the grammar default
    expect(emitted.endpoints[0].select).toEqual({
      all: [{ segment: identifier(ground.segment_id) }, { tag: "teleport" }],
    });
    expect(emitted.endpoints[0].terminal).toEqual({
      all: [{ role: "access" }, { medium: "rf" }],
    });
    expect(emitted.endpoints[0].min_elevation_deg).toBe(25);
    expect(emitted.endpoints[1].select).toEqual({ segment: identifier(space.segment_id) });
    expect(emitted.constraints).toEqual({ max_range_km: 3000 });
    expect(emitted.topology).toEqual({ mode: "visible_candidates" });
  });

  it("serializes an ISL fabric with nearest_n and a disabled rule explicitly", () => {
    const workspace = linkedWorkspace();
    const [space] = placedSegments(workspace).filter((s) => s.kind === "space");
    const rule = defaultLinkRule(space!, space!);
    rule.label = "LEO mesh";
    rule.enabled = false;
    rule.topology_n = 4;
    workspace.links.push(rule);
    const doc = toSessionDocument(workspace) as any;
    expect(doc.link_rules[0].topology).toEqual({ mode: "nearest_n", n: 4 });
    expect(doc.link_rules[0].enabled).toBe(false);
  });

  it("uniquifies seeded names even when identifier() truncation collides", () => {
    const workspace = linkedWorkspace();
    const placed = placedSegments(workspace);
    const space = placed.find((s) => s.kind === "space")!;
    const ground = placed.find((s) => s.kind === "ground")!;
    // Force a label whose 48-char id would swallow any suffix.
    const long = { ...ground, label: "An Extremely Long Ground Segment Name That Truncates" };
    const one = defaultLinkRule(long, space);
    const two = defaultLinkRule(long, space, [one]);
    const three = defaultLinkRule(long, space, [one, two]);
    const ids = new Set([one, two, three].map((r) => identifier(r.label)));
    expect(ids.size).toBe(3);
  });

  it("warns on duplicate names, removed segments, and ground-to-ground", () => {
    const workspace = linkedWorkspace();
    const placed = placedSegments(workspace);
    const space = placed.find((s) => s.kind === "space")!;
    const ground = placed.find((s) => s.kind === "ground")!;
    const one = defaultLinkRule(ground, space);
    const two = defaultLinkRule(ground, space);
    two.label = one.label; // duplicate name
    const gg = defaultLinkRule(ground, ground);
    const stale = defaultLinkRule(space, ground);
    stale.b = { ...stale.b, segment_id: "gone-1" };
    workspace.links.push(one, two, gg, stale);
    const warnings = linkWarnings(workspace);
    expect(warnings.some((w) => w.includes("two link rules named"))).toBe(true);
    expect(warnings.some((w) => w.includes("ground-to-ground"))).toBe(true);
    expect(warnings.some((w) => w.includes('"gone-1" is no longer'))).toBe(true);
  });
});

describe("session plumbing + completeness", () => {
  it("serializes the workspace's own time rate — never a hidden constant", () => {
    const workspace = draftWorkspace();
    workspace.step_seconds = 5;
    workspace.compression = 10;
    const doc = toSessionDocument(workspace) as any;
    expect(doc.time).toEqual({
      start_time: workspace.start_time,
      step_seconds: 5,
      compression: 10,
    });
  });

  it("reports object-level gaps with owning-editor targets; session structure is the guide's", () => {
    const workspace = newWorkspace("gaps");
    const ground = newDraftGroundSet("nodalarc:nodes/ground/leo-gateway.yaml", {});
    workspace.ground.push(ground); // no members
    const findings = completenessFindings(workspace);
    // Session-level structure (no space segment / no rules / no domains)
    // belongs to the always-visible session-anatomy guide, not this rail.
    expect(findings.some((f) => f.message.includes("no space segment"))).toBe(false);
    const siteless = findings.find((f) => f.message.includes("no sites yet"));
    expect(siteless?.target).toEqual({ kind: "ground", id: ground.segment_id });
    // A healthy loaded world says nothing — green comes from the resolver.
    expect(completenessFindings(draftWorkspace())).toEqual([]);
  });

  it("reseeds id counters past a restored workspace's ids", () => {
    const workspace = draftWorkspace();
    workspace.space[0]!.segment_id = "space-90";
    reseedCounters(workspace);
    const next = newDraftConstellation("nodalarc:nodes/space/starlink-v2-mesh.yaml");
    expect(Number(next.segment_id.split("-")[1])).toBeGreaterThan(90);
  });
});

describe("routing", () => {
  function routedWorkspace() {
    const workspace = draftWorkspace();
    const ground = newDraftGroundSet("nodalarc:nodes/ground/leo-gateway.yaml", {});
    ground.members = mintSiteMembers(ground, parseSiteLines("Denver, 39.7, -104.9").rows);
    workspace.ground.push(ground);
    const placed = placedSegments(workspace);
    workspace.links.push(
      defaultLinkRule(
        placed.find((s) => s.kind === "ground")!,
        placed.find((s) => s.kind === "space")!,
      ),
    );
    return workspace;
  }

  it("a new domain seeds over every placed segment and emits any-selectors", () => {
    const workspace = routedWorkspace();
    const domain = defaultRoutingDomain(workspace);
    domain.label = "Earth Domain";
    workspace.routing_domains.push(domain);
    expect(domain.member_segment_ids).toHaveLength(2);
    const doc = toSessionDocument(workspace) as any;
    const emitted = doc.routing.domains[0];
    expect(emitted.id).toBe("earth-domain");
    expect(emitted.protocol).toBe("isis");
    expect(emitted.selectors[0].any).toHaveLength(2);
    expect(emitted.area_assignment).toEqual({ strategy: "flat" });
    expect(emitted.timers).toBeUndefined(); // engine defaults when unset
  });

  it("single-member domains emit a bare segment selector; timers only when set", () => {
    const workspace = routedWorkspace();
    const domain = defaultRoutingDomain(workspace);
    domain.member_segment_ids = domain.member_segment_ids.slice(0, 1);
    domain.hello_interval_s = 1;
    domain.hold_interval_s = 3;
    workspace.routing_domains.push(domain);
    const doc = toSessionDocument(workspace) as any;
    const emitted = doc.routing.domains[0];
    expect(emitted.selectors[0].segment).toBeDefined();
    expect(emitted.timers).toEqual({ hello_interval_s: 1, hold_interval_s: 3 });
  });

  it("bgp domains carry no area assignment (IGP-only concept)", () => {
    const workspace = routedWorkspace();
    const domain = defaultRoutingDomain(workspace);
    domain.protocol = "bgp";
    workspace.routing_domains.push(domain);
    const doc = toSessionDocument(workspace) as any;
    expect(doc.routing.domains[0].area_assignment).toBeUndefined();
  });

  it("boundaries emit the shipped exchange: over the rule, symmetric originated export", () => {
    const workspace = routedWorkspace();
    const earth = defaultRoutingDomain(workspace);
    earth.label = "earth domain";
    const island = defaultRoutingDomain(workspace);
    island.label = "island domain";
    workspace.routing_domains.push(earth, island);
    const rule = workspace.links[0]!;
    rule.label = "GEO to Luna";
    const boundary = defaultBoundary(workspace);
    workspace.boundaries.push(boundary);
    expect(boundary.from_domain_id).toBe(earth.domain_id);
    expect(boundary.to_domain_id).toBe(island.domain_id);
    const doc = toSessionDocument(workspace) as any;
    const emitted = doc.routing.boundaries[0];
    expect(emitted.over).toBe("geo-to-luna");
    expect(emitted.adapter).toBe("static_ip");
    expect(emitted.export).toHaveLength(2);
    expect(emitted.export[0]).toEqual({
      from: "earth-domain",
      to: "island-domain",
      prefixes: { aggregate_of: "originated" },
      export_node_loopbacks: true,
      install_via: "peer_loopback",
    });
    expect(emitted.export[1].from).toBe("island-domain");
  });

  it("warns on empty members, stale references, and self-boundaries", () => {
    const workspace = routedWorkspace();
    const domain = defaultRoutingDomain(workspace);
    domain.member_segment_ids = [];
    workspace.routing_domains.push(domain);
    const boundary = defaultBoundary(workspace);
    boundary.to_domain_id = boundary.from_domain_id;
    boundary.over_rule_id = "gone";
    workspace.boundaries.push(boundary);
    const warnings = routingWarnings(workspace);
    expect(warnings.some((w) => w.includes("no member segments"))).toBe(true);
    expect(warnings.some((w) => w.includes("no longer in the session"))).toBe(true);
    expect(warnings.some((w) => w.includes("two different domains"))).toBe(true);
  });
});

describe("parseSiteLines", () => {
  it("parses name, lat, lon lines with optional altitude, tabs or commas", () => {
    const { rows, errors } = parseSiteLines(
      "New York, 40.7, -74.0\nPerth\t-31.9\t115.8\t20\n\n",
    );
    expect(errors).toEqual([]);
    expect(rows).toHaveLength(2);
    expect(rows[0]).toMatchObject({
      name: "New York",
      lat_deg: 40.7,
      lon_deg: -74.0,
      alt_m: 0,
    });
    expect(rows[1]).toMatchObject({ name: "Perth", alt_m: 20 });
  });

  it("reports bad lines instead of silently dropping them", () => {
    const { rows, errors } = parseSiteLines("Denver, 39.7\nOslo, north, 10.7");
    expect(rows).toEqual([]);
    expect(errors).toHaveLength(2);
    expect(errors[0]).toContain("expected: name, lat, lon");
    expect(errors[1]).toContain("must be numbers");
  });

  it("mints full sites: stamp node + derived addressing, owned per site", () => {
    const draft = newDraftGroundSet("nodalarc:nodes/ground/leo-gateway.yaml", { a: 1 });
    draft.members = mintSiteMembers(draft, parseSiteLines("Denver, 39.7, -104.9").rows);
    const more = mintSiteMembers(draft, parseSiteLines("Perth, -31.9, 115.8").rows);
    // Mint indices continue past existing draft members — no address reuse.
    expect(more[0]!.site!.lan_ipv4).toBe(stampLanPrefix(draft.stamp, 1));
    expect(draft.members[0]!.site!.nodes[0]!.model_ref).toBe(
      "nodalarc:nodes/ground/leo-gateway.yaml",
    );
    expect(draft.members[0]!.site!.nodes[0]!.installed).toEqual({ a: 1 });
  });
});

describe("groundWarnings", () => {
  it("warns on malformed bases, duplicate sites, and off-the-map coordinates", () => {
    const draft = newDraftGroundSet("nodalarc:nodes/ground/leo-gateway.yaml", {});
    draft.stamp.lan_base = "172.999";
    draft.members = mintSiteMembers(draft, parseSiteLines("A, 95, 10\nA, 10, 200").rows);
    const warnings = groundWarnings(draft);
    expect(warnings.some((w) => w.includes("lan base"))).toBe(true);
    expect(warnings.some((w) => w.includes("duplicate site id"))).toBe(true);
    expect(warnings.some((w) => w.includes("latitude 95"))).toBe(true);
    expect(warnings.some((w) => w.includes("longitude 200"))).toBe(true);
  });

  it("stays quiet on a healthy draft — empty is pending, not a warning", () => {
    const draft = newDraftGroundSet("nodalarc:nodes/ground/leo-gateway.yaml", {});
    expect(groundWarnings(draft)).toEqual([]);
  });
});

describe("draftGroundSetFromDocuments + draftSiteFromDocument", () => {
  const siteDocument = (id: string, model: string, extraNode = false) => ({
    site: {
      id,
      display_name: id,
      lan: { ipv4: "172.16.1.0/24" },
      nodes: [
        {
          id: "gw1",
          model,
          payloads: {},
          terminals: { access_ka: { installed_count: 4 } },
          interfaces: {
            lo0: { ipv4: "10.255.0.1/32" },
            terr0: { ipv4: "172.16.1.1/24" },
          },
        },
        ...(extraNode
          ? [
              {
                id: "gw2",
                model,
                payloads: {},
                terminals: {},
                interfaces: {
                  lo0: { ipv4: "10.255.0.2/32" },
                  terr0: { ipv4: "172.16.1.2/24" },
                },
              },
            ]
          : []),
      ],
      frame: { body_fixed: { body: "nodalarc:bodies/earth.yaml" } },
      location: { lat_deg: 39.7, lon_deg: -104.9, alt_m: 1600 },
    },
  });

  it("forks a set as a combination of defined sites — refs stay refs", () => {
    const draft = draftGroundSetFromDocuments(
      { site_set: { id: "gw", display_name: "Gateways", tags: ["teleport"] } },
      [
        {
          ref: "nodalarc:sites/earth/us/denver.yaml",
          document: siteDocument("denver", "nodalarc:nodes/ground/leo-gateway.yaml", true),
        },
        {
          ref: null,
          document: siteDocument("perth", "nodalarc:nodes/ground/leo-gateway.yaml"),
        },
      ],
    );
    expect(draft.display_name).toBe("Gateways (custom)");
    // Referenced member keeps full fidelity by ref — multi-node is fine.
    expect(draft.members[0]).toMatchObject({
      kind: "ref",
      ref: "nodalarc:sites/earth/us/denver.yaml",
      site_id: "denver",
    });
    // Inline member becomes an editable site draft.
    expect(draft.members[1]!.kind).toBe("draft");
    expect(draft.members[1]!.site!.nodes[0]!.lo0_ipv4).toBe("10.255.0.1/32");
    // The stamp seeds from the first readable node for future minting.
    expect(draft.stamp.node_ref).toBe("nodalarc:nodes/ground/leo-gateway.yaml");
    expect(draft.stamp.installed).toEqual({ access_ka: 4 });
    expect(draft.tags).toEqual(["teleport"]);
  });

  it("forks a multi-node site at full fidelity — every node carries over", () => {
    const site = draftSiteFromDocument(
      siteDocument("denver", "nodalarc:nodes/ground/leo-gateway.yaml", true),
    );
    expect(site.nodes).toHaveLength(2);
    expect(site.nodes[1]).toMatchObject({ node_id: "gw2", lo0_ipv4: "10.255.0.2/32" });
    expect(site.lan_ipv4).toBe("172.16.1.0/24");
  });

  it("carries any body-fixed body verbatim — a location is (body, lat, lon)", () => {
    const luna = siteDocument("moon-base", "nodalarc:nodes/ground/luna-surface-gateway.yaml");
    (luna.site.frame as any) = { body_fixed: { body: "nodalarc:bodies/luna.yaml" } };
    const draft = draftSiteFromDocument(luna);
    expect(draft.body).toBe("nodalarc:bodies/luna.yaml");
    const emitted = siteObjectFromDraft(draft) as any;
    expect(emitted.frame).toEqual({ body_fixed: { body: "nodalarc:bodies/luna.yaml" } });
  });

  it("refuses what the site editor cannot represent, loudly", () => {
    const anchored = siteDocument("anchored", "nodalarc:nodes/ground/leo-gateway.yaml");
    (anchored.site.frame as any) = { ephemeris_anchor: { frame: "some-track" } };
    expect(() => draftSiteFromDocument(anchored)).toThrow(/body-fixed surface sites/);
    const v6only = siteDocument("v6", "nodalarc:nodes/ground/leo-gateway.yaml");
    (v6only.site.lan as any) = { ipv6: "fd00::/64" };
    expect(() => draftSiteFromDocument(v6only)).toThrow(/IPv6-only/);
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

describe("dwell longitude lens (geosynchronous orbits)", () => {
  const epoch = "2026-06-08T00:00:00Z";
  const geo = {
    ...defaultDraftOrbit(),
    shape_kind: "circular" as const,
    altitude_km: 35786,
    inclination_deg: 0,
  };

  it("only offers the lens near GEO altitude on circular orbits", () => {
    expect(isGeosynchronous(geo)).toBe(true);
    expect(isGeosynchronous({ ...geo, altitude_km: 550 })).toBe(false);
    expect(isGeosynchronous({ ...geo, shape_kind: "elliptical" })).toBe(false);
  });

  it("round-trips: setting a dwell longitude derives the mean anomaly that reads back as that longitude", () => {
    for (const lon of [-100.5, 0, 77.2, 179]) {
      const mean_anomaly_deg = meanAnomalyForDwell(lon, geo, epoch);
      expect(mean_anomaly_deg).toBeGreaterThanOrEqual(0);
      expect(mean_anomaly_deg).toBeLessThan(360);
      const back = dwellLongitudeDeg({ ...geo, mean_anomaly_deg }, epoch);
      expect(back).toBeCloseTo(lon, 9);
    }
  });

  it("moving RAAN shifts the derived longitude by the same amount", () => {
    const base = dwellLongitudeDeg(geo, epoch);
    const shifted = dwellLongitudeDeg({ ...geo, raan_deg: geo.raan_deg + 30 }, epoch);
    expect((((shifted - base) % 360) + 360) % 360).toBeCloseTo(30, 9);
  });
});

describe("central body is authored state, never a hardcoded earth", () => {
  it("serializes the draft's body ref verbatim", () => {
    const ws = newWorkspace("t");
    const draft = newDraftConstellation("nodalarc:nodes/space/x.yaml");
    draft.orbit.central_body = "nodalarc:bodies/luna.yaml";
    ws.space.push(draft);
    const doc = toSessionDocument(ws) as {
      segments: { source: { constellation: { orbit: { central_body: string } } } }[];
    };
    expect(doc.segments[0]!.source.constellation.orbit.central_body).toBe(
      "nodalarc:bodies/luna.yaml",
    );
  });

  it("defaults to earth and reads a document's body back on fork", () => {
    expect(defaultDraftOrbit().central_body).toBe("nodalarc:bodies/earth.yaml");
  });

  it("a non-Earth orbit makes the session carry the kernel manifest; earth-only does not", () => {
    const ws = newWorkspace("t");
    const draft = newDraftConstellation("nodalarc:nodes/space/x.yaml");
    ws.space.push(draft);
    expect((toSessionDocument(ws) as { ephemeris?: unknown }).ephemeris).toBeUndefined();
    draft.orbit.central_body = "nodalarc:bodies/luna.yaml";
    const doc = toSessionDocument(ws) as {
      ephemeris?: { kernels: { id: string; targets: string[] }[] };
    };
    expect(doc.ephemeris?.kernels[0]?.id).toBe("de440s");
    expect(doc.ephemeris?.kernels[0]?.targets).toContain("nodalarc:bodies/luna.yaml");
  });

  it("the atmosphere warning is Earth physics — a 100 km lunar orbit is clean", () => {
    const luna = {
      ...defaultDraftOrbit(),
      altitude_km: 100,
      central_body: "nodalarc:bodies/luna.yaml",
    };
    expect(orbitWarnings(luna)).toEqual([]);
    expect(orbitWarnings({ ...luna, central_body: "nodalarc:bodies/earth.yaml" })).toHaveLength(1);
    expect(orbitWarnings({ ...luna, altitude_km: -5 })).toHaveLength(1);
  });

  it("the dwell lens is Earth math — a lunar orbit never claims it", () => {
    const orbit = {
      ...defaultDraftOrbit(),
      altitude_km: 35786,
      central_body: "nodalarc:bodies/luna.yaml",
    };
    expect(isGeosynchronous(orbit)).toBe(false);
  });
});

describe("a fresh workspace starts now, not at a fixed date", () => {
  it("seeds start_time within a minute of creation", () => {
    const ws = newWorkspace("t");
    const startMs = Date.parse(ws.start_time);
    expect(Number.isFinite(startMs)).toBe(true);
    expect(Math.abs(Date.now() - startMs)).toBeLessThan(120000);
    expect(ws.start_time.endsWith("00Z")).toBe(true);
  });
});

describe("RF band derives from frequency (ITU satellite letter bands)", () => {
  it("names the band per the ITU letter table: 1-2 L, 2-4 S, 26.5-40 Ka", () => {
    expect(bandForFrequencyGhz(1.5)).toBe("l");
    expect(bandForFrequencyGhz(2.0)).toBe("s");
    expect(bandForFrequencyGhz(3.9)).toBe("s");
    expect(bandForFrequencyGhz(6)).toBe("c");
    expect(bandForFrequencyGhz(14)).toBe("ku");
    expect(bandForFrequencyGhz(26.5)).toBe("ka");
    expect(bandForFrequencyGhz(39.9)).toBe("ka");
    expect(bandForFrequencyGhz(50)).toBe("v");
  });

  it("outside the lettered table is null, never a guess", () => {
    expect(bandForFrequencyGhz(0.001)).toBe(null);
    expect(bandForFrequencyGhz(200)).toBe(null);
  });
});

describe("workspaceFromSessionDocument — the serializer's inverse", () => {
  /** A workspace exercising every draft family the builder can author. */
  function authoredWorkspace() {
    const ws = newWorkspace("round-trip-study");
    const shell = newDraftConstellation("nodalarc:nodes/space/starlink-v2-mesh.yaml");
    shell.display_name = "Shell one";
    shell.planes = 2;
    shell.slots_per_plane = 3;
    ws.space.push(shell);
    const ground = newDraftGroundSet("nodalarc:nodes/ground/earth-leo-gateway.yaml", {});
    ground.display_name = "Study ground";
    const siteA = newDraftSiteObject("nodalarc:nodes/ground/earth-leo-gateway.yaml", {});
    siteA.site_id = "alpha";
    siteA.display_name = "Alpha";
    const siteB = { ...newDraftSiteObject("nodalarc:nodes/ground/earth-leo-gateway.yaml", {}) };
    siteB.site_id = "bravo";
    siteB.display_name = "Bravo";
    siteB.lat_deg = 45;
    ground.members.push(draftGroundMember(siteA), draftGroundMember(siteB));
    ground.members[1]!.scheduling_override = "geo-longest-pass";
    ground.originated_ipv4 = ["203.0.113.0/24"];
    ground.tags = ["study"];
    ws.ground.push(ground);
    ws.space_refs.push({
      segment_id: "lib-shell",
      ref: "nodalarc:constellations/earth-leo-starlink.yaml",
      label: "earth-leo-starlink",
    });
    const rule = defaultLinkRule(
      { segment_id: ground.segment_id, label: "Study ground", kind: "ground" },
      { segment_id: shell.segment_id, label: "Shell one", kind: "space" },
    );
    rule.label = "study access";
    rule.a.tag = "study";
    rule.a.min_elevation_deg = 25;
    rule.topology_mode = "nearest_n";
    rule.topology_n = 3;
    rule.max_range_km = 3000;
    ws.links.push(rule);
    const domain = defaultRoutingDomain(ws);
    domain.label = "everything isis";
    domain.member_segment_ids = [shell.segment_id, ground.segment_id, "lib-shell"];
    domain.hello_interval_s = 3;
    domain.hold_interval_s = 10;
    ws.routing_domains.push(domain);
    const second = defaultRoutingDomain(ws);
    second.label = "edge ospf";
    second.protocol = "ospf";
    second.member_segment_ids = [ground.segment_id];
    ws.routing_domains.push(second);
    const boundary = defaultBoundary(ws);
    boundary.over_rule_id = rule.rule_id;
    boundary.from_domain_id = domain.domain_id;
    boundary.to_domain_id = second.domain_id;
    ws.boundaries.push(boundary);
    ws.max_pairs_per_rule = 500;
    ws.max_pairs_per_tick = 4000;
    return ws;
  }

  it("round-trips a builder-authored session exactly — import then re-serialize", () => {
    const document = toSessionDocument(authoredWorkspace());
    const result = workspaceFromSessionDocument(document);
    expect(result.issues).toBeUndefined();
    expect(toSessionDocument(result.workspace!)).toEqual(document);
  });

  it("carries the session identity through import", () => {
    const document = toSessionDocument(authoredWorkspace());
    const result = workspaceFromSessionDocument(document);
    expect(result.workspace!.name).toBe("round-trip-study");
    expect(result.workspace!.space[0]!.display_name).toBe("Shell one");
    expect(result.workspace!.ground[0]!.members[1]!.scheduling_override).toBe(
      "geo-longest-pass",
    );
  });

  it("refuses grammar the builder cannot author, naming the block", () => {
    const document = toSessionDocument(authoredWorkspace());
    document.addressing = { loopbacks: { ipv4_pool: "10.255.0.0/16" } };
    const result = workspaceFromSessionDocument(document);
    expect(result.workspace).toBeUndefined();
    expect(result.issues!.some((i) => i.startsWith("addressing"))).toBe(true);
  });

  it("refuses fixed pair lists — explicit_pairs is not authorable yet", () => {
    const document = toSessionDocument(authoredWorkspace());
    (document.link_rules as Record<string, unknown>[])[0]!.topology = {
      mode: "explicit_pairs",
      pairs: [{ a: "x", b: "y" }],
    };
    const result = workspaceFromSessionDocument(document);
    expect(result.issues!.some((i) => i.includes("explicit_pairs"))).toBe(true);
  });

  it("refuses per-segment epochs that differ from the session start", () => {
    const document = toSessionDocument(authoredWorkspace());
    const segments = document.segments as Record<string, unknown>[];
    const inline = segments.find((s) => typeof s.source === "object")!;
    ((inline.source as Record<string, unknown>).constellation as Record<string, unknown>)
      .orbit = {
      ...(((inline.source as Record<string, unknown>).constellation as Record<
        string,
        unknown
      >).orbit as Record<string, unknown>),
      epoch: "2001-01-01T00:00:00Z",
    };
    const result = workspaceFromSessionDocument(document);
    expect(result.issues!.some((i) => i.includes("epoch"))).toBe(true);
  });

  it("refuses a scheduling block that matches no builder preset", () => {
    const document = toSessionDocument(authoredWorkspace());
    const segments = document.segments as Record<string, unknown>[];
    const ground = segments.find((s) => s.placement !== undefined)!;
    (ground.apply as Record<string, unknown>).scheduling = { custom: true };
    const result = workspaceFromSessionDocument(document);
    expect(result.issues!.some((i) => i.includes("scheduling"))).toBe(true);
  });

  it("an import that would not reproduce the document refuses with the path", () => {
    const document = toSessionDocument(authoredWorkspace());
    // A field the importer does not read and the serializer re-derives:
    // the inline constellation's own id.
    const segments = document.segments as Record<string, unknown>[];
    const inline = segments.find((s) => typeof s.source === "object")!;
    ((inline.source as Record<string, unknown>).constellation as Record<string, unknown>).id =
      "hand-renamed-inner-id";
    const result = workspaceFromSessionDocument(document);
    expect(result.workspace).toBeUndefined();
    expect(result.issues!.some((i) => i.includes("cannot reproduce"))).toBe(true);
  });
});

describe("workspaceFromSessionDocument — placed refs and inline nodes", () => {
  it("round-trips ground refs, an inline node draft, and a non-Earth orbit", () => {
    const ws = newWorkspace("ref-heavy-study");
    const shell = newDraftConstellation("");
    shell.display_name = "Luna shell";
    shell.orbit.central_body = "nodalarc:bodies/luna.yaml";
    shell.node_draft = {
      id: "custom-bird",
      display_name: "Custom bird",
      forwarding: "routed",
      ethernet: ["terr0"],
      terminals: [
        {
          mount_id: "isl_0",
          role: "isl",
          terminal_ref: "nodalarc:terminals/optical/leo-crosslink.yaml",
          count: 2,
        },
      ],
    };
    ws.space.push(shell);
    ws.ground_refs.push({
      segment_id: "lib-ground",
      ref: "nodalarc:site-sets/earth/leo/earth-leo-pop-sites.yaml",
      label: "earth-leo-pop-sites",
      scheduling_preset: "geo-longest-pass",
    });
    const document = toSessionDocument(ws);
    expect(document.ephemeris).toBeDefined();
    const result = workspaceFromSessionDocument(document);
    expect(result.issues).toBeUndefined();
    expect(toSessionDocument(result.workspace!)).toEqual(document);
    expect(result.workspace!.ground_refs[0]!.scheduling_preset).toBe("geo-longest-pass");
    expect(result.workspace!.space[0]!.node_draft?.terminals[0]?.count).toBe(2);
  });
});

describe("held-back incomplete containers", () => {
  it("an empty ground draft and everything referencing it stay out of the artifact", () => {
    const ws = newWorkspace("holdback-study");
    ws.space.push(newDraftConstellation("nodalarc:nodes/space/starlink-v2-mesh.yaml"));
    const shell = ws.space[0]!;
    const ground = newDraftGroundSet("nodalarc:nodes/ground/leo-gateway.yaml", {});
    ws.ground.push(ground); // zero sites
    const rule = defaultLinkRule(
      { segment_id: ground.segment_id, label: "Empty ground", kind: "ground" },
      { segment_id: shell.segment_id, label: "Shell", kind: "space" },
    );
    ws.links.push(rule);
    const domain = defaultRoutingDomain(ws);
    ws.routing_domains.push(domain);
    const doc = toSessionDocument(ws) as Record<string, unknown>;
    const segments = doc.segments as Record<string, unknown>[];
    expect(segments.some((s) => s.id === ground.segment_id)).toBe(false);
    expect(doc.link_rules).toBeUndefined();
    // The domain sheds the held-back member but keeps the emitted one.
    const domains = (doc.routing as { domains: Record<string, unknown>[] }).domains;
    expect(JSON.stringify(domains[0]!.selectors)).not.toContain(ground.segment_id);
    // The hold-back is stated, never silent.
    expect(
      completenessFindings(ws).some((f) => f.message.includes("held out of the artifact")),
    ).toBe(true);
    expect(linkWarnings(ws).some((w) => w.includes("held out of the artifact"))).toBe(true);
  });

  it("a domain whose members are all held back is itself held back", () => {
    const ws = newWorkspace("holdback-domain");
    ws.space.push(newDraftConstellation("nodalarc:nodes/space/starlink-v2-mesh.yaml"));
    const ground = newDraftGroundSet("nodalarc:nodes/ground/leo-gateway.yaml", {});
    ws.ground.push(ground);
    const domain = defaultRoutingDomain(ws);
    domain.member_segment_ids = [ground.segment_id];
    ws.routing_domains.push(domain);
    const doc = toSessionDocument(ws) as Record<string, unknown>;
    expect(doc.routing).toBeUndefined();
  });
});

describe("defaultRoutingDomain", () => {
  it("seeds only the uncovered segments — a second domain means the rest", () => {
    const ws = newWorkspace("domain-seeding");
    ws.space.push(newDraftConstellation("nodalarc:nodes/space/starlink-v2-mesh.yaml"));
    ws.space.push(newDraftConstellation("nodalarc:nodes/space/starlink-v2-mesh.yaml"));
    const first = defaultRoutingDomain(ws);
    expect(first.member_segment_ids).toHaveLength(2);
    first.member_segment_ids = [ws.space[0]!.segment_id];
    ws.routing_domains.push(first);
    const second = defaultRoutingDomain(ws);
    expect(second.member_segment_ids).toEqual([ws.space[1]!.segment_id]);
    ws.routing_domains.push(second);
    // Everything covered: the third seeds empty (held back until members).
    expect(defaultRoutingDomain(ws).member_segment_ids).toEqual([]);
  });
});

describe("multi-body ground authoring", () => {
  it("mints sites on the stamp's body and the manifest follows", () => {
    const ws = newWorkspace("luna-ground-study");
    const ground = newDraftGroundSet(
      "nodalarc:nodes/ground/luna-surface-gateway.yaml",
      {},
      "nodalarc:bodies/luna.yaml",
    );
    ground.members = mintSiteMembers(
      ground,
      parseSiteLines("Artemis Base, -89.4, 30.0\nMare Crisium, 17.0, 59.1").rows,
    );
    ws.ground.push(ground);
    expect(ground.members[0]!.site?.body).toBe("nodalarc:bodies/luna.yaml");
    expect(usesNonEarthBodies(ws)).toBe(true);
    const doc = toSessionDocument(ws) as any;
    expect(doc.ephemeris).toBeDefined();
    const segment = doc.segments.find((s: any) => s.placement);
    const artemis = segment.placement.from_site_set.site_set.sites[0].site;
    expect(artemis.frame).toEqual({
      body_fixed: { body: "nodalarc:bodies/luna.yaml" },
    });
    expect(artemis.location.lat_deg).toBe(-89.4);
  });

  it("round-trips a lunar ground session exactly", () => {
    const ws = newWorkspace("luna-roundtrip");
    const shell = newDraftConstellation("nodalarc:nodes/space/luna-relay.yaml");
    shell.orbit.central_body = "nodalarc:bodies/luna.yaml";
    ws.space.push(shell);
    const ground = newDraftGroundSet(
      "nodalarc:nodes/ground/luna-surface-gateway.yaml",
      {},
      "nodalarc:bodies/luna.yaml",
    );
    ground.members = mintSiteMembers(ground, parseSiteLines("Shackleton, -89.9, 0.0").rows);
    ws.ground.push(ground);
    const document = toSessionDocument(ws);
    const result = workspaceFromSessionDocument(document);
    expect(result.issues).toBeUndefined();
    expect(toSessionDocument(result.workspace!)).toEqual(document);
    expect(result.workspace!.ground[0]!.members[0]!.site?.body).toBe(
      "nodalarc:bodies/luna.yaml",
    );
    expect(result.workspace!.ground[0]!.stamp.body).toBe("nodalarc:bodies/luna.yaml");
  });
});
