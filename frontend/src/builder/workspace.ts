// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** The builder workspace — client-side drafts that serialize to the grammar.
 *
 *  A workspace is the editable form of a session: draft constellations
 *  (inline orbit values + pattern + a catalog node reference) plus optional
 *  shipped ground placement. `toSessionDocument` is THE serializer — every
 *  gesture becomes a grammar production a user could hand-author; inline
 *  objects and references are the same grammar (the loader contract), so a
 *  draft session is a valid session, resolvable before anything is saved.
 *
 *  Client-side until save: the workspace lives in browser
 *  state; resolve-check posts the serialized document and the world renders
 *  from the resolver's expansion, never from a builder-local one.
 */

import { gmstRadians } from "../sim/orbitalMath";

export interface DraftOrbit {
  /** The body this orbit is around — a bodies-catalog ref, serialized
   *  verbatim. The runtime decides what it supports; unsupported bodies get
   *  the resolver's typed wall, never a silent earth default. */
  central_body: string;
  /** Circular uses altitude_km; elliptical uses perigee/apogee. One form
   *  serializes (the grammar's OrbitShape variants); the other fields are
   *  kept so switching shape kinds never loses typed values. */
  shape_kind: "circular" | "elliptical";
  altitude_km: number;
  perigee_altitude_km: number;
  apogee_altitude_km: number;
  inclination_deg: number;
  raan_deg: number;
  argument_of_perigee_deg: number;
  mean_anomaly_deg: number;
  propagator: "two_body" | "j2_mean_elements";
}

export interface DraftTerminalMount {
  mount_id: string;
  role: "access" | "isl" | "crosslink" | "backbone";
  terminal_ref: string;
  count: number;
}

/** An editable node: the grammar's Node object as draft state. LAN attach is
 *  ``ethernet`` ports (the builder's "lan" chips serialize there — LAN is not
 *  a terminal role). */
export interface DraftNode {
  id: string;
  display_name: string;
  forwarding: "routed" | "host" | "bridge" | "control_only";
  ethernet: string[];
  terminals: DraftTerminalMount[];
}

export interface DraftConstellation {
  segment_id: string;
  display_name: string;
  /** The node model reference (shipped or user). When ``node_draft`` is set
   *  it overrides the ref in serialization — fork-to-draft sets the draft,
   *  discard clears it, save-to-library replaces the ref and clears it. */
  node_ref: string;
  node_draft: DraftNode | null;
  orbit: DraftOrbit;
  planes: number;
  raan_spacing_deg: number;
  slots_per_plane: number;
  phase_offset_deg: number;
}

/** One node installed at a site: a model plus what's actually mounted and
 *  how it addresses. The node is the router; the site is the place. */
export interface DraftSiteNode {
  node_id: string;
  model_ref: string;
  /** Installed count per mount id, seeded from the model's faceplate. */
  installed: Record<string, number>;
  lo0_ipv4: string;
  terr0_ipv4: string;
}

/** A SITE is a first-class primitive — the terminals, nodes, networks, and
 *  parameters that make up a location, not just a lat/lon. This is the
 *  grammar's Site object as an editable draft (IPv4-only for now). */
export interface DraftSiteObject {
  site_id: string;
  display_name: string;
  lat_deg: number;
  lon_deg: number;
  alt_m: number;
  lan_ipv4: string;
  tags: string[];
  nodes: DraftSiteNode[];
}

/** One member of a ground segment: a DEFINED site — placed by reference at
 *  full fidelity (its nodes travel with it), or an authored draft. */
export interface DraftGroundSite {
  /** Stable list key, builder-local. */
  member_id: string;
  kind: "ref" | "draft";
  /** Catalog reference when kind=ref. */
  ref: string | null;
  /** The site's grammar id — override matching keys on this. */
  site_id: string;
  label: string;
  /** Hardware line for ref rows (from the catalog browse). */
  summary: string | null;
  site: DraftSiteObject | null;
  /** Sparse per-site scheduling: null = segment template ("= template");
   *  a preset key = an override stored as a GroundOverride exception. */
  scheduling_override: SchedulingPresetKey | null;
}

/** The stamp: what bulk paste mints NEW sites with — a node model and
 *  addressing bases. Applied once at creation; every minted site owns its
 *  configuration afterwards (edit the site, not the stamp). */
export interface GroundStamp {
  node_ref: string;
  installed: Record<string, number>;
  /** IPv4 base for minted site LANs: mint i gets base.<i>.0/24, terr0 .1. */
  lan_base: string;
  /** IPv4 base for minted loopbacks: mint i gets base.0.<i+1>/32. */
  loopback_base: string;
}

/** An editable ground segment: a COMBINATION of defined sites, plus the
 *  session-level application (scheduling intent, originated prefixes, tags,
 *  sparse per-site overrides). */
export interface DraftGroundSet {
  segment_id: string;
  display_name: string;
  members: DraftGroundSite[];
  stamp: GroundStamp;
  scheduling_preset: SchedulingPresetKey;
  /** apply-level originated prefixes (routing injection intent). */
  originated_ipv4: string[];
  tags: string[];
}

/** A space segment sourced from a library block as-is (use-this-block).
 *  Customizing forks it into an editable DraftConstellation. */
export interface RefSegment {
  segment_id: string;
  ref: string;
  label: string;
}

/** One endpoint of an authored link rule: a placed segment, optionally
 *  scoped to a tag (how one ground segment serves multiple constellations
 *  differently), with the terminal role/medium it selects and an optional
 *  elevation mask. */
export interface DraftLinkEndpoint {
  segment_id: string;
  tag: string | null;
  role: "access" | "isl" | "crosslink";
  medium: "rf" | "optical";
  min_elevation_deg: number | null;
}

/** An authored link rule — comms INTENT between placed segments. Rules
 *  declare who MAY link; OME computes feasibility from geometry, terminal
 *  limits, and runtime state. */
export interface DraftLinkRule {
  rule_id: string;
  label: string;
  enabled: boolean;
  a: DraftLinkEndpoint;
  b: DraftLinkEndpoint;
  /** nearest_visible exists in the grammar but is runtime-gated — never
   *  offered here; the resolver walls it with UnsupportedFeature. */
  topology_mode: "visible_candidates" | "nearest_n";
  topology_n: number;
  max_range_km: number | null;
}

/** An authored routing domain: a protocol over member segments. Whole-
 *  segment membership only — per-terminal membership is a gated grammar
 *  change and walls at the gesture. Timers are the expert card: null =
 *  engine defaults (omitted from the artifact). */
export interface DraftRoutingDomain {
  domain_id: string;
  label: string;
  protocol: "isis" | "ospf" | "bgp" | "static";
  member_segment_ids: string[];
  hello_interval_s: number | null;
  hold_interval_s: number | null;
}

/** An authored routing boundary: a controlled exchange OVER a link rule.
 *  v1 export is the shipped exchange pattern — originated prefixes both
 *  ways, installed via peer loopback. */
export interface DraftBoundary {
  boundary_id: string;
  over_rule_id: string;
  adapter: "static_ip" | "bgp" | "dtn_bundle";
  from_domain_id: string;
  to_domain_id: string;
  export_node_loopbacks: boolean;
}

export interface Workspace {
  name: string;
  space: DraftConstellation[];
  /** Library constellations placed by reference (use-this-block). */
  space_refs: RefSegment[];
  /** Authored ground segments (drafts) — plural by design: teleport, edge,
   *  and experiment sets carry different scheduling in one session. */
  ground: DraftGroundSet[];
  /** Library site sets placed by reference (use-this-block). */
  ground_refs: RefGroundSet[];
  /** Authored comms intent between placed segments. */
  links: DraftLinkRule[];
  /** Authored routing domains + boundaries between them. */
  routing_domains: DraftRoutingDomain[];
  boundaries: DraftBoundary[];
  /** Candidate math budget — the grammar REQUIRES declared limits once link
   *  rules exist in a multi-segment session (no silent defaults). Sized to
   *  the largest shipped session; typeable like everything else. */
  max_pairs_per_rule: number;
  max_pairs_per_tick: number;
  start_time: string;
  /** Sim step and wall-clock compression — 1/1 is real time (the time-rate
   *  invariant: deviation is an explicit manipulation, never a default). */
  step_seconds: number;
  compression: number;
}

/** Orbit presets seed RAW VALUES the user then owns — never modes. */
export const ORBIT_PRESETS: { label: string; orbit: Partial<DraftOrbit> }[] = [
  { label: "LEO 550", orbit: { shape_kind: "circular", altitude_km: 550, inclination_deg: 53 } },
  {
    label: "Polar LEO",
    orbit: { shape_kind: "circular", altitude_km: 780, inclination_deg: 86.4 },
  },
  {
    label: "MEO (GPS-like)",
    orbit: { shape_kind: "circular", altitude_km: 20180, inclination_deg: 55 },
  },
  { label: "GEO", orbit: { shape_kind: "circular", altitude_km: 35786, inclination_deg: 0 } },
  {
    label: "Molniya",
    orbit: {
      shape_kind: "elliptical",
      perigee_altitude_km: 600,
      apogee_altitude_km: 39700,
      inclination_deg: 63.4,
      argument_of_perigee_deg: 270,
    },
  },
];

export const EARTH_BODY_REF = "nodalarc:bodies/earth.yaml";

/** The shipped planetary-ephemeris manifest (DE440s), exactly as the
 *  reference multi-body session declares it. A session whose orbits leave
 *  Earth must carry a kernel manifest; the builder seeds this one and the
 *  artifact column shows it — the resolver still validates file and
 *  checksum server-side. */
export const DE440S_EPHEMERIS = {
  provider: "skyfield_bsp",
  quality_tier: "de440s",
  kernels: [
    {
      id: "de440s",
      path: "configs/ephemerides/de440s.bsp",
      sha256: "c1c7feeab882263fc493a9d5a5b2ddd71b54826cdf65d8d17a76126b260a49f2",
      coverage_start: "1849-12-25T00:00:00Z",
      coverage_end: "2150-01-21T00:00:00Z",
      targets: ["nodalarc:bodies/earth.yaml", "nodalarc:bodies/luna.yaml"],
      frame: "gcrs",
    },
  ],
} as const;

/** True when any authored orbit is around a body other than Earth. */
export function usesNonEarthBodies(workspace: Workspace): boolean {
  return workspace.space.some((draft) => draft.orbit.central_body !== EARTH_BODY_REF);
}

export function defaultDraftOrbit(): DraftOrbit {
  return {
    central_body: EARTH_BODY_REF,
    shape_kind: "circular",
    altitude_km: 550,
    perigee_altitude_km: 550,
    apogee_altitude_km: 550,
    inclination_deg: 53,
    raan_deg: 0,
    argument_of_perigee_deg: 0,
    mean_anomaly_deg: 0,
    propagator: "j2_mean_elements",
  };
}

let draftCounter = 0;

export function newDraftConstellation(nodeRef: string): DraftConstellation {
  draftCounter += 1;
  return {
    segment_id: `space-${draftCounter}`,
    display_name: `Constellation ${draftCounter}`,
    node_ref: nodeRef,
    node_draft: null,
    orbit: defaultDraftOrbit(),
    planes: 3,
    raan_spacing_deg: 60,
    slots_per_plane: 8,
    phase_offset_deg: 0,
  };
}

let refCounter = 0;

export function newRefSegment(ref: string, label: string): RefSegment {
  refCounter += 1;
  return { segment_id: `lib-${refCounter}`, ref, label };
}

/** Fork a constellation document (plus its orbit document when referenced)
 *  into an editable draft — customize-a-library-block. Constructs the editor
 *  cannot represent refuse loudly; nothing is silently dropped. */
export function draftConstellationFromDocuments(
  constellationDocument: Record<string, unknown>,
  orbitDocument: Record<string, unknown> | null,
): DraftConstellation {
  const constellation = (
    constellationDocument as { constellation?: Record<string, unknown> }
  ).constellation;
  if (!constellation) throw new Error("not a constellation document");
  const phasing = (constellation.phasing ?? {}) as Record<string, unknown>;
  if (phasing.mode && phasing.mode !== "evenly_spaced_mean_anomaly") {
    throw new Error(
      `phasing mode ${String(phasing.mode)} — walker modes are pending their runtime semantics`,
    );
  }
  const orbitRaw =
    typeof constellation.orbit === "string"
      ? ((orbitDocument as { orbit?: Record<string, unknown> } | null)?.orbit ?? null)
      : ((constellation.orbit as Record<string, unknown>) ?? null);
  if (!orbitRaw) throw new Error("constellation orbit could not be read");
  const shape = (orbitRaw.shape ?? null) as Record<string, unknown> | null;
  if (!shape) throw new Error("element-form orbits are not editable yet");
  const orientation = (orbitRaw.orientation ?? {}) as Record<string, unknown>;
  const phase = (orbitRaw.phase ?? {}) as Record<string, unknown>;
  const propagator = String(orbitRaw.propagator ?? "j2_mean_elements");
  if (propagator !== "two_body" && propagator !== "j2_mean_elements") {
    throw new Error(`propagator ${propagator} is not editable yet`);
  }
  const planes = (constellation.planes ?? {}) as Record<string, unknown>;

  const orbit: DraftOrbit = {
    central_body:
      typeof orbitRaw.central_body === "string" ? orbitRaw.central_body : EARTH_BODY_REF,
    shape_kind: "altitude_km" in shape ? "circular" : "elliptical",
    altitude_km: Number(shape.altitude_km ?? 550),
    perigee_altitude_km: Number(shape.perigee_altitude_km ?? shape.altitude_km ?? 550),
    apogee_altitude_km: Number(shape.apogee_altitude_km ?? shape.altitude_km ?? 550),
    inclination_deg: Number(orientation.inclination_deg ?? 0),
    raan_deg: Number(orientation.raan_deg ?? 0),
    argument_of_perigee_deg: Number(orientation.argument_of_perigee_deg ?? 0),
    mean_anomaly_deg: Number(phase.mean_anomaly_deg ?? 0),
    propagator: propagator as DraftOrbit["propagator"],
  };

  draftCounter += 1;
  const node = constellation.node;
  return {
    segment_id: `space-${draftCounter}`,
    display_name: `${String(constellation.display_name ?? constellation.id)} (custom)`,
    node_ref: typeof node === "string" ? node : "",
    node_draft:
      typeof node === "object" && node !== null
        ? draftNodeFromDocument({ node: node as Record<string, unknown> })
        : null,
    orbit,
    planes: Number(planes.count ?? 1),
    raan_spacing_deg: Number(planes.raan_spacing_deg ?? 0),
    slots_per_plane: Number(constellation.slots_per_plane ?? 1),
    phase_offset_deg: Number(phasing.phase_offset_deg ?? 0),
  };
}

/** A blank node draft for from-scratch authoring. */
export function defaultDraftNode(): DraftNode {
  return {
    id: "my-node",
    display_name: "My node",
    forwarding: "routed",
    ethernet: ["terr0"],
    terminals: [],
  };
}

/** Convert a node document (authoring-wrapper form, as read from the catalog)
 *  into an editable draft — the fork-to-draft direction of tweak-ours→yours.
 *  Grammar constructs the editor cannot represent yet (payload mounts,
 *  mount tags) are refused loudly rather than silently dropped. */
export function draftNodeFromDocument(document: Record<string, unknown>): DraftNode {
  const node = (document as { node?: Record<string, unknown> }).node;
  if (!node) throw new Error("not a node document");
  const payloads = node.payloads as unknown[] | undefined;
  if (payloads && payloads.length > 0) {
    throw new Error("this node carries payload mounts — payload editing is not built yet");
  }
  const terminals = ((node.terminals as Record<string, unknown>[] | undefined) ?? []).map(
    (mount) => {
      if (typeof mount.terminal !== "string") {
        throw new Error(
          "this node inlines terminal definitions — inline-terminal editing is not built yet",
        );
      }
      return {
        mount_id: String(mount.id),
        role: mount.role as DraftTerminalMount["role"],
        terminal_ref: mount.terminal,
        count: Number(mount.count ?? 1),
      };
    },
  );
  return {
    id: String(node.id ?? "custom-node"),
    display_name: String(node.display_name ?? node.id ?? "Custom node"),
    forwarding: (node.forwarding as DraftNode["forwarding"]) ?? "routed",
    ethernet: ((node.ethernet as { id: string }[] | undefined) ?? []).map((port) =>
      String(port.id),
    ),
    terminals,
  };
}

/** Serialize a draft node to the grammar's Node object (unwrapped form —
 *  nested object-valued fields accept it; the save-to-library path wraps). */
export function nodeObjectFromDraft(draft: DraftNode): Record<string, unknown> {
  return {
    id: identifier(draft.id) || "custom-node",
    display_name: draft.display_name,
    forwarding: draft.forwarding,
    ethernet: draft.ethernet.map((id) => ({ id: identifier(id) || "terr0" })),
    terminals: draft.terminals.map((mount) => ({
      id: identifier(mount.mount_id),
      role: mount.role,
      terminal: mount.terminal_ref,
      count: mount.count,
    })),
    payloads: [],
    reference: "session-builder-draft",
  };
}

/** An editable terminal: the grammar's Terminal object (pure physics — no
 *  role, no placement; those live on mounts and sites). Terminals author
 *  LIBRARY-FIRST: the draft saves to the user catalog and mounts by
 *  reference, because a terminal has no session-local form. */
export interface DraftTerminal {
  id: string;
  display_name: string;
  medium: "rf" | "optical";
  /** rf signal */
  band: string;
  frequency_ghz: number;
  /** optical signal */
  wavelength_nm: number;
  transmit_mbps: number;
  receive_mbps: number;
  tracking_capacity: number;
  max_range_km: number;
  elevation_min_deg: number;
  elevation_max_deg: number;
  azimuth_min_deg: number;
  azimuth_max_deg: number;
  max_tracking_rate_deg_s: number;
  reference: string;
}

export function defaultDraftTerminal(): DraftTerminal {
  return {
    id: "my-terminal",
    display_name: "My terminal",
    medium: "rf",
    band: "ka",
    frequency_ghz: 29.5,
    wavelength_nm: 1550,
    transmit_mbps: 500,
    receive_mbps: 500,
    tracking_capacity: 1,
    max_range_km: 2500,
    elevation_min_deg: 20,
    elevation_max_deg: 90,
    azimuth_min_deg: -180,
    azimuth_max_deg: 180,
    max_tracking_rate_deg_s: 2,
    reference: "session-builder-draft",
  };
}

/** Seed a terminal draft from an existing document (fork-by-seeding). */
export function draftTerminalFromDocument(document: Record<string, unknown>): DraftTerminal {
  const terminal = (document as { terminal?: Record<string, unknown> }).terminal;
  if (!terminal) throw new Error("not a terminal document");
  const defaults = defaultDraftTerminal();
  const signal = (terminal.signal ?? {}) as Record<string, unknown>;
  const bandwidth = (terminal.bandwidth_mbps ?? {}) as Record<string, unknown>;
  const limits = (terminal.limits ?? {}) as Record<string, Record<string, unknown>>;
  return {
    id: String(terminal.id ?? defaults.id),
    display_name: String(terminal.display_name ?? terminal.id ?? defaults.display_name),
    medium: (terminal.medium as DraftTerminal["medium"]) ?? "rf",
    band: String(signal.band ?? defaults.band),
    frequency_ghz:
      typeof signal.frequency_hz === "number"
        ? signal.frequency_hz / 1e9
        : defaults.frequency_ghz,
    wavelength_nm:
      typeof signal.wavelength_nm === "number" ? signal.wavelength_nm : defaults.wavelength_nm,
    transmit_mbps: Number(bandwidth.transmit ?? defaults.transmit_mbps),
    receive_mbps: Number(bandwidth.receive ?? defaults.receive_mbps),
    tracking_capacity: Number(terminal.tracking_capacity ?? defaults.tracking_capacity),
    max_range_km: Number(terminal.max_range_km ?? defaults.max_range_km),
    elevation_min_deg: Number(limits.elevation_deg?.min ?? defaults.elevation_min_deg),
    elevation_max_deg: Number(limits.elevation_deg?.max ?? defaults.elevation_max_deg),
    azimuth_min_deg: Number(limits.azimuth_deg?.min ?? defaults.azimuth_min_deg),
    azimuth_max_deg: Number(limits.azimuth_deg?.max ?? defaults.azimuth_max_deg),
    max_tracking_rate_deg_s: Number(
      limits.max_tracking_rate_deg_s ?? defaults.max_tracking_rate_deg_s,
    ),
    reference: String(terminal.reference ?? defaults.reference),
  };
}

/** Serialize a terminal draft to the grammar's Terminal object. */
export function terminalObjectFromDraft(draft: DraftTerminal): Record<string, unknown> {
  return {
    id: identifier(draft.id) || "my-terminal",
    display_name: draft.display_name,
    medium: draft.medium,
    signal:
      draft.medium === "rf"
        ? { band: identifier(draft.band) || "ka", frequency_hz: draft.frequency_ghz * 1e9 }
        : { wavelength_nm: draft.wavelength_nm },
    bandwidth_mbps: { transmit: draft.transmit_mbps, receive: draft.receive_mbps },
    tracking_capacity: draft.tracking_capacity,
    max_range_km: draft.max_range_km,
    limits: {
      azimuth_deg: { min: draft.azimuth_min_deg, max: draft.azimuth_max_deg },
      elevation_deg: { min: draft.elevation_min_deg, max: draft.elevation_max_deg },
      max_tracking_rate_deg_s: draft.max_tracking_rate_deg_s,
    },
    reference: draft.reference,
  };
}

/** Terminal sanity findings: warn, never block. */
export function terminalWarnings(draft: DraftTerminal): string[] {
  const warnings: string[] = [];
  if (draft.elevation_min_deg > draft.elevation_max_deg) {
    warnings.push("elevation min is above max — swap them");
  }
  if (draft.azimuth_min_deg > draft.azimuth_max_deg) {
    warnings.push("azimuth min is above max — swap them");
  }
  return warnings;
}

export function newWorkspace(name: string): Workspace {
  return {
    name: identifier(name) || "untitled-session",
    space: [],
    space_refs: [],
    ground: [],
    ground_refs: [],
    links: [],
    routing_domains: [],
    boundaries: [],
    max_pairs_per_rule: 2000,
    max_pairs_per_tick: 10000,
    // The session starts when it was authored, not at a fixed date in the
    // past: a stale epoch turns the live view's "Now" into a huge sim-time
    // jump the moment the session runs. Whole-minute for readability.
    start_time: `${new Date().toISOString().slice(0, 17)}00Z`,
    step_seconds: 1,
    compression: 1,
  };
}

/** Normalize a display string into a grammar Identifier.
 *
 *  The grammar allows underscores (shipped mount ids like ``isl_optical``);
 *  they must survive round-trips. Mapping identifiers into runtime node ids
 *  (which ban underscores) is the RESOLVER's normalization, never ours. */
export function identifier(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "-")
    .replace(/^[-_]+|[-_]+$/g, "")
    .slice(0, 48);
}

/** IEEE/ITU letter bands as used for satellite allocations. The frequency
 *  is the single source of truth; the band NAME derives from it — picking a
 *  band seeds a typical satcom frequency the user then owns (IG-7). */
export const RF_BANDS: {
  band: string;
  minGhz: number;
  maxGhz: number;
  seedGhz: number;
}[] = [
  { band: "vhf", minGhz: 0.03, maxGhz: 0.3, seedGhz: 0.15 },
  { band: "uhf", minGhz: 0.3, maxGhz: 1, seedGhz: 0.4 },
  { band: "l", minGhz: 1, maxGhz: 2, seedGhz: 1.5 },
  { band: "s", minGhz: 2, maxGhz: 4, seedGhz: 2.2 },
  { band: "c", minGhz: 4, maxGhz: 8, seedGhz: 6 },
  { band: "x", minGhz: 8, maxGhz: 12, seedGhz: 8.4 },
  { band: "ku", minGhz: 12, maxGhz: 18, seedGhz: 14 },
  { band: "k", minGhz: 18, maxGhz: 26.5, seedGhz: 20 },
  { band: "ka", minGhz: 26.5, maxGhz: 40, seedGhz: 30 },
  { band: "v", minGhz: 40, maxGhz: 75, seedGhz: 50 },
  { band: "w", minGhz: 75, maxGhz: 110, seedGhz: 80 },
];

/** The lettered band a frequency falls in, or null outside the table. */
export function bandForFrequencyGhz(frequencyGhz: number): string | null {
  for (const row of RF_BANDS) {
    if (frequencyGhz >= row.minGhz && frequencyGhz < row.maxGhz) return row.band;
  }
  return null;
}

const GEO_ALTITUDE_KM = 35786;

/** Geosynchronous check for the dwell-longitude lens. Within ~500 km of GEO
 *  altitude the period tracks Earth's rotation closely enough that "which
 *  longitude does this bird sit over" is the question the user is actually
 *  answering with mean anomaly. */
export function isGeosynchronous(orbit: DraftOrbit): boolean {
  return (
    orbit.central_body === EARTH_BODY_REF &&
    orbit.shape_kind === "circular" &&
    Math.abs(orbit.altitude_km - GEO_ALTITUDE_KM) <= 500
  );
}

function gmstDegAt(epochIso: string): number {
  return (gmstRadians(Date.parse(epochIso) / 1000) * 180) / Math.PI;
}

/** Sub-satellite longitude of the first slot at session start (deg east,
 *  [-180, 180)). A lens on phase, not new state: it reads RAAN + argument of
 *  perigee + mean anomaly against sidereal time at the session epoch. Nonzero
 *  inclination turns the point into a figure-eight centered here. */
export function dwellLongitudeDeg(orbit: DraftOrbit, epochIso: string): number {
  const lon =
    orbit.raan_deg +
    orbit.argument_of_perigee_deg +
    orbit.mean_anomaly_deg -
    gmstDegAt(epochIso);
  return ((lon % 360) + 540) % 360 - 180;
}

/** Inverse of the lens: the mean anomaly that puts the first slot over the
 *  given longitude at session start. */
export function meanAnomalyForDwell(
  lonDeg: number,
  orbit: DraftOrbit,
  epochIso: string,
): number {
  const anomaly =
    lonDeg + gmstDegAt(epochIso) - orbit.raan_deg - orbit.argument_of_perigee_deg;
  return ((anomaly % 360) + 360) % 360;
}

/** Orbit sanity findings: warn, never block (unusual orbits are learning
 *  paths; only the physically broken gets flagged, in plain language).
 *  Altitude is body-relative already; the atmosphere check is Earth
 *  physics and must never fire for an airless body — a 100 km lunar orbit
 *  is a fine orbit, and a false warning is a false state display. */
export function orbitWarnings(orbit: DraftOrbit): string[] {
  const warnings: string[] = [];
  const low =
    orbit.shape_kind === "circular" ? orbit.altitude_km : orbit.perigee_altitude_km;
  if (low <= 0) {
    warnings.push(
      orbit.shape_kind === "circular"
        ? "orbit is below the surface"
        : "perigee is below the surface",
    );
  } else if (low < 160 && orbit.central_body === EARTH_BODY_REF) {
    warnings.push(
      orbit.shape_kind === "circular"
        ? "inside the upper atmosphere — rapid decay"
        : "perigee inside the upper atmosphere — rapid decay",
    );
  }
  if (
    orbit.shape_kind === "elliptical" &&
    orbit.apogee_altitude_km < orbit.perigee_altitude_km
  ) {
    warnings.push("apogee is below perigee — swap them");
  }
  return warnings;
}

export type SchedulingPresetKey = "leo-fast-handover" | "geo-longest-pass";

/** Allocator-wide scheduling fields: the resolver requires these to be
 *  UNIFORM across every ground node in the session (they configure the one
 *  allocator, not a node). Presets therefore never vary them — mixing
 *  presets across segments or per-site overrides stays resolvable by
 *  construction. Session-level control over these lands with session
 *  plumbing (S7). */
const ALLOCATOR_SCHEDULING = {
  // per_gs_rank (not selection_score): presets are made to be MIXED, and
  // their selection policies score on different scales (elevation degrees
  // vs remaining seconds). per_gs_rank arbitrates across policies — the
  // same choice the shipped multi-regime session makes.
  ranking_order: [
    "service_priority",
    "per_gs_rank",
    "satellite_ground_terminal_capacity",
    "lex_pair",
  ],
  mbb_preemption: "off",
  successor_abort_policy: "hard_release",
  cross_tenant_displacement: "off",
  // 1 is the only implemented value; larger waits are reserved extension
  // points the resolver rejects once access allocation engages.
  bbm_acquire_timeout_ticks: 1,
} as const;

/** Scheduling intent presets — dual literacy: the preset name carries the
 *  operational intent; selecting one writes the FULL explicit block the
 *  expert can read in the YAML pane. No hidden defaults. Presets differ
 *  only on per-node fields (see ALLOCATOR_SCHEDULING). */
export const SCHEDULING_PRESETS: Record<
  SchedulingPresetKey,
  { label: string; block: Record<string, unknown> }
> = {
  "leo-fast-handover": {
    label: "LEO fast handover — make-before-break",
    block: {
      selection_policy: { highest_elevation: {} },
      handover_policy: { hysteresis: { discount_factor: 1.1, mask_fade_range_deg: 3.0 } },
      handover_mode: "mbb",
      mbb_overlap_ticks: 30,
      mbb_reserve: 1,
      handover_concurrency: "one_at_a_time",
      ...ALLOCATOR_SCHEDULING,
    },
  },
  "geo-longest-pass": {
    label: "GEO longest pass — break-before-make",
    block: {
      selection_policy: { longest_remaining_pass: { lookahead_horizon_ticks: 600 } },
      handover_policy: { hard_release: {} },
      handover_mode: "bbm",
      mbb_overlap_ticks: 0,
      mbb_reserve: 0,
      handover_concurrency: "one_at_a_time",
      ...ALLOCATOR_SCHEDULING,
    },
  },
};

/** A site set placed by reference, plus the session-owned scheduling intent
 *  (scheduling is a SESSION concern — site-set documents never carry it). */
export interface RefGroundSet extends RefSegment {
  scheduling_preset: SchedulingPresetKey;
}

export function newRefGroundSet(ref: string, label: string): RefGroundSet {
  return { ...newRefSegment(ref, label), scheduling_preset: "leo-fast-handover" };
}

let groundCounter = 0;
let memberCounter = 0;

/** A blank ground segment: blank-first (defined sites arrive from the
 *  library, forks, or minting by paste). Stamp bases stagger per draft so
 *  two authored segments never collide by default — all editable. */
export function newDraftGroundSet(
  nodeRef: string,
  installed: Record<string, number>,
): DraftGroundSet {
  groundCounter += 1;
  return {
    segment_id: `ground-${groundCounter}`,
    display_name: `Ground segment ${groundCounter}`,
    members: [],
    stamp: {
      node_ref: nodeRef,
      installed,
      lan_base: `172.${20 + ((groundCounter - 1) % 12)}`,
      loopback_base: `10.${200 + ((groundCounter - 1) % 55)}`,
    },
    scheduling_preset: "leo-fast-handover",
    originated_ipv4: [],
    tags: [],
  };
}

/** Stamp-derived addressing for MINTED sites (mint index i). Applied once
 *  at creation and stored explicitly on the site — the site owns it after. */
export function stampLanPrefix(stamp: GroundStamp, index: number): string {
  return `${stamp.lan_base}.${index}.0/24`;
}

export function stampTerr0Address(stamp: GroundStamp, index: number): string {
  return `${stamp.lan_base}.${index}.1/24`;
}

export function stampLoopbackAddress(stamp: GroundStamp, index: number): string {
  return `${stamp.loopback_base}.0.${index + 1}/32`;
}

/** The grammar id a member answers to (override matching keys on it). */
export function memberSiteId(member: DraftGroundSite): string {
  return member.kind === "draft" && member.site ? member.site.site_id : member.site_id;
}

export interface ParsedSiteLine {
  name: string;
  lat_deg: number;
  lon_deg: number;
  alt_m: number;
}

/** Bulk site paste: one site per line, ``name, lat, lon[, alt_m]`` — commas
 *  or tabs (spreadsheet columns paste as tabs). Bad lines are reported,
 *  never silently dropped. */
export function parseSiteLines(text: string): { rows: ParsedSiteLine[]; errors: string[] } {
  const rows: ParsedSiteLine[] = [];
  const errors: string[] = [];
  for (const rawLine of text.split("\n")) {
    const line = rawLine.trim();
    if (!line) continue;
    const parts = line
      .split(/[\t,]/)
      .map((part) => part.trim())
      .filter((part) => part.length > 0);
    if (parts.length < 3) {
      errors.push(`"${line}" — expected: name, lat, lon`);
      continue;
    }
    const name = parts[0] ?? "";
    const lat = Number(parts[1]);
    const lon = Number(parts[2]);
    const alt = parts.length > 3 ? Number(parts[3]) : 0;
    if (!Number.isFinite(lat) || !Number.isFinite(lon) || !Number.isFinite(alt)) {
      errors.push(`"${line}" — lat, lon, alt must be numbers`);
      continue;
    }
    if (!identifier(name)) {
      errors.push(`"${line}" — name is empty after normalizing`);
      continue;
    }
    rows.push({ name, lat_deg: lat, lon_deg: lon, alt_m: alt });
  }
  return { rows, errors };
}

/** Mint full SITES from pasted rows using the segment's stamp — node model,
 *  installed mounts, and derived addressing are applied AT CREATION; each
 *  minted site owns its configuration afterwards. Mint indices continue
 *  from the count of existing draft members so addressing never collides
 *  within the segment. */
export function mintSiteMembers(
  draft: DraftGroundSet,
  rows: ParsedSiteLine[],
): DraftGroundSite[] {
  const start = draft.members.filter((member) => member.kind === "draft").length;
  return rows.map((row, offset) => {
    const index = start + offset;
    memberCounter += 1;
    const site: DraftSiteObject = {
      site_id: identifier(row.name),
      display_name: row.name,
      lat_deg: row.lat_deg,
      lon_deg: row.lon_deg,
      alt_m: row.alt_m,
      lan_ipv4: stampLanPrefix(draft.stamp, index),
      tags: [],
      nodes: [
        {
          node_id: "gw1",
          model_ref: draft.stamp.node_ref,
          installed: { ...draft.stamp.installed },
          lo0_ipv4: stampLoopbackAddress(draft.stamp, index),
          terr0_ipv4: stampTerr0Address(draft.stamp, index),
        },
      ],
    };
    return {
      member_id: `member-${memberCounter}`,
      kind: "draft" as const,
      ref: null,
      site_id: site.site_id,
      label: row.name,
      summary: null,
      site,
      scheduling_override: null,
    };
  });
}

/** A defined site placed by reference — full fidelity, its nodes travel
 *  with it. site_id must be the document's grammar id (read at add time). */
export function refGroundMember(
  ref: string,
  siteId: string,
  label: string,
  summary: string | null,
): DraftGroundSite {
  memberCounter += 1;
  return {
    member_id: `member-${memberCounter}`,
    kind: "ref",
    ref,
    site_id: siteId,
    label,
    summary,
    site: null,
    scheduling_override: null,
  };
}

/** Wrap an authored site as a segment member. */
export function draftGroundMember(site: DraftSiteObject): DraftGroundSite {
  memberCounter += 1;
  return {
    member_id: `member-${memberCounter}`,
    kind: "draft",
    ref: null,
    site_id: site.site_id,
    label: site.display_name,
    summary: null,
    site,
    scheduling_override: null,
  };
}

/** A blank site for from-scratch authoring (Library → Sites → + new). */
export function newDraftSiteObject(nodeRef: string, installed: Record<string, number>): DraftSiteObject {
  return {
    site_id: "my-site",
    display_name: "My site",
    lat_deg: 0,
    lon_deg: 0,
    alt_m: 0,
    lan_ipv4: "172.20.0.0/24",
    tags: [],
    nodes: [
      {
        node_id: "gw1",
        model_ref: nodeRef,
        installed,
        lo0_ipv4: "10.200.0.1/32",
        terr0_ipv4: "172.20.0.1/24",
      },
    ],
  };
}

/** Fork a site document into an editable draft — full fidelity: every node,
 *  its installed mounts, and its addressing carry over. Constructs the
 *  editor cannot represent (non-Earth frames, inline node models, payload
 *  installs, IPv6-only addressing) are refused loudly, never dropped. */
export function draftSiteFromDocument(document: Record<string, unknown>): DraftSiteObject {
  const site = (document as { site?: Record<string, unknown> }).site;
  if (!site) throw new Error("not a site document");
  const siteId = String(site.id ?? "");
  const frame = (site.frame ?? {}) as { body_fixed?: { body?: unknown } };
  const body = frame.body_fixed?.body;
  if (typeof body !== "string" || !body.includes("bodies/earth")) {
    throw new Error(
      `site ${siteId}: only Earth surface sites are editable yet — multi-body ground authoring is pending`,
    );
  }
  const location = (site.location ?? null) as Record<string, unknown> | null;
  if (!location) throw new Error(`site ${siteId}: non-surface sites are not editable yet`);
  const lan = (site.lan ?? {}) as { ipv4?: unknown };
  if (typeof lan.ipv4 !== "string") {
    throw new Error(`site ${siteId}: IPv6-only sites are not editable yet`);
  }
  const nodes = ((site.nodes as Record<string, unknown>[] | undefined) ?? []).map((node) => {
    const nodeId = String(node.id ?? "gw1");
    if (typeof node.model !== "string") {
      throw new Error(`site ${siteId}/${nodeId}: inline node models are not editable yet`);
    }
    const payloads = (node.payloads ?? {}) as Record<string, unknown>;
    if (Object.keys(payloads).length > 0) {
      throw new Error(`site ${siteId}/${nodeId}: payload installs are not editable yet`);
    }
    const terminals =
      (node.terminals as Record<string, { installed_count?: number }> | undefined) ?? {};
    const interfaces = (node.interfaces ?? {}) as {
      lo0?: { ipv4?: unknown };
      terr0?: { ipv4?: unknown };
    };
    if (typeof interfaces.lo0?.ipv4 !== "string" || typeof interfaces.terr0?.ipv4 !== "string") {
      throw new Error(`site ${siteId}/${nodeId}: IPv6-only interfaces are not editable yet`);
    }
    return {
      node_id: nodeId,
      model_ref: node.model,
      installed: Object.fromEntries(
        Object.entries(terminals).map(([mount, install]) => [
          mount,
          Number(install.installed_count ?? 1),
        ]),
      ),
      lo0_ipv4: interfaces.lo0.ipv4,
      terr0_ipv4: interfaces.terr0.ipv4,
    };
  });
  if (nodes.length === 0) throw new Error(`site ${siteId} has no nodes`);
  return {
    site_id: siteId,
    display_name: String(site.display_name ?? siteId),
    lat_deg: Number(location.lat_deg ?? 0),
    lon_deg: Number(location.lon_deg ?? 0),
    alt_m: Number(location.alt_m ?? 0),
    lan_ipv4: lan.ipv4,
    tags: ((site.tags as unknown[] | undefined) ?? []).map(String),
    nodes,
  };
}

/** Serialize a site draft to the grammar's Site object (unwrapped form) —
 *  the SAME builder feeds session emission and save-to-library. */
export function siteObjectFromDraft(site: DraftSiteObject): Record<string, unknown> {
  return {
    id: identifier(site.site_id),
    display_name: site.display_name,
    lan: { ipv4: site.lan_ipv4 },
    ...(site.tags.length > 0 ? { tags: site.tags.map(identifier) } : {}),
    nodes: site.nodes.map((node) => ({
      id: identifier(node.node_id) || "gw1",
      model: node.model_ref,
      payloads: {},
      terminals: Object.fromEntries(
        Object.entries(node.installed).map(([mount, count]) => [
          mount,
          { installed_count: count },
        ]),
      ),
      interfaces: {
        lo0: { ipv4: node.lo0_ipv4 },
        terr0: { ipv4: node.terr0_ipv4 },
      },
    })),
    frame: { body_fixed: { body: "nodalarc:bodies/earth.yaml" } },
    location: { lat_deg: site.lat_deg, lon_deg: site.lon_deg, alt_m: site.alt_m },
  };
}

/** Ground sanity findings: warn, never block (a polar site under an
 *  equatorial shell is a learning path; only the structurally broken and
 *  the off-the-map get flagged, in plain language). */
export function groundWarnings(draft: DraftGroundSet): string[] {
  const warnings: string[] = [];
  const validBase = (base: string): boolean => {
    const octets = base.split(".");
    return (
      octets.length === 2 &&
      octets.every((octet) => {
        const value = Number(octet);
        return Number.isInteger(value) && value >= 0 && value <= 255 && octet !== "";
      })
    );
  };
  if (!validBase(draft.stamp.lan_base)) {
    warnings.push(`lan base "${draft.stamp.lan_base}" — expected two octets, like 172.20`);
  }
  if (!validBase(draft.stamp.loopback_base)) {
    warnings.push(
      `loopback base "${draft.stamp.loopback_base}" — expected two octets, like 10.200`,
    );
  }
  const seenIds = new Set<string>();
  const seenLans = new Set<string>();
  for (const member of draft.members) {
    const id = memberSiteId(member);
    if (seenIds.has(id)) {
      warnings.push(`duplicate site id "${id}" — sites are places and exist once`);
    }
    seenIds.add(id);
    const site = member.site;
    if (!site) continue;
    if (seenLans.has(site.lan_ipv4)) {
      warnings.push(`${site.display_name}: lan ${site.lan_ipv4} is already used in this segment`);
    }
    seenLans.add(site.lan_ipv4);
    if (Math.abs(site.lat_deg) > 90) {
      warnings.push(`${site.display_name}: latitude ${site.lat_deg} is off the map (±90)`);
    }
    if (Math.abs(site.lon_deg) > 180) {
      warnings.push(`${site.display_name}: longitude ${site.lon_deg} is off the map (±180)`);
    }
  }
  return warnings;
}

/** Fork a site-set document into an editable ground draft — customize-a-
 *  placed-block for ground. A site set is a COMBINATION of defined sites:
 *  referenced members stay references at full fidelity (their nodes travel
 *  with them); inline members become editable site drafts. The stamp seeds
 *  from the first readable node so pasting new sites keeps working. */
export function draftGroundSetFromDocuments(
  siteSetDocument: Record<string, unknown>,
  siteEntries: { ref: string | null; document: Record<string, unknown> }[],
): DraftGroundSet {
  const siteSet = (siteSetDocument as { site_set?: Record<string, unknown> }).site_set;
  if (!siteSet) throw new Error("not a site_set document");
  const members: DraftGroundSite[] = [];
  let stampNodeRef: string | null = null;
  let stampInstalled: Record<string, number> | null = null;
  for (const entry of siteEntries) {
    const site = (entry.document as { site?: Record<string, unknown> }).site;
    if (!site) throw new Error("site set contains a non-site entry");
    const siteId = String(site.id ?? "");
    const label = String(site.display_name ?? siteId);
    if (entry.ref !== null) {
      members.push(refGroundMember(entry.ref, siteId, label, null));
    } else {
      members.push(draftGroundMember(draftSiteFromDocument(entry.document)));
    }
    if (stampNodeRef === null) {
      const nodes = (site.nodes as Record<string, unknown>[] | undefined) ?? [];
      const [node] = nodes;
      if (node && typeof node.model === "string") {
        stampNodeRef = node.model;
        const terminals =
          (node.terminals as Record<string, { installed_count?: number }> | undefined) ?? {};
        stampInstalled = Object.fromEntries(
          Object.entries(terminals).map(([mount, install]) => [
            mount,
            Number(install.installed_count ?? 1),
          ]),
        );
      }
    }
  }
  if (members.length === 0) throw new Error("site set has no readable sites");
  const forked = newDraftGroundSet(stampNodeRef ?? "", stampInstalled ?? {});
  return {
    ...forked,
    display_name: `${String(siteSet.display_name ?? siteSet.id)} (custom)`,
    members,
    tags: ((siteSet.tags as unknown[] | undefined) ?? []).map(String),
  };
}

/** Serialize a ground draft to the grammar's SiteSet object (unwrapped form)
 *  — the SAME builder feeds session emission (inline from_site_set) and
 *  save-to-library (wrapped by the save path). Scheduling, originated
 *  prefixes, and overrides are SESSION concerns and stay out of it. */
export function siteSetObjectFromDraft(
  draft: DraftGroundSet,
  id: string,
): Record<string, unknown> {
  return {
    id,
    display_name: draft.display_name,
    sites: draft.members.map((member) =>
      member.kind === "ref" && member.ref !== null
        ? member.ref
        : { site: siteObjectFromDraft(member.site as DraftSiteObject) },
    ),
    reference: "session-builder-draft",
  };
}

/** Serialize the workspace to the session grammar (the ONE artifact). *//** Every placed segment a link rule can select, with its kind — the role
 *  defaults key on kinds (space⟲space=isl, space↔space=crosslink,
 *  ground↔space=access). */
export interface PlacedSegment {
  segment_id: string;
  label: string;
  kind: "space" | "ground";
}

export function placedSegments(workspace: Workspace): PlacedSegment[] {
  return [
    ...workspace.space_refs.map((placed) => ({
      segment_id: placed.segment_id,
      label: placed.label,
      kind: "space" as const,
    })),
    ...workspace.space.map((draft) => ({
      segment_id: draft.segment_id,
      label: draft.display_name,
      kind: "space" as const,
    })),
    ...workspace.ground_refs.map((placed) => ({
      segment_id: placed.segment_id,
      label: placed.label,
      kind: "ground" as const,
    })),
    ...workspace.ground.map((draft) => ({
      segment_id: draft.segment_id,
      label: draft.display_name,
      kind: "ground" as const,
    })),
  ];
}

let linkCounter = 0;

/** Connect two placed segments with the DOCUMENTED role defaults: the same
 *  space segment twice = an ISL fabric (nearest-2 optical); two different
 *  space segments = optical crosslink; ground↔space = RF access with a 25°
 *  mask on the ground side. All values are seeds the user then owns. */
export function defaultLinkRule(
  a: PlacedSegment,
  b: PlacedSegment,
  existing: DraftLinkRule[] = [],
): DraftLinkRule {
  linkCounter += 1;
  const endpoint = (segment: PlacedSegment): DraftLinkEndpoint => ({
    segment_id: segment.segment_id,
    tag: null,
    role: "access",
    medium: "rf",
    min_elevation_deg: null,
  });
  // Ground endpoint first (the shipped-session convention).
  const [first, second] = a.kind === "ground" || b.kind !== "ground" ? [a, b] : [b, a];
  const rule: DraftLinkRule = {
    rule_id: `link-${linkCounter}`,
    label: "",
    enabled: true,
    a: endpoint(first),
    b: endpoint(second),
    topology_mode: "visible_candidates",
    topology_n: 2,
    max_range_km: null,
  };
  if (first.kind === "space" && second.kind === "space") {
    const isl = first.segment_id === second.segment_id;
    const role = isl ? ("isl" as const) : ("crosslink" as const);
    rule.a = { ...rule.a, role, medium: "optical" };
    rule.b = { ...rule.b, role, medium: "optical" };
    if (isl) rule.topology_mode = "nearest_n";
    rule.label = isl ? `${first.label} mesh` : `${first.label} to ${second.label}`;
  } else {
    rule.a = { ...rule.a, min_elevation_deg: first.kind === "ground" ? 25 : null };
    rule.label = `${first.label} to ${second.label}`;
  }
  // Rule ids must be unique in the session — uniquify the seeded name so a
  // second connect never trips the duplicate-id wall before the rename.
  // identifier() truncates to 48 chars, so compare TRUNCATED ids and keep
  // the base short enough that the numeric suffix survives truncation.
  const taken = new Set(existing.map((r) => identifier(r.label) || r.rule_id));
  if (taken.has(identifier(rule.label))) {
    const base = rule.label.slice(0, 40);
    let n = 2;
    while (taken.has(identifier(`${base} ${n}`)) && n < 1000) n += 1;
    rule.label = `${base} ${n}`;
  }
  return rule;
}

/** Link sanity findings: warn, never block. The resolver's verdict on the
 *  emitted rules arrives verbatim through the resolve-check. */
export function linkWarnings(workspace: Workspace): string[] {
  const warnings: string[] = [];
  const placed = new Map(placedSegments(workspace).map((s) => [s.segment_id, s]));
  const seenIds = new Set<string>();
  for (const rule of workspace.links) {
    const id = identifier(rule.label) || rule.rule_id;
    if (seenIds.has(id)) {
      warnings.push(`two link rules named "${id}" — rename one`);
    }
    seenIds.add(id);
    for (const endpoint of [rule.a, rule.b]) {
      if (!placed.has(endpoint.segment_id)) {
        warnings.push(
          `${rule.label || id}: segment "${endpoint.segment_id}" is no longer in the session`,
        );
      }
    }
    const a = placed.get(rule.a.segment_id);
    const b = placed.get(rule.b.segment_id);
    if (a?.kind === "ground" && b?.kind === "ground") {
      warnings.push(
        `${rule.label || id}: ground-to-ground links are terrestrial network territory — that arrives with routing, not link rules`,
      );
    }
  }
  return warnings;
}

let domainCounter = 0;
let boundaryCounter = 0;

/** A new domain seeds over EVERY placed segment — one IGP over the whole
 *  world is the honest default; members are then removed per segment. */
export function defaultRoutingDomain(workspace: Workspace): DraftRoutingDomain {
  domainCounter += 1;
  return {
    domain_id: `domain-${domainCounter}`,
    label: `domain ${domainCounter}`,
    protocol: "isis",
    member_segment_ids: placedSegments(workspace).map((s) => s.segment_id),
    hello_interval_s: null,
    hold_interval_s: null,
  };
}

export function defaultBoundary(workspace: Workspace): DraftBoundary {
  boundaryCounter += 1;
  const [firstRule] = workspace.links;
  const [fromDomain, toDomain] = workspace.routing_domains;
  return {
    boundary_id: `boundary-${boundaryCounter}`,
    over_rule_id: firstRule?.rule_id ?? "",
    adapter: "static_ip",
    from_domain_id: fromDomain?.domain_id ?? "",
    to_domain_id: toDomain?.domain_id ?? fromDomain?.domain_id ?? "",
    export_node_loopbacks: true,
  };
}

/** The grammar id a link rule serializes under (boundaries key on it). */
export function emittedRuleId(rule: DraftLinkRule): string {
  return identifier(rule.label) || rule.rule_id;
}

export function emittedDomainId(domain: DraftRoutingDomain): string {
  return identifier(domain.label) || domain.domain_id;
}

/** Routing sanity findings: warn, never block — the resolver's verdict on
 *  the emitted routing block arrives verbatim through the resolve-check. */
export function routingWarnings(workspace: Workspace): string[] {
  const warnings: string[] = [];
  const placed = new Set(placedSegments(workspace).map((s) => s.segment_id));
  const domainIds = new Set<string>();
  for (const domain of workspace.routing_domains) {
    const id = emittedDomainId(domain);
    if (domainIds.has(id)) warnings.push(`two routing domains named "${id}" — rename one`);
    domainIds.add(id);
    if (domain.member_segment_ids.length === 0) {
      warnings.push(`${domain.label}: no member segments — the resolver requires at least one`);
    }
    for (const member of domain.member_segment_ids) {
      if (!placed.has(member)) {
        warnings.push(`${domain.label}: segment "${member}" is no longer in the session`);
      }
    }
    if (
      domain.hello_interval_s !== null &&
      domain.hold_interval_s !== null &&
      domain.hold_interval_s <= domain.hello_interval_s
    ) {
      warnings.push(`${domain.label}: hold must exceed hello`);
    }
  }
  const ruleIds = new Set(workspace.links.map((rule) => rule.rule_id));
  const draftDomainIds = new Set(workspace.routing_domains.map((d) => d.domain_id));
  for (const boundary of workspace.boundaries) {
    if (!ruleIds.has(boundary.over_rule_id)) {
      warnings.push("a boundary rides a link rule that is no longer in the session");
    }
    if (
      !draftDomainIds.has(boundary.from_domain_id) ||
      !draftDomainIds.has(boundary.to_domain_id)
    ) {
      warnings.push("a boundary references a routing domain that no longer exists");
    } else if (boundary.from_domain_id === boundary.to_domain_id) {
      warnings.push("a boundary must exchange between two DIFFERENT domains");
    }
  }
  return warnings;
}

/** One authoring gap: what's missing/broken and which editor owns it.
 *  target=null means the gap is about something not yet created. */
export interface CompletenessFinding {
  message: string;
  target:
    | { kind: "session" }
    | { kind: "segment"; id: string }
    | { kind: "ground"; id: string }
    | { kind: "link"; id: string }
    | null;
}

/** The completeness rail's source: OBJECT-level authoring gaps with
 *  click-to-jump targets. Session-level structure (no segments, no rules,
 *  no domains) lives in the session-anatomy guide, which is always on
 *  screen — saying it twice would just be two surfaces to keep agreeing.
 *  Warnings that already render inline on their owning object are
 *  AGGREGATED as counts, not duplicated. Empty result = nothing to say
 *  (the resolve status is the green, never this rail). */
export function completenessFindings(workspace: Workspace): CompletenessFinding[] {
  const findings: CompletenessFinding[] = [];
  for (const draft of workspace.ground) {
    if (draft.members.length === 0) {
      findings.push({
        message: `${draft.display_name}: no sites yet`,
        target: { kind: "ground", id: draft.segment_id },
      });
    }
  }
  for (const draft of workspace.space) {
    const count = orbitWarnings(draft.orbit).length;
    if (count > 0) {
      findings.push({
        message: `${draft.display_name}: ${count} orbit ${count === 1 ? "finding" : "findings"}`,
        target: { kind: "segment", id: draft.segment_id },
      });
    }
  }
  for (const draft of workspace.ground) {
    const count = groundWarnings(draft).length;
    if (count > 0) {
      findings.push({
        message: `${draft.display_name}: ${count} ground ${count === 1 ? "finding" : "findings"}`,
        target: { kind: "ground", id: draft.segment_id },
      });
    }
  }
  const linkCount = linkWarnings(workspace).length;
  if (linkCount > 0) {
    const first = workspace.links[0];
    findings.push({
      message: `${linkCount} link ${linkCount === 1 ? "finding" : "findings"}`,
      target: first ? { kind: "link", id: first.rule_id } : null,
    });
  }
  const routingCount = routingWarnings(workspace).length;
  if (routingCount > 0) {
    findings.push({
      message: `${routingCount} routing ${routingCount === 1 ? "finding" : "findings"}`,
      target: null,
    });
  }
  return findings;
}

/** After restoring an autosaved workspace, module counters restart at zero
 *  and freshly minted ids would collide with restored ones. Reseed every
 *  counter past the highest id the workspace carries. */
export function reseedCounters(workspace: Workspace): void {
  const bump = (values: string[], prefix: string): number => {
    let max = 0;
    for (const value of values) {
      const match = value.match(new RegExp(`^${prefix}-(\\d+)$`));
      if (match) max = Math.max(max, Number(match[1]));
    }
    return max;
  };
  draftCounter = Math.max(
    draftCounter,
    bump(workspace.space.map((d) => d.segment_id), "space"),
  );
  refCounter = Math.max(
    refCounter,
    bump(
      [...workspace.space_refs, ...workspace.ground_refs].map((r) => r.segment_id),
      "lib",
    ),
  );
  groundCounter = Math.max(
    groundCounter,
    bump(workspace.ground.map((d) => d.segment_id), "ground"),
  );
  memberCounter = Math.max(
    memberCounter,
    bump(
      workspace.ground.flatMap((d) => d.members.map((m) => m.member_id)),
      "member",
    ),
  );
  linkCounter = Math.max(
    linkCounter,
    bump(workspace.links.map((r) => r.rule_id), "link"),
  );
  domainCounter = Math.max(
    domainCounter,
    bump(workspace.routing_domains.map((d) => d.domain_id), "domain"),
  );
  boundaryCounter = Math.max(
    boundaryCounter,
    bump(workspace.boundaries.map((b) => b.boundary_id), "boundary"),
  );
}

/** Serialize the workspace to the session grammar (the ONE artifact). */
export function toSessionDocument(workspace: Workspace): Record<string, unknown> {
  const refSegments: unknown[] = workspace.space_refs.map((placed) => ({
    id: identifier(placed.segment_id),
    source: placed.ref,
  }));
  const segments: unknown[] = workspace.space.map((draft) => ({
    id: identifier(draft.segment_id),
    source: {
      constellation: {
        id: identifier(`${workspace.name}-${draft.segment_id}`),
        display_name: draft.display_name,
        node: draft.node_draft ? nodeObjectFromDraft(draft.node_draft) : draft.node_ref,
        orbit: {
          id: identifier(`${draft.segment_id}-orbit`),
          central_body: draft.orbit.central_body,
          epoch: workspace.start_time,
          shape:
            draft.orbit.shape_kind === "circular"
              ? { altitude_km: draft.orbit.altitude_km }
              : {
                  perigee_altitude_km: draft.orbit.perigee_altitude_km,
                  apogee_altitude_km: draft.orbit.apogee_altitude_km,
                },
          orientation: {
            inclination_deg: draft.orbit.inclination_deg,
            raan_deg: draft.orbit.raan_deg,
            argument_of_perigee_deg: draft.orbit.argument_of_perigee_deg,
          },
          phase: { mean_anomaly_deg: draft.orbit.mean_anomaly_deg },
          propagator: draft.orbit.propagator,
          reference: "session-builder-draft",
        },
        planes: {
          count: draft.planes,
          raan_spacing_deg: draft.raan_spacing_deg,
        },
        slots_per_plane: draft.slots_per_plane,
        phasing: {
          mode: "evenly_spaced_mean_anomaly",
          phase_offset_deg: draft.phase_offset_deg,
        },
        node_tags: [{ tag: "all" }],
        reference: "session-builder-draft",
      },
    },
  }));

  const groundRefSegments: unknown[] = workspace.ground_refs.map((placed) => ({
    id: identifier(placed.segment_id),
    placement: { from_site_set: placed.ref },
    apply: { scheduling: SCHEDULING_PRESETS[placed.scheduling_preset].block },
  }));

  const groundSegments: unknown[] = workspace.ground.map((draft) => {
    const overrides = draft.members
      .filter((member) => member.scheduling_override !== null)
      .map((member) => ({
        match: { site: identifier(memberSiteId(member)) },
        scheduling:
          SCHEDULING_PRESETS[member.scheduling_override as SchedulingPresetKey].block,
      }));
    return {
      id: identifier(draft.segment_id),
      display_name: draft.display_name,
      placement: {
        from_site_set: {
          site_set: siteSetObjectFromDraft(
            draft,
            identifier(`${workspace.name}-${draft.segment_id}`),
          ),
        },
      },
      apply: {
        scheduling: SCHEDULING_PRESETS[draft.scheduling_preset].block,
        ...(draft.originated_ipv4.length > 0
          ? { originated_prefixes: { ipv4: draft.originated_ipv4 } }
          : {}),
        ...(draft.tags.length > 0 ? { tags: draft.tags.map(identifier) } : {}),
      },
      ...(overrides.length > 0 ? { overrides } : {}),
    };
  });

  const linkRules: unknown[] = workspace.links.map((rule) => ({
    id: identifier(rule.label) || rule.rule_id,
    ...(rule.enabled ? {} : { enabled: false }),
    topology:
      rule.topology_mode === "nearest_n"
        ? { mode: "nearest_n", n: rule.topology_n }
        : { mode: rule.topology_mode },
    endpoints: [rule.a, rule.b].map((endpoint) => ({
      select: endpoint.tag
        ? {
            all: [
              { segment: identifier(endpoint.segment_id) },
              { tag: identifier(endpoint.tag) },
            ],
          }
        : { segment: identifier(endpoint.segment_id) },
      terminal: { all: [{ role: endpoint.role }, { medium: endpoint.medium }] },
      ...(endpoint.min_elevation_deg !== null
        ? { min_elevation_deg: endpoint.min_elevation_deg }
        : {}),
    })),
    ...(rule.max_range_km !== null
      ? { constraints: { max_range_km: rule.max_range_km } }
      : {}),
  }));

  const domains: unknown[] = workspace.routing_domains.map((domain) => ({
    id: emittedDomainId(domain),
    protocol: domain.protocol,
    selectors:
      domain.member_segment_ids.length === 1
        ? [{ segment: identifier(domain.member_segment_ids[0] as string) }]
        : [
            {
              any: domain.member_segment_ids.map((member) => ({
                segment: identifier(member),
              })),
            },
          ],
    ...(domain.protocol === "isis" || domain.protocol === "ospf"
      ? { area_assignment: { strategy: "flat" } }
      : {}),
    ...(domain.hello_interval_s !== null && domain.hold_interval_s !== null
      ? {
          timers: {
            hello_interval_s: domain.hello_interval_s,
            hold_interval_s: domain.hold_interval_s,
          },
        }
      : {}),
  }));

  const domainById = new Map(workspace.routing_domains.map((d) => [d.domain_id, d]));
  const ruleById = new Map(workspace.links.map((r) => [r.rule_id, r]));
  const boundaries: unknown[] = workspace.boundaries.map((boundary) => {
    const fromDomain = domainById.get(boundary.from_domain_id);
    const toDomain = domainById.get(boundary.to_domain_id);
    const overRule = ruleById.get(boundary.over_rule_id);
    const exchange = (from: string, to: string) => ({
      from,
      to,
      prefixes: { aggregate_of: "originated" },
      export_node_loopbacks: boundary.export_node_loopbacks,
      install_via: "peer_loopback",
    });
    const fromId = fromDomain ? emittedDomainId(fromDomain) : boundary.from_domain_id;
    const toId = toDomain ? emittedDomainId(toDomain) : boundary.to_domain_id;
    return {
      over: overRule ? emittedRuleId(overRule) : boundary.over_rule_id,
      adapter: boundary.adapter,
      export: [exchange(fromId, toId), exchange(toId, fromId)],
    };
  });

  return {
    session: { name: identifier(workspace.name) || "untitled-session" },
    segments: [...refSegments, ...segments, ...groundRefSegments, ...groundSegments],
    ...(domains.length > 0
      ? {
          routing: {
            domains,
            ...(boundaries.length > 0 ? { boundaries } : {}),
          },
        }
      : {}),
    ...(linkRules.length > 0
      ? {
          link_rules: linkRules,
          simulation: {
            candidate_limits: {
              max_pairs_per_rule: workspace.max_pairs_per_rule,
              max_pairs_per_tick: workspace.max_pairs_per_tick,
            },
          },
        }
      : {}),
    time: {
      start_time: workspace.start_time,
      step_seconds: workspace.step_seconds,
      compression: workspace.compression,
    },
    // Orbits beyond Earth need body frames from a kernel manifest — the
    // resolver refuses a non-Earth session without one.
    ...(usesNonEarthBodies(workspace) ? { ephemeris: DE440S_EPHEMERIS } : {}),
  };
}
