// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** BuilderView surface contract: the on-screen world is the resolver's
 *  expansion of the current draft, never a stale frame or a false "resolves".
 *  These pins RENDER the view (Scene mocked to a null render, fetch stubbed,
 *  localStorage isolated) and assert the emitted DOM — the data-layer hook
 *  test alone cannot see the UI state fixes.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, render, screen, fireEvent, waitFor, cleanup, within } from "@testing-library/react";
import type { BuilderVisualDraftEnvelope } from "../generated/builderApi";
import { AUTHORING_FACTS } from "./fixtures/authoringFacts";

vi.mock("../../globe/r3f/Scene", () => ({ Scene: () => null }));
vi.mock("../../config", () => ({
  REST_URL: "http://test:8080",
  authHeaders: (extra?: Record<string, string>) => ({ ...extra }),
}));

const { BuilderView } = await import("../BuilderView");
const { resetCatalogStores } = await import("../useBuilderWorld");
const { catalogEarthFrame } = await import("../../sim/__tests__/bodyModelFixture");
const { newWorkspace } = await import("./fixtures/workspaceFixtures");
const {
  getRecoveryTabBinding,
  recoveryStorageKey,
  writeCatalogDraftRecovery,
  writeStructuredAutosave,
} = await import("../structuredDraftRecovery");

const builderRecoveryScope = () => ({
  authoringContextBinding: "builder-view-test-context",
  tabBinding: getRecoveryTabBinding(),
});

const PROPS = {
  active: true,
  colorMode: "regime",
  globeMode: "blue-marble",
  referenceFrame: "earth-fixed",
  showSatPaths: false,
  showIslLinks: false,
  showGroundLinks: false,
  showGroundTracks: false,
  showTrails: false,
  actionsRef: { current: null },
} as const;

function jsonResponse(body: unknown, ok = true, status = 200) {
  return { ok, status, json: () => Promise.resolve(body) };
}

function structuredVisualDraft(
  sessionName = "untitled-session",
): BuilderVisualDraftEnvelope {
  const workspace = {
    session_name: sessionName,
    display_name: null,
    description: null,
    space: [],
    space_refs: [],
    ground: [],
    ground_refs: [],
    links: [],
    routing_domains: [],
    boundaries: [],
    max_pairs_per_rule: 2_000,
    max_pairs_per_tick: 10_000,
    start_time: "2026-01-01T00:00:00Z",
    step_seconds: 1,
    compression: 1,
    projection_revision: 0,
    generic_scenes: [],
  };
  return {
    contract_version: 2,
    draft_revision: 0,
    projection_status: "applied",
    target_ref: `user:sessions/${sessionName}.yaml`,
    source_ref: null,
    expected_session_revision: null,
    catalog_documents: [],
    session_name_is_placeholder:
      sessionName === "untitled-session" || sessionName.startsWith("untitled-session-"),
    reserved_authoring_ids: [],
    session_yaml: `session:\n  name: ${sessionName}\nsegments: []\n`,
    authoring_workspace: workspace,
    applied_workspace: workspace,
    applied_revision: 0,
    applied_session: { session: { name: sessionName } },
  };
}

function compileResponse(
  request: Record<string, any>,
  preview: unknown = null,
  forcedBlocker: string | null = null,
  canonicalYaml: string | null = null,
) {
  const visualDraft = request.draft;
  const visualWorkspace = visualDraft.authoring_workspace ?? visualDraft.applied_workspace;
  const emptyGroundIndex = visualWorkspace?.ground?.findIndex(
    (ground: { members?: unknown[] }) => (ground.members ?? []).length === 0,
  );
  const incomplete = typeof emptyGroundIndex === "number" && emptyGroundIndex >= 0;
  const targetName =
    String(visualDraft.target_ref).split("/").pop()?.replace(/\.ya?ml$/, "") ?? "";
  const sessionName =
    visualWorkspace?.session_name ?? (targetName || "draft");
  const identityMismatch = sessionName !== targetName;
  const assemblyIssues = [
    ...(incomplete
      ? [
        {
          code: "builder.draft.site_set_sites_required",
          stage: "draft",
          severity: "error",
          message: "Ground sites requires at least one site",
          blocks: ["save", "deploy"],
          source_ref: visualDraft.target_ref,
          draft_path: `workspace.ground.${emptyGroundIndex}.members`,
        },
      ]
      : []),
    ...(identityMismatch
      ? [{
          code: "builder.draft.session_identity_mismatch",
          stage: "draft",
          severity: "error",
          message: `session.name ${sessionName} does not match target ${targetName}`,
          blocks: ["save", "deploy"],
          source_ref: visualDraft.target_ref,
          draft_path: "workspace.session_name",
        }]
      : []),
    ...(forcedBlocker
      ? [{
          code: "builder.draft.forced_test_blocker",
          stage: "draft",
          severity: "error",
          message: forcedBlocker,
          blocks: ["save", "deploy"],
          source_ref: visualDraft.target_ref,
          draft_path: "workspace.session_name",
        }]
      : []),
  ];
  const blocked = assemblyIssues.length > 0;
  const assembledDraft = {
    contract_version: 1,
    draft_revision: visualDraft.draft_revision,
    state: {
      session: { session: { name: sessionName }, segments: [] },
      catalog_documents: visualDraft.catalog_documents ?? [],
    },
  };
  const compileResult = {
    draft: assembledDraft,
    target_ref: visualDraft.target_ref,
    canonical_session_yaml:
      canonicalYaml ?? `session:\n  name: ${sessionName}\nsegments: []\n`,
    canonical_session_json: assembledDraft.state.session,
    dependency_closure: {
      entries: [],
      file_count: 0,
      total_bytes: 0,
      closure_digest: "dep-draft",
    },
    resolved_preview: blocked ? null : preview,
    digests: { document: "doc-draft", dependency: "dep-draft" },
    issues: assemblyIssues,
    save_verdict: {
      operation: "save",
      allowed: !blocked,
      blockers: assemblyIssues,
    },
    deploy_eligibility_after_save: {
      operation: "deploy",
      allowed: !blocked,
      blockers: assemblyIssues,
    },
  };
  return jsonResponse({
    visual_draft: visualDraft,
    assembled_draft: assembledDraft,
    save_request: { draft: assembledDraft, target_ref: visualDraft.target_ref },
    compile_result: compileResult,
    assembly_issues: assemblyIssues,
  });
}

function saveResponse(request: Record<string, any>) {
  const ref = String(request.target_ref);
  const sessionName = ref.split("/").pop()?.replace(/\.ya?ml$/, "") ?? "saved";
  const revision = `revision-${sessionName}`;
  const digests = { document: `document-${sessionName}`, dependency: `dependency-${sessionName}` };
  return jsonResponse({
    session: {
      ref,
      family: "sessions",
      canonical_yaml: `session:\n  name: ${sessionName}\nsegments: []\n`,
      canonical_json: { session: { name: sessionName }, segments: [] },
      content_digest: digests.document,
      revision,
    },
    digests,
    dependency_closure: {
      entries: [],
      file_count: 0,
      total_bytes: 0,
      closure_digest: digests.dependency,
    },
    deploy_verdict: {
      allowed: true,
      session_ref: ref,
      session_revision: revision,
      digests,
      blockers: [],
    },
    issues: [],
  });
}

const LEO_SCHEDULING = {
  selection_policy: { highest_elevation: {} },
  handover_policy: {
    hysteresis: { discount_factor: 1.1, mask_fade_range_deg: 3.0 },
  },
  handover_mode: "mbb",
  mbb_overlap_ticks: 30,
  mbb_reserve: 1,
  handover_concurrency: "one_at_a_time",
  ranking_order: [
    "service_priority",
    "per_gs_rank",
    "satellite_ground_terminal_capacity",
    "lex_pair",
  ],
  mbb_preemption: "off",
  successor_abort_policy: "hard_release",
  cross_tenant_displacement: "off",
  bbm_acquire_timeout_ticks: 1,
};
const GEO_SCHEDULING = {
  ...LEO_SCHEDULING,
  selection_policy: { longest_remaining_pass: { lookahead_horizon_ticks: 600 } },
  handover_policy: { hard_release: {} },
  handover_mode: "bbm",
  mbb_overlap_ticks: 0,
  mbb_reserve: 0,
};

function visualCommandResponse(request: Record<string, any>) {
  const draft = structuredClone(request.draft);
  const workspace = draft.authoring_workspace ?? draft.applied_workspace;
  const command = request.command;
  let affectedKind = "space";
  let affectedId = "space-1";
  let schedulingPreset: "leo-fast-handover" | "geo-longest-pass" | null | undefined;
  let notice: string | null = null;
  const groundInstallation = (nodeRef: string) => ({
    installed: { access_ka: nodeRef === SECOND_GROUND_NODE ? 64 : 8 },
    boresights: { access_ka: { mode: "local_vertical" } },
  });
  const refStem = (ref: string) =>
    (ref.split("/").pop() ?? ref).replace(/\.ya?ml$/, "");
  if (command.operation === "place_space_reference") {
    const number = workspace.space.length + workspace.space_refs.length + 1;
    affectedId = `space-${number}`;
    workspace.space_refs.push({
      segment_id: affectedId,
      source_ref: command.source_ref,
      label: refStem(command.source_ref),
    });
  } else if (command.operation === "place_ground_reference") {
    const number = workspace.ground.length + workspace.ground_refs.length + 1;
    affectedKind = "ground";
    affectedId = `ground-${number}`;
    workspace.ground_refs.push({
      segment_id: affectedId,
      site_set_ref: command.site_set_ref,
      label: refStem(command.site_set_ref),
      scheduling: structuredClone(LEO_SCHEDULING),
    });
    schedulingPreset = "leo-fast-handover";
  } else if (command.operation === "add_ground_site_reference") {
    let ground = workspace.ground.find(
      (candidate: { segment_id: string }) => candidate.segment_id === command.segment_id,
    );
    if (!ground) {
      const groundNumber = workspace.ground.length + workspace.ground_refs.length + 1;
      ground = {
        segment_id: `ground-${groundNumber}`,
        display_name: `Ground segment ${groundNumber}`,
        members: [],
        stamp: {
          node_ref: GROUND_NODE,
          installed: {},
          boresights: {},
          body: "nodalarc:bodies/earth.yaml",
          lan_base: `172.${19 + groundNumber}`,
          loopback_base: `10.${199 + groundNumber}`,
        },
        scheduling: structuredClone(LEO_SCHEDULING),
        originated_ipv4: [],
        tags: [],
      };
      workspace.ground.push(ground);
      schedulingPreset = "leo-fast-handover";
    }
    const number =
      workspace.ground.reduce(
        (count: number, candidate: { members: unknown[] }) => count + candidate.members.length,
        0,
      ) + 1;
    const siteId = refStem(command.site_ref);
    affectedKind = "ground_member";
    affectedId = `member-${number}`;
    ground.members.push({
      member_id: affectedId,
      kind: "ref",
      ref: command.site_ref,
      site_id: siteId,
      label: siteId,
      summary: null,
      site: null,
      scheduling_override: null,
    });
  } else if (command.operation === "author_inline_space_node") {
    affectedId = command.segment_id;
    const space = workspace.space.find(
      (candidate: { segment_id: string }) => candidate.segment_id === command.segment_id,
    );
    space.node_ref = null;
    space.node_draft = {
      id: `${command.segment_id}-node`,
      display_name: `${space.display_name} node`,
      forwarding: null,
      ethernet: [],
      terminals: [],
    };
  } else if (command.operation === "add_or_increment_node_terminal") {
    affectedId = command.segment_id;
    const space = workspace.space.find(
      (candidate: { segment_id: string }) => candidate.segment_id === command.segment_id,
    );
    const existing = space.node_draft.terminals.find(
      (candidate: { terminal_ref: string; role: string }) =>
        candidate.terminal_ref === command.terminal_ref && candidate.role === command.role,
    );
    if (existing) {
      existing.count += 1;
    } else {
      space.node_draft.terminals.push({
        mount_id: `${command.role}_0`,
        role: command.role,
        terminal_ref: command.terminal_ref,
        count: 1,
        boresight: command.role === "access" ? { mode: "nadir" } : null,
      });
    }
  } else if (command.operation === "set_node_terminal_role") {
    affectedId = command.segment_id;
    const space = workspace.space.find(
      (candidate: { segment_id: string }) => candidate.segment_id === command.segment_id,
    );
    const mount = space.node_draft.terminals.find(
      (candidate: { mount_id: string }) => candidate.mount_id === command.mount_id,
    );
    mount.role = command.role;
    mount.boresight = command.role === "access" ? { mode: "nadir" } : null;
  } else if (command.operation === "add_generated_space") {
    const number = workspace.space.length + 1;
    affectedId = `space-${number}`;
    workspace.space.push({
      segment_id: affectedId,
      display_name: `Constellation ${number}`,
      node_ref: command.node_ref ?? "nodalarc:nodes/space/relay.yaml",
      node_draft: null,
      orbit: {
        central_body: "nodalarc:bodies/earth.yaml",
        shape_kind: "circular",
        altitude_km: 550,
        perigee_altitude_km: 550,
        apogee_altitude_km: 550,
        inclination_deg: 53,
        raan_deg: 0,
        argument_of_perigee_deg: 0,
        mean_anomaly_deg: 0,
        propagator: "j2_mean_elements",
      },
      planes: command.phasing_mode === "evenly_spaced_mean_anomaly" ? 1 : 3,
      raan_spacing_deg: command.phasing_mode === "evenly_spaced_mean_anomaly" ? 360 : 120,
      slots_per_plane: 8,
      phasing_mode: command.phasing_mode,
      phase_offset_deg: command.phasing_mode === "evenly_spaced_mean_anomaly" ? 0 : 15,
    });
  } else if (command.operation === "set_space_population") {
    affectedId = command.segment_id;
    const space = workspace.space.find(
      (candidate: { segment_id: string }) => candidate.segment_id === command.segment_id,
    );
    let phasingMode = command.phasing_mode ?? space.phasing_mode;
    let planes = command.planes ?? space.planes;
    const slotsPerPlane = command.slots_per_plane ?? space.slots_per_plane;
    if (command.phasing_mode) {
      planes = phasingMode === "evenly_spaced_mean_anomaly" ? 1 : Math.max(2, planes);
    } else if (command.planes === 1) {
      phasingMode = "evenly_spaced_mean_anomaly";
    } else if (command.planes && phasingMode === "evenly_spaced_mean_anomaly") {
      phasingMode = "walker_delta";
    }
    const singlePlane = phasingMode === "evenly_spaced_mean_anomaly";
    space.phasing_mode = phasingMode;
    space.planes = planes;
    space.slots_per_plane = slotsPerPlane;
    space.raan_spacing_deg = singlePlane
      ? 360
      : (phasingMode === "walker_star" ? 180 : 360) / planes;
    space.phase_offset_deg = singlePlane ? 0 : 360 / (planes * slotsPerPlane);
  } else if (command.operation === "add_ground") {
    const number = workspace.ground.length + 1;
    affectedKind = "ground";
    affectedId = `ground-${number}`;
    workspace.ground.push({
      segment_id: affectedId,
      display_name: `Ground segment ${number}`,
      members: [],
      stamp: {
        node_ref: command.node_ref ?? GROUND_NODE,
        installed: command.installed ?? {},
        boresights: command.boresights ?? {},
        body: command.body_ref ?? "nodalarc:bodies/earth.yaml",
        lan_base: `172.${19 + number}`,
        loopback_base: `10.${199 + number}`,
      },
      scheduling: structuredClone(LEO_SCHEDULING),
      originated_ipv4: [],
      tags: [],
    });
    schedulingPreset = "leo-fast-handover";
  } else if (command.operation === "set_ground_stamp_node_model") {
    affectedKind = "ground";
    affectedId = command.segment_id;
    const ground = workspace.ground.find(
      (candidate: { segment_id: string }) => candidate.segment_id === command.segment_id,
    );
    ground.stamp = {
      ...ground.stamp,
      node_ref: command.node_ref,
      ...groundInstallation(command.node_ref),
    };
  } else if (command.operation === "set_ground_site_node_model") {
    affectedKind = "ground_member";
    affectedId = command.member_id;
    const ground = workspace.ground.find(
      (candidate: { segment_id: string }) => candidate.segment_id === command.segment_id,
    );
    const member = ground.members.find(
      (candidate: { member_id: string }) => candidate.member_id === command.member_id,
    );
    const node = member.site.nodes.find(
      (candidate: { node_id: string }) => candidate.node_id === command.node_id,
    );
    Object.assign(node, {
      model_ref: command.node_ref,
      ...groundInstallation(command.node_ref),
    });
  } else if (command.operation === "add_ground_site_node") {
    affectedKind = "ground_member";
    affectedId = command.member_id;
    const ground = workspace.ground.find(
      (candidate: { segment_id: string }) => candidate.segment_id === command.segment_id,
    );
    const member = ground.members.find(
      (candidate: { member_id: string }) => candidate.member_id === command.member_id,
    );
    const taken = new Set(member.site.nodes.map((node: { node_id: string }) => node.node_id));
    let number = 1;
    while (taken.has(`gw${number}`)) number += 1;
    const nodeRef = command.node_ref ?? member.site.nodes[0].model_ref;
    member.site.nodes.push({
      node_id: `gw${number}`,
      model_ref: nodeRef,
      ...groundInstallation(nodeRef),
      lo0_ipv4: "",
      terr0_ipv4: "",
    });
  } else if (command.operation === "mint_ground_members") {
    affectedKind = "ground";
    affectedId = command.segment_id;
    const ground = workspace.ground.find(
      (candidate: { segment_id: string }) => candidate.segment_id === command.segment_id,
    );
    for (const [offset, siteIntent] of command.sites.entries()) {
      const index = ground.members.length + offset;
      ground.members.push({
        member_id: `member-${index + 1}`,
        kind: "draft",
        ref: null,
        site_id: `site-${index + 1}`,
        label: siteIntent.name,
        summary: null,
        scheduling_override: null,
        site: {
          site_id: `site-${index + 1}`,
          display_name: siteIntent.name,
          body: ground.stamp.body,
          lat_deg: siteIntent.lat_deg,
          lon_deg: siteIntent.lon_deg,
          alt_m: siteIntent.alt_m ?? 0,
          lan_ipv4: `${ground.stamp.lan_base}.${index}.0/24`,
          tags: [],
          nodes: [{
            node_id: "gw1",
            model_ref: ground.stamp.node_ref,
            installed: ground.stamp.installed,
            boresights: ground.stamp.boresights,
            lo0_ipv4: `${ground.stamp.loopback_base}.0.${index + 1}/32`,
            terr0_ipv4: `${ground.stamp.lan_base}.${index}.1/24`,
          }],
        },
      });
    }
    notice = `minted ${command.sites.length} sites`;
  } else if (command.operation === "add_routing_domain") {
    affectedKind = "routing_domain";
    affectedId = `domain-${workspace.routing_domains.length + 1}`;
    workspace.routing_domains.push({
      domain_id: affectedId,
      label: affectedId.replace("-", " "),
      protocol: "isis",
      member_segment_ids: [
        ...workspace.space_refs,
        ...workspace.space,
        ...workspace.ground_refs,
        ...workspace.ground,
      ].map((segment: { segment_id: string }) => segment.segment_id),
      hello_interval_s: null,
      hold_interval_s: null,
    });
  } else if (command.operation === "add_boundary") {
    affectedKind = "boundary";
    affectedId = `boundary-${workspace.boundaries.length + 1}`;
    workspace.boundaries.push({
      boundary_id: affectedId,
      over_rule_id: workspace.links[0]?.rule_id ?? "",
      adapter: "static_ip",
      from_domain_id: workspace.routing_domains[0]?.domain_id ?? "",
      to_domain_id:
        workspace.routing_domains[1]?.domain_id ??
        workspace.routing_domains[0]?.domain_id ??
        "",
      export_node_loopbacks: true,
    });
  } else if (command.operation === "connect_segments") {
    affectedKind = "link";
    affectedId = `link-${workspace.links.length + 1}`;
    const segments = [
      ...workspace.space_refs.map((segment: any) => ({ ...segment, kind: "space" })),
      ...workspace.space.map((segment: any) => ({ ...segment, kind: "space" })),
      ...workspace.ground_refs.map((segment: any) => ({ ...segment, kind: "ground" })),
      ...workspace.ground.map((segment: any) => ({ ...segment, kind: "ground" })),
    ];
    const source = segments.find(
      (segment: any) => segment.segment_id === command.from_segment_id,
    );
    const target = segments.find((segment: any) => segment.segment_id === command.to_segment_id);
    const [first, second] =
      source.kind === "ground" || target.kind !== "ground"
        ? [source, target]
        : [target, source];
    const access = first.kind === "ground" || second.kind === "ground";
    const mesh = first.segment_id === second.segment_id;
    const role = access ? "access" : mesh ? "isl" : "crosslink";
    const endpoint = (segment: any) => ({
      segment_id: segment.segment_id,
      tag: null,
      role,
      medium: access ? "rf" : "optical",
      min_elevation_deg: access && segment.kind === "ground" ? 25 : null,
    });
    workspace.links.push({
      rule_id: affectedId,
      label: mesh
        ? `${first.display_name ?? first.label} mesh`
        : `${first.display_name ?? first.label} to ${second.display_name ?? second.label}`,
      enabled: true,
      a: endpoint(first),
      b: endpoint(second),
      topology_mode: access ? "visible_candidates" : "nearest_n",
      topology_n: mesh ? 2 : 1,
      max_range_km: null,
    });
  } else if (command.operation === "set_scheduling_preset") {
    affectedKind = command.member_id ? "ground_member" : "ground";
    affectedId = command.member_id ?? command.segment_id;
    schedulingPreset = command.preset;
    const ground = workspace.ground.find(
      (candidate: any) => candidate.segment_id === command.segment_id,
    );
    const groundRef = workspace.ground_refs.find(
      (candidate: any) => candidate.segment_id === command.segment_id,
    );
    if (ground && command.member_id) {
      const member = ground.members.find(
        (candidate: any) => candidate.member_id === command.member_id,
      );
      member.scheduling_override = command.preset
        ? structuredClone(
            command.preset === "geo-longest-pass" ? GEO_SCHEDULING : LEO_SCHEDULING,
          )
        : null;
    } else if (ground) {
      ground.scheduling = structuredClone(
        command.preset === "geo-longest-pass" ? GEO_SCHEDULING : LEO_SCHEDULING,
      );
    } else if (groundRef) {
      groundRef.scheduling = structuredClone(
        command.preset === "geo-longest-pass" ? GEO_SCHEDULING : LEO_SCHEDULING,
      );
    }
  } else if (command.operation === "rederive_link") {
    affectedKind = "link";
    affectedId = command.rule_id;
    const rule = workspace.links.find((candidate: any) => candidate.rule_id === command.rule_id);
    rule[command.side].segment_id = command.segment_id;
    notice = "re-derived by VS-API";
  }
  const baseDraftRevision = draft.draft_revision;
  draft.draft_revision += 1;
  workspace.projection_revision = draft.draft_revision;
  draft.authoring_workspace = workspace;
  draft.applied_workspace = workspace;
  draft.applied_revision = draft.draft_revision;
  return jsonResponse({
    contract_version: 1,
    operation: command.operation,
    base_draft_revision: baseDraftRevision,
    draft,
    affected_kind: affectedKind,
    affected_id: affectedId,
    scheduling_preset: schedulingPreset,
    notice,
  });
}

function summary(ref: string, family: string) {
  const id = (ref.split("/").pop() ?? ref).replace(/\.ya?ml$/, "");
  return {
    ref,
    namespace: ref.startsWith("user:") ? "user" : "nodalarc",
    family,
    revision: `revision-${ref}`,
    size_bytes: 100,
    display_name: id,
    summary: null,
  };
}

function catalogDocument(ref: string, family: string, canonicalJson: Record<string, unknown>) {
  return {
    ref,
    family,
    canonical_yaml: "document: true\n",
    canonical_json: canonicalJson,
    content_digest: `digest-${ref}`,
    revision: `revision-${ref}`,
  };
}

const GROUND_NODE = "nodalarc:nodes/ground/gateway.yaml";
const SECOND_GROUND_NODE = "nodalarc:nodes/ground/high-capacity-gateway.yaml";
const CATALOG_FAMILIES = [
  "bodies",
  "constellations",
  "nodes",
  "orbits",
  "payloads",
  "site-sets",
  "sites",
  "space-node-sets",
  "terminals",
  "sessions",
] as const;

function bootstrapResponse() {
  return jsonResponse({
    contract_version: 1,
    authoring_context_binding: "builder-view-test-context",
    public_grammar_href: "/docs/ops/configuration-grammar.md",
    capabilities: { user_catalog_write: true, deploy_yaml_closure: true },
    families: CATALOG_FAMILIES.map((family) => ({
      family,
      wrapper: family === "sessions" ? null : family.replace(/-/g, "_").replace(/s$/, ""),
      direct_user_write: family !== "sessions",
      component_fork: family !== "sessions",
      session_draft_save: family === "sessions",
      suggested_object_id: family === "sessions" ? null : `my-${family}`,
    })),
    scheduling_presets: [
      {
        id: "leo-fast-handover",
        label: "LEO fast handover — make-before-break",
      },
      {
        id: "geo-longest-pass",
        label: "GEO longest pass — break-before-make",
      },
    ],
    authoring: AUTHORING_FACTS,
  });
}

/** A fetch stub covering every typed Builder endpoint this surface touches. */
function stubFetch(options?: {
  sessions?: ReturnType<typeof summary>[];
  sessionDocument?: Record<string, unknown>;
  sessionPreview?: unknown;
  newDraft?: BuilderVisualDraftEnvelope;
  rejectExplicitBlankName?: boolean;
  commandHandler?: (request: Record<string, any>) => Promise<unknown>;
  compileHandler?: (
    request: Record<string, any>,
  ) => ReturnType<typeof jsonResponse> | Promise<ReturnType<typeof jsonResponse>>;
  applyYamlHandler?: (
    request: Record<string, any>,
  ) => ReturnType<typeof jsonResponse> | Promise<ReturnType<typeof jsonResponse>>;
  saveHandler?: (
    request: Record<string, any>,
  ) => ReturnType<typeof jsonResponse> | Promise<ReturnType<typeof jsonResponse>>;
}) {
  const sessions = options?.sessions ?? [];
  let blankDraftCount = 0;
  const fetchMock = vi.fn((url: string, init?: { body?: string }) => {
    if (url.includes("/builder/bootstrap")) return Promise.resolve(bootstrapResponse());
    if (url.includes("/builder/draft/new")) {
      const request = init?.body ? JSON.parse(init.body) : {};
      if (options?.rejectExplicitBlankName && request.session_name === "") {
        return Promise.resolve(
          jsonResponse(
            { detail: [{ msg: "String should match the canonical identifier pattern" }] },
            false,
            422,
          ),
        );
      }
      const sessionName = request.session_name ?? `untitled-session-${++blankDraftCount}`;
      const responseDraft =
        options?.newDraft && request.session_name === undefined
          ? options.newDraft
          : structuredVisualDraft(sessionName);
      return Promise.resolve(
        jsonResponse(structuredClone(responseDraft)),
      );
    }
    if (url.includes("/builder/draft/open")) {
      const request = init?.body ? JSON.parse(init.body) : {};
      const sourceRef = String(request.source_ref);
      const targetRef = request.target_ref ?? (sourceRef.startsWith("user:")
        ? sourceRef
        : `user:${sourceRef.slice("nodalarc:".length)}`);
      const targetName = String(targetRef).split("/").pop()?.replace(/\.ya?ml$/, "") ?? "opened";
      return Promise.resolve(
        jsonResponse({
          ...structuredVisualDraft(targetName),
          target_ref: targetRef,
          source_ref: sourceRef,
          expected_session_revision: sourceRef.startsWith("user:")
            ? `revision-${sourceRef}`
            : null,
          session_yaml: "# exact source\nsession:\n  name: opened\nsegments: []\n",
        }),
      );
    }
    if (url.includes("/builder/catalog/get")) {
      const request = init?.body ? JSON.parse(init.body) : {};
      if (String(request.ref).includes(":sessions/")) {
        return Promise.resolve(
          jsonResponse(
            catalogDocument(request.ref, "sessions", options?.sessionDocument ?? { session: { name: "opened" } }),
          ),
        );
      }
      return Promise.resolve(
        jsonResponse(catalogDocument(GROUND_NODE, "nodes", { node: { id: "gateway", terminals: [] } })),
      );
    }
    if (url.includes("/builder/catalog/list")) {
      const family = init?.body ? JSON.parse(init.body).family : null;
      return Promise.resolve(
        jsonResponse({
          generation: "g1",
          items:
            family === "sessions"
              ? sessions
              : family === "nodes"
                ? [summary(GROUND_NODE, "nodes"), summary(SECOND_GROUND_NODE, "nodes")]
                : family === "terminals"
                  ? [summary("nodalarc:terminals/rf/space-gateway.yaml", "terminals")]
                  : [],
          next_page_token: null,
        }),
      );
    }
    if (url.includes("/builder/draft/compile")) {
      const request = init?.body ? JSON.parse(init.body) : {};
      if (options?.compileHandler) return Promise.resolve(options.compileHandler(request));
      return Promise.resolve(compileResponse(request, options?.sessionPreview));
    }
    if (url.includes("/builder/draft/apply-yaml")) {
      const request = init?.body ? JSON.parse(init.body) : {};
      if (options?.applyYamlHandler) {
        return Promise.resolve(options.applyYamlHandler(request));
      }
      return Promise.resolve(jsonResponse({}));
    }
    if (url.includes("/builder/draft/apply-workspace")) {
      const request = init?.body ? JSON.parse(init.body) : {};
      const revision = Number(request.draft.draft_revision) + 1;
      const workspace = { ...request.workspace, projection_revision: revision };
      const nextDraft = {
        ...request.draft,
        draft_revision: revision,
        projection_status: "applied",
        session_yaml: `session:\n  name: ${workspace.session_name}\nsegments: []\n`,
        authoring_workspace: workspace,
        applied_workspace: workspace,
        applied_revision: revision,
        applied_session: { session: { name: workspace.session_name }, segments: [] },
      };
      return Promise.resolve(compileResponse({ draft: nextDraft }, options?.sessionPreview));
    }
    if (url.includes("/builder/draft/retarget")) {
      const request = init?.body ? JSON.parse(init.body) : {};
      const revision = Number(request.draft.draft_revision) + 1;
      const sessionName = String(request.target_ref).split("/").pop()?.replace(/\.ya?ml$/, "");
      const workspace = {
        ...request.draft.authoring_workspace,
        session_name: sessionName,
        projection_revision: revision,
      };
      const nextDraft = {
        ...request.draft,
        target_ref: request.target_ref,
        expected_session_revision: null,
        draft_revision: revision,
        projection_status: "applied",
        session_yaml: `session:\n  name: ${sessionName}\nsegments: []\n`,
        authoring_workspace: workspace,
        applied_workspace: workspace,
        applied_revision: revision,
        applied_session: { session: { name: sessionName }, segments: [] },
      };
      const compileRequest = { draft: nextDraft };
      if (options?.compileHandler) {
        return Promise.resolve(options.compileHandler(compileRequest));
      }
      return Promise.resolve(compileResponse(compileRequest, options?.sessionPreview));
    }
    if (url.includes("/builder/session/save")) {
      const request = init?.body ? JSON.parse(init.body) : {};
      if (options?.saveHandler) return Promise.resolve(options.saveHandler(request));
      return Promise.resolve(saveResponse(request));
    }
    if (url.includes("/builder/draft/command")) {
      const request = init?.body ? JSON.parse(init.body) : {};
      if (options?.commandHandler) return options.commandHandler(request);
      return Promise.resolve(visualCommandResponse(request));
    }
    return Promise.resolve(jsonResponse({}));
  });
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  return fetchMock;
}

const sessionsCalls = (fetchMock: ReturnType<typeof vi.fn>) =>
  fetchMock.mock.calls.filter((call: unknown[]) => {
    if (!String(call[0]).includes("/builder/catalog/list")) return false;
    const init = call[1] as { body?: string } | undefined;
    return init?.body ? JSON.parse(init.body).family === "sessions" : false;
  });
const compileCalls = (fetchMock: ReturnType<typeof vi.fn>) =>
  fetchMock.mock.calls.filter((call: unknown[]) =>
    String(call[0]).includes("/builder/draft/compile"),
  );
const workspaceCalls = (fetchMock: ReturnType<typeof vi.fn>) =>
  fetchMock.mock.calls.filter((call: unknown[]) =>
    String(call[0]).includes("/builder/draft/apply-workspace"),
  );
const yamlCalls = (fetchMock: ReturnType<typeof vi.fn>) =>
  fetchMock.mock.calls.filter((call: unknown[]) =>
    String(call[0]).includes("/builder/draft/apply-yaml"),
  );
const retargetCalls = (fetchMock: ReturnType<typeof vi.fn>) =>
  fetchMock.mock.calls.filter((call: unknown[]) =>
    String(call[0]).includes("/builder/draft/retarget"),
  );
const commandCalls = (fetchMock: ReturnType<typeof vi.fn>) =>
  fetchMock.mock.calls.filter((call: unknown[]) =>
    String(call[0]).includes("/builder/draft/command"),
  );
const newDraftCalls = (fetchMock: ReturnType<typeof vi.fn>) =>
  fetchMock.mock.calls.filter((call: unknown[]) =>
    String(call[0]).includes("/builder/draft/new"),
  );
const saveCalls = (fetchMock: ReturnType<typeof vi.fn>) =>
  fetchMock.mock.calls.filter((call: unknown[]) =>
    String(call[0]).includes("/builder/session/save"),
  );

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
});
afterEach(() => {
  cleanup();
  localStorage.clear();
});

describe("BuilderView — resolve-loop and world honesty", () => {
  it("renders the start card with no session loaded", async () => {
    stubFetch();
    render(<BuilderView {...PROPS} />);
    expect(await screen.findByTestId("builder-start")).toBeTruthy();
  });

  it("adopts byte-canonical YAML before workspace autosync can repost stale state", async () => {
    const canonicalYaml = [
      "session:",
      "  name: yaml-sync",
      "segments: []",
      "simulation:",
      "  candidate_limits:",
      "    max_pairs_per_rule: 2000",
      "    max_pairs_per_tick: 10000",
      "time:",
      "  start_time: '2026-01-01T00:00:00Z'",
      "  step_seconds: 1",
      "  compression: 7",
      "",
    ].join("\n");
    const fetchMock = stubFetch({
      newDraft: structuredVisualDraft("yaml-sync"),
      compileHandler: (request) =>
        compileResponse(
          request,
          null,
          null,
          request.draft.draft_revision > 0 ? canonicalYaml : null,
        ),
      applyYamlHandler: (request) => {
        const revision = Number(request.draft.draft_revision) + 1;
        const workspace = {
          ...request.draft.authoring_workspace,
          compression: 7,
          projection_revision: revision,
        };
        return jsonResponse({
          draft: {
            ...request.draft,
            draft_revision: revision,
            projection_status: "applied",
            session_yaml: request.yaml_text,
            authoring_workspace: workspace,
            applied_workspace: workspace,
            applied_revision: revision,
            applied_session: {
              session: { name: "yaml-sync" },
              segments: [],
              time: { compression: 7 },
            },
          },
          buffer_generation: request.buffer_generation,
          yaml_text: request.yaml_text,
          applied: true,
          canonicalization_required: false,
          issues: [],
        });
      },
    });
    render(<BuilderView {...PROPS} />);
    fireEvent.click(
      within(await screen.findByTestId("builder-start")).getByRole("button", {
        name: /New session/i,
      }),
    );
    await waitFor(() => expect(compileCalls(fetchMock).length).toBeGreaterThan(0));

    fireEvent.change(screen.getByLabelText("Session YAML"), {
      target: { value: canonicalYaml },
    });
    fireEvent.click(screen.getByRole("button", { name: "Apply YAML" }));

    await waitFor(() => expect(yamlCalls(fetchMock)).toHaveLength(1));
    expect(await screen.findByText("step 1s · ×7")).toBeTruthy();
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 700));
    });
    expect(workspaceCalls(fetchMock)).toHaveLength(0);
    expect(screen.getByText("step 1s · ×7")).toBeTruthy();
  });

  it("adopts formatted YAML and retains its projection through Use canonical YAML", async () => {
    const formattedYaml = [
      "# hand-formatted session",
      "session:",
      "  name: yaml-canonical",
      "segments: []",
      "simulation:",
      "  candidate_limits: {max_pairs_per_rule: 2000, max_pairs_per_tick: 10000}",
      "time: {start_time: '2026-01-01T00:00:00Z', step_seconds: 1, compression: 9}",
      "",
    ].join("\n");
    const canonicalYaml = [
      "session:",
      "  name: yaml-canonical",
      "segments: []",
      "simulation:",
      "  candidate_limits:",
      "    max_pairs_per_rule: 2000",
      "    max_pairs_per_tick: 10000",
      "time:",
      "  start_time: '2026-01-01T00:00:00Z'",
      "  step_seconds: 1",
      "  compression: 9",
      "",
    ].join("\n");
    const fetchMock = stubFetch({
      newDraft: structuredVisualDraft("yaml-canonical"),
      compileHandler: (request) =>
        compileResponse(
          request,
          null,
          null,
          request.draft.draft_revision > 0 ? canonicalYaml : null,
        ),
      applyYamlHandler: (request) => {
        const revision = Number(request.draft.draft_revision) + 1;
        const workspace = {
          ...request.draft.authoring_workspace,
          compression: 9,
          projection_revision: revision,
        };
        return jsonResponse({
          draft: {
            ...request.draft,
            draft_revision: revision,
            projection_status: "applied",
            session_yaml: request.yaml_text,
            authoring_workspace: workspace,
            applied_workspace: workspace,
            applied_revision: revision,
            applied_session: {
              session: { name: "yaml-canonical" },
              segments: [],
              time: { compression: 9 },
            },
          },
          buffer_generation: request.buffer_generation,
          yaml_text: request.yaml_text,
          applied: true,
          canonicalization_required: request.yaml_text !== canonicalYaml,
          issues: [],
        });
      },
    });
    render(<BuilderView {...PROPS} />);
    fireEvent.click(
      within(await screen.findByTestId("builder-start")).getByRole("button", {
        name: /New session/i,
      }),
    );
    await waitFor(() => expect(compileCalls(fetchMock).length).toBeGreaterThan(0));

    fireEvent.change(screen.getByLabelText("Session YAML"), {
      target: { value: formattedYaml },
    });
    fireEvent.click(screen.getByRole("button", { name: "Apply YAML" }));

    expect(await screen.findByText("step 1s · ×9")).toBeTruthy();
    fireEvent.click(await screen.findByRole("button", { name: "Use canonical YAML" }));
    await waitFor(() => expect(yamlCalls(fetchMock)).toHaveLength(2));
    expect((screen.getByLabelText("Session YAML") as HTMLTextAreaElement).value).toBe(
      canonicalYaml,
    );
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 700));
    });
    expect(workspaceCalls(fetchMock)).toHaveLength(0);
    expect(screen.getByText("step 1s · ×9")).toBeTruthy();
  });

  it("keeps the saved target while a typed rename is reverted", async () => {
    const savedFoo: BuilderVisualDraftEnvelope = {
      ...structuredVisualDraft("foo"),
      source_ref: "user:sessions/foo.yaml",
      expected_session_revision: "foo-revision",
      catalog_documents: [
        {
          ref: "user:terminals/shared/custom.yaml",
          document: { terminal: { id: "custom" } },
          origin: "customized",
        },
      ],
      reserved_authoring_ids: ["space-1", "ground-1"],
    };
    const fetchMock = stubFetch({ newDraft: savedFoo });
    render(<BuilderView {...PROPS} />);
    fireEvent.click(
      within(await screen.findByTestId("builder-start")).getByRole("button", {
        name: /New session/i,
      }),
    );
    await waitFor(() => expect(compileCalls(fetchMock).length).toBeGreaterThan(0));
    expect(newDraftCalls(fetchMock)).toHaveLength(1);
    expect(JSON.parse(newDraftCalls(fetchMock)[0]![1]?.body ?? "{}")).toEqual({});

    fireEvent.click(screen.getByText("Identity & time"));
    const nameInput = screen.getByLabelText("name");
    fireEvent.change(nameInput, { target: { value: "bar" } });
    await waitFor(() => {
      const calls = workspaceCalls(fetchMock);
      const request = JSON.parse(calls[calls.length - 1]?.[1]?.body ?? "{}");
      expect(request.workspace.session_name).toBe("bar");
    });
    let calls = workspaceCalls(fetchMock);
    let compiled = JSON.parse(calls[calls.length - 1]?.[1]?.body ?? "{}").draft;
    expect(compiled).toMatchObject({
      target_ref: "user:sessions/foo.yaml",
      expected_session_revision: "foo-revision",
      catalog_documents: savedFoo.catalog_documents,
      reserved_authoring_ids: ["space-1", "ground-1"],
    });
    expect(newDraftCalls(fetchMock)).toHaveLength(1);

    fireEvent.change(nameInput, { target: { value: "foo" } });
    await waitFor(() => {
      const currentCalls = workspaceCalls(fetchMock);
      const request = JSON.parse(currentCalls[currentCalls.length - 1]?.[1]?.body ?? "{}");
      expect(request.workspace.session_name).toBe("foo");
    });
    calls = workspaceCalls(fetchMock);
    compiled = JSON.parse(calls[calls.length - 1]?.[1]?.body ?? "{}").draft;
    expect(compiled).toMatchObject({
      target_ref: "user:sessions/foo.yaml",
      expected_session_revision: "foo-revision",
      catalog_documents: savedFoo.catalog_documents,
      reserved_authoring_ids: ["space-1", "ground-1"],
    });
    expect(newDraftCalls(fetchMock)).toHaveLength(1);
    expect(screen.getByText("Drafts · foo")).toBeTruthy();
  });

  it("treats a nonempty local name as authored without guessing backend placeholders", async () => {
    const fetchMock = stubFetch({ newDraft: structuredVisualDraft("untitled-session-1") });
    render(<BuilderView {...PROPS} />);
    fireEvent.click(
      within(await screen.findByTestId("builder-start")).getByRole("button", {
        name: /New session/i,
      }),
    );
    await waitFor(() => expect(compileCalls(fetchMock).length).toBeGreaterThan(0));

    fireEvent.click(screen.getByText("Identity & time"));
    fireEvent.change(screen.getByLabelText("name"), { target: { value: "authored-name" } });

    const guide = screen.getByTestId("builder-guide");
    expect(await within(guide).findByText("authored-name")).toBeTruthy();
    expect(within(guide).queryByText("name it — real time unless you say otherwise")).toBeNull();
    expect(newDraftCalls(fetchMock)).toHaveLength(1);
  });

  it("leaves the original target in place when Save As compilation is blocked", async () => {
    const fetchMock = stubFetch({
      newDraft: {
        ...structuredVisualDraft("foo"),
        expected_session_revision: "foo-revision",
        reserved_authoring_ids: ["space-1"],
      },
      compileHandler: (request) =>
        compileResponse(
          request,
          null,
          request.draft.target_ref === "user:sessions/bar.yaml"
            ? "the new target is blocked"
            : null,
        ),
    });
    render(<BuilderView {...PROPS} />);
    fireEvent.click(
      within(await screen.findByTestId("builder-start")).getByRole("button", {
        name: /New session/i,
      }),
    );
    const toolbarSave = await screen.findByRole("button", {
      name: "Save session to your library",
    });
    await waitFor(() => expect((toolbarSave as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(toolbarSave);
    const dialog = await screen.findByTestId("builder-save-dialog");
    fireEvent.change(within(dialog).getByLabelText("save as"), { target: { value: "bar" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));

    expect(await within(dialog).findByText("save failed: the new target is blocked")).toBeTruthy();
    expect(saveCalls(fetchMock)).toHaveLength(0);
    expect(retargetCalls(fetchMock)).toHaveLength(1);
    expect(screen.getByText("Drafts · foo")).toBeTruthy();
    expect(newDraftCalls(fetchMock)).toHaveLength(1);
  });

  it("leaves the original target in place when Save As persistence fails", async () => {
    const fetchMock = stubFetch({
      newDraft: {
        ...structuredVisualDraft("foo"),
        expected_session_revision: "foo-revision",
        catalog_documents: [
          {
            ref: "user:terminals/shared/custom.yaml",
            document: { terminal: { id: "custom" } },
            origin: "customized",
          },
        ],
        reserved_authoring_ids: ["space-1"],
      },
      saveHandler: () => jsonResponse({ detail: "storage refused the write" }, false, 500),
    });
    render(<BuilderView {...PROPS} />);
    fireEvent.click(
      within(await screen.findByTestId("builder-start")).getByRole("button", {
        name: /New session/i,
      }),
    );
    const toolbarSave = await screen.findByRole("button", {
      name: "Save session to your library",
    });
    await waitFor(() => expect((toolbarSave as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(toolbarSave);
    const dialog = await screen.findByTestId("builder-save-dialog");
    fireEvent.change(within(dialog).getByLabelText("save as"), { target: { value: "bar" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));

    expect(await within(dialog).findByText("save failed: storage refused the write")).toBeTruthy();
    expect(saveCalls(fetchMock)).toHaveLength(1);
    expect(retargetCalls(fetchMock)).toHaveLength(1);
    expect(screen.getByText("Drafts · foo")).toBeTruthy();
    const failedSave = JSON.parse(saveCalls(fetchMock)[0]![1]?.body ?? "{}");
    expect(failedSave).toMatchObject({
      target_ref: "user:sessions/bar.yaml",
    });
    expect(JSON.parse(retargetCalls(fetchMock)[0]![1]?.body ?? "{}")).toMatchObject({
      expected_draft_revision: 0,
      target_ref: "user:sessions/bar.yaml",
    });
  });

  it("adopts a Save As target only after persistence succeeds", async () => {
    let releaseSave!: (response: ReturnType<typeof jsonResponse>) => void;
    const pendingSave = new Promise<ReturnType<typeof jsonResponse>>((resolve) => {
      releaseSave = resolve;
    });
    const fetchMock = stubFetch({
      newDraft: {
        ...structuredVisualDraft("foo"),
        expected_session_revision: "foo-revision",
        reserved_authoring_ids: ["space-1"],
      },
      saveHandler: () => pendingSave,
    });
    render(<BuilderView {...PROPS} />);
    fireEvent.click(
      within(await screen.findByTestId("builder-start")).getByRole("button", {
        name: /New session/i,
      }),
    );
    const toolbarSave = await screen.findByRole("button", {
      name: "Save session to your library",
    });
    await waitFor(() => expect((toolbarSave as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(toolbarSave);
    const dialog = await screen.findByTestId("builder-save-dialog");
    fireEvent.change(within(dialog).getByLabelText("save as"), { target: { value: "bar" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() => expect(saveCalls(fetchMock)).toHaveLength(1));
    expect(retargetCalls(fetchMock)).toHaveLength(1);
    expect(screen.getByText("Drafts · foo")).toBeTruthy();
    const request = JSON.parse(saveCalls(fetchMock)[0]![1]?.body ?? "{}");
    await act(async () => {
      releaseSave(saveResponse(request));
      await pendingSave;
    });

    expect(await screen.findByText("Drafts · bar")).toBeTruthy();
    expect(within(await screen.findByTestId("builder-save-dialog")).getByText(
      "saved as bar — deploy it with the rocket",
    )).toBeTruthy();
  });

  it("keeps a committed-but-unverified Save As target visible and guarded", async () => {
    stubFetch({
      newDraft: {
        ...structuredVisualDraft("foo"),
        expected_session_revision: "foo-revision",
      },
      saveHandler: () =>
        jsonResponse(
          {
            message: "storage verification failed",
            repository_committed: true,
            target_ref: "user:sessions/bar.yaml",
          },
          false,
          500,
        ),
    });
    render(<BuilderView {...PROPS} />);
    fireEvent.click(
      within(await screen.findByTestId("builder-start")).getByRole("button", {
        name: /New session/i,
      }),
    );
    const toolbarSave = await screen.findByRole("button", {
      name: "Save session to your library",
    });
    await waitFor(() => expect((toolbarSave as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(toolbarSave);
    const dialog = await screen.findByTestId("builder-save-dialog");
    fireEvent.change(within(dialog).getByLabelText("save as"), { target: { value: "bar" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));

    expect(await screen.findByText("Drafts · bar")).toBeTruthy();
    expect(within(await screen.findByTestId("builder-save-dialog")).getByText(
      /save committed, but storage verification failed/,
    )).toBeTruthy();
    expect(screen.getByRole("button", {
      name: /Save committed without verification for user:sessions\/bar.yaml/,
    })).toBeTruthy();
  });

  it("refuses a touched blank Save-As name without saving or changing draft identity", async () => {
    const fetchMock = stubFetch({ rejectExplicitBlankName: true });
    render(<BuilderView {...PROPS} />);
    fireEvent.click(
      within(await screen.findByTestId("builder-start")).getByRole("button", {
        name: /New session/i,
      }),
    );

    const saveToolbarButton = await screen.findByRole("button", {
      name: "Save session to your library",
    });
    await waitFor(() => expect((saveToolbarButton as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(saveToolbarButton);

    const dialog = await screen.findByTestId("builder-save-dialog");
    const nameInput = within(dialog).getByLabelText("save as");
    expect((nameInput as HTMLInputElement).value).toBe("untitled-session-1");
    fireEvent.change(nameInput, { target: { value: "" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(within(dialog).getByText("save failed: session name is required")).toBeTruthy(),
    );
    const newDraftCalls = fetchMock.mock.calls.filter((call) =>
      String(call[0]).includes("/builder/draft/new"),
    );
    expect(newDraftCalls).toHaveLength(1);
    expect(retargetCalls(fetchMock)).toHaveLength(0);
    expect(
      fetchMock.mock.calls.some((call) => String(call[0]).includes("/builder/session/save")),
    ).toBe(false);
    expect(screen.getByText("Drafts · untitled-session-1")).toBeTruthy();
  });

  it("labels nullable link and routing facts as incomplete in the outline", async () => {
    const base = structuredVisualDraft("incomplete-outline");
    const baseWorkspace = base.authoring_workspace!;
    const incompleteDraft: BuilderVisualDraftEnvelope = {
      ...base,
      authoring_workspace: {
        ...baseWorkspace,
        space_refs: [
          {
            segment_id: "space-1",
            source_ref: "nodalarc:constellations/a.yaml",
            label: "A",
          },
          {
            segment_id: "space-2",
            source_ref: "nodalarc:constellations/b.yaml",
            label: "B",
          },
        ],
        links: [{
          rule_id: "link-1",
          label: "A to B",
          enabled: true,
          a: {
            segment_id: "space-1",
            tag: null,
            role: null,
            medium: null,
            min_elevation_deg: null,
          },
          b: {
            segment_id: "space-2",
            tag: null,
            role: "isl",
            medium: "optical",
            min_elevation_deg: null,
          },
          topology_mode: null,
          topology_n: null,
          max_range_km: null,
        }],
        routing_domains: [{
          domain_id: "domain-1",
          label: "Domain 1",
          protocol: null,
          member_segment_ids: ["space-1", "space-2"],
          hello_interval_s: null,
          hold_interval_s: null,
        }],
        boundaries: [{
          boundary_id: "boundary-1",
          over_rule_id: "link-1",
          adapter: null,
          from_domain_id: "domain-1",
          to_domain_id: "domain-1",
          export_node_loopbacks: true,
        }],
      },
      applied_workspace: {
        ...baseWorkspace,
        space_refs: [
          {
            segment_id: "space-1",
            source_ref: "nodalarc:constellations/a.yaml",
            label: "A",
          },
          {
            segment_id: "space-2",
            source_ref: "nodalarc:constellations/b.yaml",
            label: "B",
          },
        ],
      },
    };
    stubFetch({ newDraft: incompleteDraft });
    render(<BuilderView {...PROPS} />);
    fireEvent.click(
      within(await screen.findByTestId("builder-start")).getByRole("button", {
        name: /New session/i,
      }),
    );

    const outline = await screen.findByTestId("builder-outline");
    expect(await within(outline).findByText("role incomplete")).toBeTruthy();
    expect(within(outline).getByText("protocol incomplete")).toBeTruthy();
    expect(within(outline).getByText("adapter incomplete")).toBeTruthy();
    expect(outline.textContent).not.toContain("null");
  });

  it("restores the exact structured envelope without minting a replacement draft", async () => {
    const workspace = newWorkspace("recovered-session");
    const recoveredDraft: BuilderVisualDraftEnvelope = {
      ...structuredVisualDraft("recovered-session"),
      source_ref: "user:sessions/recovered-session.yaml",
      expected_session_revision: "session-revision-fence",
      catalog_documents: [
        {
          ref: "user:terminals/recovered-terminal.yaml",
          document: {
            terminal: {
              id: "recovered-terminal",
              medium: "rf",
            },
          },
          origin: "customized",
        },
      ],
      authoring_workspace: workspace,
      applied_workspace: workspace,
    };
    expect(
      writeStructuredAutosave({
        authoringContextBinding: "builder-view-test-context",
        workspace,
        visualDraft: recoveredDraft,
        yaml: {
          text: recoveredDraft.session_yaml,
          appliedText: recoveredDraft.session_yaml,
          generation: 0,
          canonicalizationRequired: false,
          canonicalizationAccepted: false,
          issues: [],
        },
        editor: { windows: [], buffers: {} },
      }, builderRecoveryScope()),
    ).toBe(true);

    const fetchMock = stubFetch();
    render(<BuilderView {...PROPS} />);
    fireEvent.click(
      await screen.findByLabelText("Restore the autosaved draft from this browser"),
    );

    await waitFor(() => expect(compileCalls(fetchMock)).toHaveLength(1));
    expect(
      fetchMock.mock.calls.filter((call) =>
        String(call[0]).includes("/builder/draft/new"),
      ),
    ).toHaveLength(0);
    const request = JSON.parse(compileCalls(fetchMock)[0]![1]?.body ?? "{}");
    expect(request.draft).toMatchObject({
      target_ref: "user:sessions/recovered-session.yaml",
      source_ref: "user:sessions/recovered-session.yaml",
      expected_session_revision: "session-revision-fence",
      catalog_documents: [
        {
          ref: "user:terminals/recovered-terminal.yaml",
          origin: "customized",
        },
      ],
    });
  });

  it("restores invalid YAML beside the last valid graphical revision without compiling it", async () => {
    const workspace = newWorkspace("recovered-invalid");
    workspace.projection_revision = 3;
    const recoveredDraft: BuilderVisualDraftEnvelope = {
      ...structuredVisualDraft("recovered-invalid"),
      draft_revision: 3,
      projection_status: "pending_authoring",
      session_yaml: ": [unfinished",
      authoring_workspace: { ...workspace, projection_revision: null },
      applied_workspace: workspace,
      applied_revision: 3,
      applied_session: { session: { name: "recovered-invalid" }, segments: [] },
    };
    expect(
      writeStructuredAutosave({
        authoringContextBinding: "builder-view-test-context",
        workspace,
        visualDraft: recoveredDraft,
        yaml: {
          text: ": [unfinished",
          appliedText: "session:\n  name: recovered-invalid\nsegments: []\n",
          generation: 1,
          canonicalizationRequired: false,
          canonicalizationAccepted: false,
          issues: [
            {
              code: "builder.yaml.syntax",
              stage: "structural",
              severity: "error",
              message: "expected a closing bracket",
              source_line: 1,
              source_column: 3,
              blocks: ["save", "deploy"],
            },
          ],
        },
        editor: { windows: [], buffers: {} },
      }, builderRecoveryScope()),
    ).toBe(true);
    const fetchMock = stubFetch();
    render(<BuilderView {...PROPS} />);
    fireEvent.click(
      await screen.findByLabelText("Restore the autosaved draft from this browser"),
    );
    expect(await screen.findByText("Drafts · recovered-invalid")).toBeTruthy();
    expect(screen.getByTestId("builder-yaml-revision").textContent).toContain(
      "Showing applied revision 3; buffer generation 1 has errors",
    );
    expect(screen.getByTestId("builder-shell").className).toContain(
      "builder-shell--gui-locked",
    );
    expect(compileCalls(fetchMock)).toHaveLength(0);
  });

  it("submits incomplete content and shows the backend's typed save blocker", async () => {
    const fetchMock = stubFetch();
    render(<BuilderView {...PROPS} />);

    // New session → the guide; then add a ground segment (memberless → held back).
    const start = await screen.findByTestId("builder-start");
    fireEvent.click(within(start).getByRole("button", { name: /New session/i }));
    const addGround = await screen.findByText("Ground sites");
    fireEvent.click(addGround);

    await waitFor(() => expect(commandCalls(fetchMock)).toHaveLength(1));
    expect(JSON.parse(commandCalls(fetchMock)[0]![1].body).command).toEqual({
      operation: "add_ground",
    });

    // The incomplete object remains visible and reaches backend assembly; the
    // browser never filters it into a deceptively valid subset.
    await waitFor(() =>
      expect(screen.getByTestId("builder-status").textContent).toContain(
        "Ground sites requires at least one site",
      ),
    );
    expect(compileCalls(fetchMock).length).toBeGreaterThan(0);
    const calls = compileCalls(fetchMock);
    const latestCompile = JSON.parse(calls[calls.length - 1]![1].body);
    expect(latestCompile.draft.authoring_workspace.ground).toHaveLength(1);
    const status = screen.getByTestId("builder-status").textContent ?? "";
    expect(status).not.toContain("✓ resolves");
    expect(screen.getByTestId("builder-rail").textContent).toContain("Ground sites");
  });

  it("routes ground scheduling through the visual command and keeps it buffered", async () => {
    const fetchMock = stubFetch();
    render(<BuilderView {...PROPS} />);
    fireEvent.click(
      within(await screen.findByTestId("builder-start")).getByRole("button", {
        name: /New session/i,
      }),
    );
    fireEvent.click(await screen.findByText("Ground sites"));
    const editor = await screen.findByTestId("builder-ground-editor");
    fireEvent.click(within(editor).getByRole("button", { name: /Scheduling/ }));
    fireEvent.change(within(editor).getByLabelText("Scheduling preset"), {
      target: { value: "geo-longest-pass" },
    });

    await waitFor(() => expect(commandCalls(fetchMock)).toHaveLength(2));
    const request = JSON.parse(commandCalls(fetchMock)[1]![1].body);
    expect(request.command).toEqual({
      operation: "set_scheduling_preset",
      segment_id: "ground-1",
      preset: "geo-longest-pass",
    });
    await waitFor(() =>
      expect((within(editor).getByLabelText("Scheduling preset") as HTMLSelectElement).value).toBe(
        "geo-longest-pass",
      ),
    );
    expect(
      screen
        .getAllByRole("button", { name: "Apply" })
        .some((button) => !(button as HTMLButtonElement).disabled),
    ).toBe(true);
  });

  it("sends population intent and adopts backend-derived phasing values", async () => {
    const fetchMock = stubFetch();
    render(<BuilderView {...PROPS} />);
    fireEvent.click(
      within(await screen.findByTestId("builder-start")).getByRole("button", {
        name: /New session/i,
      }),
    );
    fireEvent.click(await screen.findByText("Space segments"));
    const editor = await screen.findByTestId("builder-editor");
    fireEvent.click(within(editor).getByRole("button", { name: /Pattern/ }));
    fireEvent.change(within(editor).getByLabelText("phasing"), {
      target: { value: "evenly_spaced_mean_anomaly" },
    });

    await waitFor(() => expect(commandCalls(fetchMock)).toHaveLength(2));
    expect(JSON.parse(commandCalls(fetchMock)[1]![1].body).command).toEqual({
      operation: "set_space_population",
      segment_id: "space-1",
      phasing_mode: "evenly_spaced_mean_anomaly",
    });
    await waitFor(() =>
      expect((within(editor).getByLabelText("planes") as HTMLInputElement).value).toBe("1"),
    );
    expect(editor.textContent).toContain("phase offset 0 deg");
  });

  it("sends only mount-role intent and adopts backend pointing semantics", async () => {
    const fetchMock = stubFetch();
    render(<BuilderView {...PROPS} />);
    fireEvent.click(
      within(await screen.findByTestId("builder-start")).getByRole("button", {
        name: /New session/i,
      }),
    );
    fireEvent.click(await screen.findByText("Space segments"));
    const editor = await screen.findByTestId("builder-editor");
    fireEvent.click(within(editor).getByRole("button", { name: /Node/ }));
    fireEvent.click(within(editor).getByRole("button", { name: "Author inline node" }));
    await waitFor(() => expect(commandCalls(fetchMock)).toHaveLength(2));
    fireEvent.click(within(editor).getByRole("button", { name: "+ port" }));
    fireEvent.click(await within(editor).findByRole("button", { name: /space-gateway/ }));
    await waitFor(() => expect(commandCalls(fetchMock)).toHaveLength(3));
    fireEvent.click(
      within(editor).getByRole("button", { name: /space-gatewayaccess/ }),
    );
    fireEvent.change(within(editor).getByLabelText("Mount role"), {
      target: { value: "isl" },
    });

    await waitFor(() => expect(commandCalls(fetchMock)).toHaveLength(4));
    expect(JSON.parse(commandCalls(fetchMock)[3]![1].body).command).toEqual({
      operation: "set_node_terminal_role",
      segment_id: "space-1",
      mount_id: "access_0",
      role: "isl",
    });
    await waitFor(() =>
      expect((within(editor).getByLabelText("Mount role") as HTMLSelectElement).value).toBe(
        "isl",
      ),
    );
    expect(within(editor).queryByLabelText("spacecraft boresight")).toBeNull();
  });

  it("sends stamp model selection and adopts backend installation facts", async () => {
    const fetchMock = stubFetch();
    render(<BuilderView {...PROPS} />);
    fireEvent.click(
      within(await screen.findByTestId("builder-start")).getByRole("button", {
        name: /New session/i,
      }),
    );
    fireEvent.click(await screen.findByText("Ground sites"));
    const editor = await screen.findByTestId("builder-ground-editor");
    fireEvent.click(within(editor).getByRole("button", { name: /New-site stamp/ }));
    fireEvent.change(within(editor).getByLabelText("Stamp node model"), {
      target: { value: SECOND_GROUND_NODE },
    });

    await waitFor(() => expect(commandCalls(fetchMock)).toHaveLength(2));
    expect(JSON.parse(commandCalls(fetchMock)[1]![1].body).command).toEqual({
      operation: "set_ground_stamp_node_model",
      segment_id: "ground-1",
      node_ref: SECOND_GROUND_NODE,
    });
    await waitFor(() =>
      expect(
        (within(editor).getByRole("spinbutton", { name: /access_ka/ }) as HTMLInputElement).value,
      ).toBe("64"),
    );
  });

  it("sends pasted locations as intent and adopts backend-minted site values", async () => {
    const fetchMock = stubFetch();
    render(<BuilderView {...PROPS} />);
    fireEvent.click(
      within(await screen.findByTestId("builder-start")).getByRole("button", {
        name: /New session/i,
      }),
    );
    fireEvent.click(await screen.findByText("Ground sites"));
    const editor = await screen.findByTestId("builder-ground-editor");
    fireEvent.change(within(editor).getByPlaceholderText(/paste sites, one per line/i), {
      target: { value: "Denver, 39.7, -104.9" },
    });
    fireEvent.click(within(editor).getByRole("button", { name: "+ mint pasted sites" }));

    await waitFor(() => expect(commandCalls(fetchMock)).toHaveLength(2));
    expect(JSON.parse(commandCalls(fetchMock)[1]![1].body).command).toEqual({
      operation: "mint_ground_members",
      segment_id: "ground-1",
      sites: [{ name: "Denver", lat_deg: 39.7, lon_deg: -104.9 }],
    });
    await waitFor(() => expect(editor.textContent).toContain("Denver"));
    expect(editor.textContent).toContain("172.20.0.0/24");

    fireEvent.click(within(editor).getByRole("button", { name: "Edit Denver" }));
    fireEvent.change(within(editor).getByLabelText("gw1 model"), {
      target: { value: SECOND_GROUND_NODE },
    });
    await waitFor(() => expect(commandCalls(fetchMock)).toHaveLength(3));
    expect(JSON.parse(commandCalls(fetchMock)[2]![1].body).command).toEqual({
      operation: "set_ground_site_node_model",
      segment_id: "ground-1",
      member_id: "member-1",
      node_id: "gw1",
      node_ref: SECOND_GROUND_NODE,
    });

    fireEvent.click(within(editor).getByRole("button", { name: "+ add node" }));
    await waitFor(() => expect(commandCalls(fetchMock)).toHaveLength(4));
    expect(JSON.parse(commandCalls(fetchMock)[3]![1].body).command).toEqual({
      operation: "add_ground_site_node",
      segment_id: "ground-1",
      member_id: "member-1",
    });
    await waitFor(() => expect(editor.textContent).toContain("gw2"));
  });

  it("discards a scheduling response when the editor changes in flight", async () => {
    let scheduleRequest: Record<string, any> | null = null;
    let resolveSchedule!: (response: unknown) => void;
    const pendingSchedule = new Promise<unknown>((resolve) => {
      resolveSchedule = resolve;
    });
    const fetchMock = stubFetch({
      commandHandler: (request) => {
        if (request.command.operation === "add_ground") {
          return Promise.resolve(visualCommandResponse(request));
        }
        scheduleRequest = request;
        return pendingSchedule;
      },
    });
    render(<BuilderView {...PROPS} />);
    fireEvent.click(
      within(await screen.findByTestId("builder-start")).getByRole("button", {
        name: /New session/i,
      }),
    );
    fireEvent.click(await screen.findByText("Ground sites"));
    const editor = await screen.findByTestId("builder-ground-editor");
    fireEvent.click(within(editor).getByRole("button", { name: /Scheduling/ }));
    fireEvent.change(within(editor).getByLabelText("Scheduling preset"), {
      target: { value: "geo-longest-pass" },
    });
    await waitFor(() => expect(commandCalls(fetchMock)).toHaveLength(2));
    fireEvent.change(within(editor).getByLabelText("name"), {
      target: { value: "Edited while waiting" },
    });
    resolveSchedule(visualCommandResponse(scheduleRequest!));

    await waitFor(() =>
      expect(editor.textContent).toContain(
        "an editor changed while the visual command was running; try again",
      ),
    );
    expect((within(editor).getByLabelText("name") as HTMLInputElement).value).toBe(
      "Edited while waiting",
    );
    expect((within(editor).getByLabelText("Scheduling preset") as HTMLSelectElement).value).toBe(
      "leo-fast-handover",
    );
  });

  it("routes connect and endpoint rederive through backend commands", async () => {
    const fetchMock = stubFetch();
    render(<BuilderView {...PROPS} />);
    fireEvent.click(
      within(await screen.findByTestId("builder-start")).getByRole("button", {
        name: /New session/i,
      }),
    );
    fireEvent.click(await screen.findByText("Space segments"));
    await waitFor(() => expect(commandCalls(fetchMock)).toHaveLength(1));
    fireEvent.click(await screen.findByRole("button", { name: "+ Add constellation" }));
    await waitFor(() => expect(commandCalls(fetchMock)).toHaveLength(2));

    fireEvent.click(
      screen.getByLabelText(
        "Connect Constellation 1: pick the other end — physics derive from both faceplates",
      ),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Constellation 2 (space)" }));
    await waitFor(() => expect(commandCalls(fetchMock)).toHaveLength(3));
    expect(JSON.parse(commandCalls(fetchMock)[2]![1].body).command).toEqual({
      operation: "connect_segments",
      from_segment_id: "space-1",
      to_segment_id: "space-2",
    });

    const linkEditor = await screen.findByTestId("builder-link-editor");
    fireEvent.change(within(linkEditor).getByLabelText("Endpoint B segment"), {
      target: { value: "space-1" },
    });
    await waitFor(() => expect(commandCalls(fetchMock)).toHaveLength(4));
    expect(JSON.parse(commandCalls(fetchMock)[3]![1].body).command).toEqual({
      operation: "rederive_link",
      rule_id: "link-1",
      side: "b",
      segment_id: "space-1",
    });
    expect((await within(linkEditor).findByTestId("rederive-note")).textContent).toContain(
      "re-derived by VS-API",
    );
  });

  it("an inactive builder lists sessions but never opens one implicitly", async () => {
    const fetchMock = stubFetch({
      sessions: [summary("nodalarc:sessions/hidden.yaml", "sessions")],
    });
    render(<BuilderView {...PROPS} active={false} />);
    await waitFor(() => expect(sessionsCalls(fetchMock).length).toBeGreaterThanOrEqual(1));
    // Catalog presence is never treated as an operational running-session
    // signal. Opening always requires an explicit picker gesture.
    await new Promise((resolve) => setTimeout(resolve, 60));
    expect(compileCalls(fetchMock)).toHaveLength(0);
  });

  it("opening the picker refetches the sessions list", async () => {
    const fetchMock = stubFetch();
    render(<BuilderView {...PROPS} />);
    await waitFor(() => expect(sessionsCalls(fetchMock).length).toBeGreaterThanOrEqual(1));
    const before = sessionsCalls(fetchMock).length;
    fireEvent.click(await screen.findByRole("button", { name: /Open a session/i }));
    await waitFor(() => expect(sessionsCalls(fetchMock).length).toBe(before + 1));
  });

  it("(resolved-but-preview-pending) a satellite-less resolved world shows the nudge, not a wall", async () => {
    const groundOnlyWorld = {
      session: { name: "ground-only" },
      nodes: [
        {
          node_id: "ground-gw1",
          local_node_id: "gw1",
          segment_id: "ground",
          namespace: "ground",
          kind: "ground_station",
          plane: null,
          slot: null,
          tags: [],
          surface_position: { lat_deg: 0, lon_deg: 0, alt_m: 0 },
          forwarding: "routed",
          terminal_inventory: [],
          interfaces: [],
          originated_prefixes: [],
        },
      ],
      link_rules: [],
      segments: [{ segment_id: "ground", display_name: "Ground" }],
      allocations: [],
      rule_previews: [],
      ephemeris: {
        epoch_id: 0,
        sim_time: "2000-01-01T12:00:00Z",
        epoch_unix: 0,
        nodes: {},
        body_frames: { earth: catalogEarthFrame() },
      },
      epoch_unix: 0,
    };
    const fetchMock = stubFetch({
      sessions: [summary("nodalarc:sessions/ground-only.yaml", "sessions")],
      sessionDocument: { session: { name: "ground-only" } },
      sessionPreview: groundOnlyWorld,
    });
    render(<BuilderView {...PROPS} />);
    fireEvent.click(await screen.findByRole("button", { name: /Open a session/i }));
    fireEvent.click(await screen.findByTitle("Open ground-only"));
    fireEvent.click(await screen.findByRole("button", { name: "Open editable copy" }));
    await waitFor(() => expect(fetchMock.mock.calls.some((call) =>
      String(call[0]).includes("/builder/draft/open"),
    )).toBe(true));
    const openCall = fetchMock.mock.calls.find((call) =>
      String(call[0]).includes("/builder/draft/open"),
    )!;
    expect(JSON.parse(openCall[1]?.body ?? "{}")).toEqual({
      source_ref: "nodalarc:sessions/ground-only.yaml",
      target_ref: "user:sessions/ground-only-copy.yaml",
    });
    await waitFor(
      () => expect(screen.getByTestId("builder-status").textContent).toContain("✓ resolves"),
      { timeout: 3000 },
    );
    const status = screen.getByTestId("builder-status").textContent ?? "";
    expect(status).toContain("no satellites yet — add one to run contact previews");
    // A valid ground-only session is NOT a resolver refusal.
    expect(status).not.toContain("does not resolve");
  });
});

describe("BuilderView — Library Use places and reveals", () => {
  // The catalog cache is module-global; reset it per case so each mounts with a
  // fresh fetch (an earlier suite fetching a family empty would otherwise stick).
  beforeEach(() => resetCatalogStores());
  // Renders BuilderView and invokes onUse through the real Library panel. A
  // draft family opens the created object's editor with create-focus; a ref
  // family places its outline row and flashes it (the separate outline reveal),
  // never opening an editor; an unusable entry surfaces an error, never a
  // silent no-op.
  function catalogFor(family: string | null) {
    switch (family) {
      case "constellations":
        return [summary("nodalarc:constellations/leo.yaml", "constellations")];
      case "site-sets":
        return [summary("nodalarc:site-sets/gw.yaml", "site-sets")];
      case "nodes":
        return [summary(GROUND_NODE, "nodes")];
      case "sites":
        return [summary("nodalarc:sites/orphan.yaml", "sites")];
      case "terminals":
        return [summary("nodalarc:terminals/ka.yaml", "terminals")];
      default:
        return [];
    }
  }
  function stubCatalog(options?: {
    commandHandler?: (request: Record<string, any>) => Promise<unknown>;
  }) {
    let blankDraftCount = 0;
    const fetchMock = vi.fn((url: string, init?: { body?: string }) => {
      if (url.includes("/builder/bootstrap")) return Promise.resolve(bootstrapResponse());
      if (url.includes("/builder/draft/new")) {
        const request = init?.body ? JSON.parse(init.body) : {};
        const sessionName = request.session_name ?? `untitled-session-${++blankDraftCount}`;
        return Promise.resolve(jsonResponse(structuredVisualDraft(sessionName)));
      }
      if (url.includes("/builder/catalog/get")) {
        const request = init?.body ? JSON.parse(init.body) : {};
        if (request.ref === "nodalarc:sites/orphan.yaml") {
          return Promise.resolve(
            jsonResponse(catalogDocument(request.ref, "sites", { site: { display_name: "No-Id Site" } })),
          );
        }
        return Promise.resolve(
          jsonResponse(catalogDocument(GROUND_NODE, "nodes", { node: { id: "gateway", terminals: [] } })),
        );
      }
      if (url.includes("/builder/catalog/draft/open")) {
        const request = init?.body ? JSON.parse(init.body) : {};
        const id = String(request.target_ref).split("/").pop()?.replace(/\.ya?ml$/, "");
        return Promise.resolve(
          jsonResponse({
            contract_version: 1,
            draft_revision: 0,
            family: "terminals",
            target_ref: request.target_ref,
            source_ref: request.source_ref,
            expected_source_revision: `revision-${request.source_ref}`,
            expected_target_revision: null,
            document: {
              terminal: { id, display_name: "Forked terminal", medium: "rf" },
            },
            projected_yaml: `terminal:\n  display_name: Forked terminal\n  id: ${id}\n  medium: rf\n`,
            control_tree: {
              projection_revision: 0,
              root: {
                control_id: "ctl_root",
                json_pointer: "",
                label: "Terminal",
                required: true,
                present: true,
                model_name: "Terminal",
                fields: [],
              },
            },
            issues: [],
          }),
        );
      }
      if (url.includes("/builder/catalog/dependents")) {
        const request = init?.body ? JSON.parse(init.body) : {};
        return Promise.resolve(jsonResponse({
          target_ref: request.ref,
          target_revision: "revision-target",
          direct_dependents: [],
          transitive_dependents: [],
          overwrite_affects_dependents: false,
          delete_allowed: true,
          acknowledgement: "impact-recovery",
        }));
      }
      if (url.includes("/builder/catalog/list")) {
        const family = init?.body ? JSON.parse(init.body).family : null;
        return Promise.resolve(
          jsonResponse({ generation: "g1", items: catalogFor(family), next_page_token: null }),
        );
      }
      if (url.includes("/builder/draft/compile")) {
        const request = init?.body ? JSON.parse(init.body) : {};
        return Promise.resolve(compileResponse(request));
      }
      if (url.includes("/builder/draft/command")) {
        const request = init?.body ? JSON.parse(init.body) : {};
        if (options?.commandHandler) return options.commandHandler(request);
        return Promise.resolve(visualCommandResponse(request));
      }
      return Promise.resolve(jsonResponse({}));
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    return fetchMock;
  }
  async function openLibrary() {
    const start = await screen.findByTestId("builder-start");
    fireEvent.click(within(start).getByRole("button", { name: /New session/i }));
    fireEvent.click(await screen.findByLabelText(/^Library —/));
  }

  it("using a constellation ref places its outline row and flashes exactly it", async () => {
    const fetchMock = stubCatalog();
    render(<BuilderView {...PROPS} />);
    await openLibrary();
    // Default family is constellations (a ref). Use its entry.
    fireEvent.click(await screen.findByLabelText("Use: place as a space segment"));
    await waitFor(() => expect(commandCalls(fetchMock)).toHaveLength(1));
    expect(JSON.parse(commandCalls(fetchMock)[0]![1].body).command).toEqual({
      operation: "place_space_reference",
      source_ref: "nodalarc:constellations/leo.yaml",
    });
    const outline = screen.getByTestId("builder-outline");
    await within(outline).findByText("leo");
    await waitFor(() => {
      const flashed = outline.querySelectorAll(".builder-outline-row--revealed");
      expect(flashed).toHaveLength(1); // exactly one reveal — not a broadcast (pin 2)
      expect(flashed[0]?.textContent).toContain("leo");
    });
  });

  it("using a site-set ref places its ground row and flashes exactly it", async () => {
    const fetchMock = stubCatalog();
    render(<BuilderView {...PROPS} />);
    await openLibrary();
    fireEvent.click(await screen.findByRole("tab", { name: "Site sets" }));
    fireEvent.click(await screen.findByRole("button", { name: "Use: place as ground sites" }));
    await waitFor(() => expect(commandCalls(fetchMock)).toHaveLength(1));
    expect(JSON.parse(commandCalls(fetchMock)[0]![1].body).command).toEqual({
      operation: "place_ground_reference",
      site_set_ref: "nodalarc:site-sets/gw.yaml",
    });
    const outline = screen.getByTestId("builder-outline");
    await within(outline).findByText("gw");
    await waitFor(() => {
      const flashed = outline.querySelectorAll(".builder-outline-row--revealed");
      expect(flashed).toHaveLength(1);
      expect(flashed[0]?.textContent).toContain("gw");
    });
  });

  it("using a draft family opens the created segment's editor with create-focus", async () => {
    const fetchMock = stubCatalog();
    render(<BuilderView {...PROPS} />);
    await openLibrary();
    fireEvent.click(await screen.findByRole("tab", { name: "Nodes" }));
    fireEvent.click(await screen.findByLabelText("Use: start a constellation with this node"));
    // The created segment lands in the outline drafts, and freshId sets
    // create-focus: the opened editor's name field is focused.
    const editor = await screen.findByTestId("builder-editor");
    const name = within(editor).getByLabelText("name") as HTMLInputElement;
    await waitFor(() => expect(document.activeElement).toBe(name));
    expect(screen.getByTestId("builder-drafts")).toBeTruthy();
    expect(name.value).toBe("Constellation 1");
    const command = JSON.parse(commandCalls(fetchMock)[0]![1].body);
    expect(command.command).toEqual({
      operation: "add_generated_space",
      phasing_mode: "walker_delta",
      node_ref: GROUND_NODE,
    });
  });

  it("using a typed site summary places its ref-derived catalog identity", async () => {
    const fetchMock = stubCatalog();
    render(<BuilderView {...PROPS} />);
    await openLibrary();
    fireEvent.click(await screen.findByRole("tab", { name: "Sites" }));
    fireEvent.click(await screen.findByRole("button", { name: "Use: add to a ground segment" }));
    await waitFor(() => expect(commandCalls(fetchMock)).toHaveLength(1));
    expect(JSON.parse(commandCalls(fetchMock)[0]![1].body).command).toEqual({
      operation: "add_ground_site_reference",
      site_ref: "nodalarc:sites/orphan.yaml",
    });
    expect(await screen.findByTestId("builder-ground-editor")).toBeTruthy();
    expect(screen.getAllByText("orphan").length).toBeGreaterThan(0);
  });

  it("a refused site placement leaves no empty ground segment", async () => {
    const fetchMock = stubCatalog({
      commandHandler: async () =>
        jsonResponse(
          { code: "catalog_authoring.invalid_graph", message: "site unavailable" },
          false,
          409,
        ),
    });
    render(<BuilderView {...PROPS} />);
    await openLibrary();
    fireEvent.click(await screen.findByRole("tab", { name: "Sites" }));
    fireEvent.click(await screen.findByRole("button", { name: "Use: add to a ground segment" }));

    expect((await screen.findAllByText("site unavailable")).length).toBeGreaterThan(0);
    expect(commandCalls(fetchMock)).toHaveLength(1);
    expect(JSON.parse(commandCalls(fetchMock)[0]![1].body).command).toEqual({
      operation: "add_ground_site_reference",
      site_ref: "nodalarc:sites/orphan.yaml",
    });
    expect(document.querySelector('[data-segment-id="ground-1"]')).toBeNull();
    expect(screen.queryByTestId("builder-ground-editor")).toBeNull();
  });

  it("customize requires an explicit user identity and opens a lossless backend draft", async () => {
    const fetchMock = stubCatalog();
    render(<BuilderView {...PROPS} />);
    fireEvent.click(await screen.findByLabelText(/^Library —/));
    fireEvent.click(await screen.findByRole("tab", { name: "Terminals" }));
    fireEvent.click(await screen.findByLabelText("Customize: fork into your library"));
    const fork = await screen.findByTestId("builder-fork-draft");
    const id = within(fork).getByRole("textbox");
    fireEvent.change(id, { target: { value: "my-ka" } });
    fireEvent.click(within(fork).getByRole("button", { name: "Fork" }));

    const editor = await screen.findByTestId("catalog-terminal-form");
    const call = fetchMock.mock.calls.find((item) =>
      String(item[0]).includes("/builder/catalog/draft/open"),
    );
    expect(call).toBeTruthy();
    expect(JSON.parse(call?.[1]?.body ?? "{}")).toMatchObject({
      source_ref: "nodalarc:terminals/ka.yaml",
      target_ref: "user:terminals/my-ka.yaml",
    });
    expect((within(editor).getByLabelText("name") as HTMLInputElement).value).toBe(
      "Forked terminal",
    );
  });

  it("restores a dirty component draft after close and browser remount", async () => {
    const componentRecovery = {
      draft: {
        contract_version: 1 as const,
        draft_revision: 6,
        family: "terminals" as const,
        target_ref: "user:terminals/recovered-ka.yaml",
        source_ref: "nodalarc:terminals/ka.yaml",
        expected_source_revision: "revision-source",
        expected_target_revision: "revision-target",
        document: {
          terminal: { id: "recovered-ka", display_name: "Saved name", medium: "rf" },
        },
        projected_yaml: "terminal:\n  display_name: Saved name\n  id: recovered-ka\n  medium: rf\n",
        control_tree: {
          projection_revision: 6,
          root: {
            control_id: "ctl_root",
            json_pointer: "",
            label: "Terminal",
            required: true,
            present: true,
            model_name: "Terminal",
            fields: [],
          },
        },
        issues: [],
      },
      baselineDocument: {
        terminal: { id: "recovered-ka", display_name: "Saved name", medium: "rf" },
      },
      workingDocument: {
        terminal: { id: "recovered-ka", display_name: "Dirty recovered name", medium: "rf" },
      },
      yamlText: "terminal:\n  display_name: Saved name\n  id: recovered-ka\n  medium: rf\n",
      appliedYamlText: "terminal:\n  display_name: Saved name\n  id: recovered-ka\n  medium: rf\n",
      canonicalizationRequired: false,
      canonicalizationAccepted: false,
    };
    const recoveryScope = builderRecoveryScope();
    expect(writeCatalogDraftRecovery(componentRecovery, recoveryScope)).toBe(true);
    stubCatalog();
    const first = render(<BuilderView {...PROPS} />);

    expect((within(await screen.findByTestId("catalog-terminal-form")).getByLabelText(
      "name",
    ) as HTMLInputElement).value).toBe("Dirty recovered name");
    fireEvent.click(
      within(screen.getByTestId("catalog-draft-editor")).getByRole("button", { name: "Close" }),
    );
    await waitFor(() => expect(screen.queryByTestId("catalog-terminal-form")).toBeNull());
    expect(localStorage.getItem(recoveryStorageKey(
      recoveryScope,
      "catalog",
      componentRecovery.draft.target_ref,
    ))).not.toBeNull();

    fireEvent.click(screen.getByLabelText(/Library — resume unsaved/));
    expect((within(await screen.findByTestId("catalog-terminal-form")).getByLabelText(
      "name",
    ) as HTMLInputElement).value).toBe("Dirty recovered name");
    first.unmount();

    render(<BuilderView {...PROPS} />);
    expect((within(await screen.findByTestId("catalog-terminal-form")).getByLabelText(
      "name",
    ) as HTMLInputElement).value).toBe("Dirty recovered name");
  });
});

describe("BuilderView — deploy gate toolbar wiring", () => {
  it("the deploy verb is disabled and shows canDeploy's reason as its label when unsaved", async () => {
    stubFetch();
    render(<BuilderView {...PROPS} />);
    const start = await screen.findByTestId("builder-start");
    fireEvent.click(within(start).getByRole("button", { name: /New session/i }));
    // A fresh, unsaved session: canDeploy gates deploy, and the gate's REASON is
    // the button's own label (gate.reason → label), while gate.ok=false disables
    // it (gate.ok → the click). A broken wiring would enable it or mislabel it.
    const deploy = await screen.findByRole("button", {
      name: "save the session first, then deploy",
    });
    expect((deploy as HTMLButtonElement).disabled).toBe(true);
  });
});
