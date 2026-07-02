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

export interface DraftConstellation {
  segment_id: string;
  display_name: string;
  node_ref: string;
  orbit: DraftOrbit;
  planes: number;
  raan_spacing_deg: number;
  slots_per_plane: number;
  phase_offset_deg: number;
}

export interface Workspace {
  name: string;
  space: DraftConstellation[];
  /** Shipped site-set reference; ground authoring proper lands in S4. */
  ground_site_set_ref: string | null;
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
    orbit: defaultDraftOrbit(),
    planes: 3,
    raan_spacing_deg: 60,
    slots_per_plane: 8,
    phase_offset_deg: 0,
  };
}

export function newWorkspace(name: string): Workspace {
  return {
    name: identifier(name) || "untitled-session",
    space: [],
    ground_site_set_ref: null,
    start_time: "2026-06-08T00:00:00Z",
  };
}

/** Normalize a display string into a grammar Identifier. */
export function identifier(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9-]+/g, "-")
    .replace(/^-+|-+$/g, "")
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
  const segments: unknown[] = workspace.space.map((draft) => ({
    id: identifier(draft.segment_id),
    source: {
      constellation: {
        id: identifier(`${workspace.name}-${draft.segment_id}`),
        display_name: draft.display_name,
        node: draft.node_ref,
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

  if (workspace.ground_site_set_ref) {
    segments.push({
      id: "ground",
      placement: { from_site_set: workspace.ground_site_set_ref },
      apply: { scheduling: DEFAULT_GROUND_SCHEDULING },
    });
  }

  return {
    session: { name: identifier(workspace.name) || "untitled-session" },
    segments,
    time: { start_time: workspace.start_time, step_seconds: 1, compression: 1 },
  };
}
