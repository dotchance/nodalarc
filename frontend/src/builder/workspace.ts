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
 *  Client-side until save (owner decision): the workspace lives in browser
 *  state; resolve-check posts the serialized document and the world renders
 *  from the resolver's expansion, never from a builder-local one.
 */

export interface DraftOrbit {
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

export interface DraftSite {
  site_id: string;
  display_name: string;
  lat_deg: number;
  lon_deg: number;
  alt_m: number;
  tags: string[];
}

/** An editable ground segment: authored sites with a template node model and
 *  derived, deterministic, VISIBLE addressing. Per-site node variation is a
 *  registered grammar delta (template+override, #5) — this slice applies one
 *  model to every site, which is the current shipped-catalog pattern too. */
export interface DraftGroundSet {
  node_ref: string;
  /** Installed count per mount id, seeded from the node model's mounts. */
  installed: Record<string, number>;
  sites: DraftSite[];
  /** IPv4 base for site LANs: site i gets base.<i>.0/24, terr0 .1. */
  lan_base: string;
  /** IPv4 base for node loopbacks: site i gets base.0.<i+1>/32. */
  loopback_base: string;
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

export interface Workspace {
  name: string;
  space: DraftConstellation[];
  /** Library constellations placed by reference (use-this-block). */
  space_refs: RefSegment[];
  /** Shipped site-set reference; ``ground_draft`` overrides it when set. */
  ground_site_set_ref: string | null;
  ground_draft: DraftGroundSet | null;
  start_time: string;
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

export function defaultDraftOrbit(): DraftOrbit {
  return {
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
    ground_site_set_ref: null,
    ground_draft: null,
    start_time: "2026-06-08T00:00:00Z",
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

/** Orbit sanity findings: warn, never block (unusual orbits are learning
 *  paths; only the physically broken gets flagged, in plain language). */
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
  } else if (low < 160) {
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

/** Scheduling intent presets — dual literacy: the preset name carries the
 *  operational intent; selecting one writes the FULL explicit block the
 *  expert can read in the YAML pane. No hidden defaults. */
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
      ranking_order: [
        "service_priority",
        "selection_score",
        "satellite_ground_terminal_capacity",
        "lex_pair",
      ],
      mbb_preemption: "off",
      successor_abort_policy: "hard_release",
      cross_tenant_displacement: "off",
      bbm_acquire_timeout_ticks: 1,
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
      ranking_order: [
        "service_priority",
        "selection_score",
        "satellite_ground_terminal_capacity",
        "lex_pair",
      ],
      mbb_preemption: "off",
      successor_abort_policy: "hard_release",
      cross_tenant_displacement: "off",
      bbm_acquire_timeout_ticks: 30,
    },
  },
};

// Ground segments need complete effective scheduling per node (resolver
// S-rules); until the S4 scheduling editor lands, drafts apply this explicit
// default block — visible in the YAML pane, not hidden.
const DEFAULT_GROUND_SCHEDULING = {
  selection_policy: { highest_elevation: {} },
  handover_policy: { hysteresis: { discount_factor: 1.1, mask_fade_range_deg: 3.0 } },
  handover_mode: "mbb",
  mbb_overlap_ticks: 30,
  mbb_reserve: 1,
  handover_concurrency: "one_at_a_time",
  ranking_order: [
    "service_priority",
    "selection_score",
    "satellite_ground_terminal_capacity",
    "lex_pair",
  ],
  mbb_preemption: "off",
  successor_abort_policy: "hard_release",
  cross_tenant_displacement: "off",
  bbm_acquire_timeout_ticks: 1,
};

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
          central_body: "nodalarc:bodies/earth.yaml",
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

  const allSegments = [...refSegments, ...segments];
  if (workspace.ground_site_set_ref) {
    allSegments.push({
      id: "ground",
      placement: { from_site_set: workspace.ground_site_set_ref },
      apply: { scheduling: DEFAULT_GROUND_SCHEDULING },
    });
  }

  return {
    session: { name: identifier(workspace.name) || "untitled-session" },
    segments: allSegments,
    time: { start_time: workspace.start_time, step_seconds: 1, compression: 1 },
  };
}
