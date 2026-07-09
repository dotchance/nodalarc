// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** The session-document → workspace inverse.
 *
 *  The import path — deserialize a session document back into an editable
 *  workspace, or refuse with the offending paths. It mints ids through the
 *  counter transaction workspace.ts owns (a refused import advances no counter),
 *  never the raw `let`s, and consumes the mint-free parse core one-way. The
 *  fork wrappers (draftConstellationFromDocuments / draftGroundSetFromDocuments)
 *  stay in workspace.ts; this module is the collect-issues-and-continue side.
 */
import {
  DE440S_EPHEMERIS,
  LINK_MEDIA,
  MOUNT_ROLES,
  draftGroundMember,
  draftNodeFromDocument,
  emittedDomainId,
  emittedRuleId,
  identifier,
  reseedCounters,
  memberSiteId,
  mintBoundaryId,
  mintLinkRuleId,
  mintRoutingDomainId,
  newDraftGroundSet,
  newWorkspace,
  presetForSchedulingBlock,
  refGroundMember,
  runCounterTransaction,
  toSessionDocument,
  type DraftBoundary,
  type DraftConstellation,
  type DraftGroundSet,
  type DraftGroundSite,
  type DraftLinkEndpoint,
  type DraftNode,
  type DraftRoutingDomain,
  type LinkMedium,
  type MountRole,
  type Workspace,
} from "./workspace";
import {
  buildDraftOrbit,
  constellationGeometry,
  deepEqual,
  orbitLacksShape,
  parseGroundMember,
  refStem,
  unsupportedPhasingMode,
  unsupportedPropagator,
  type GroundMemberParse,
} from "./workspaceParseCore";

export type WorkspaceImport =
  | { workspace: Workspace; issues?: undefined }
  | { workspace?: undefined; issues: string[] };

/** Mint a ground member from an id-free candidate (de-mint): the import
 *  path mints inside the counter transaction, so a refusal leaks no member id. */
function _mintMember(parse: Exclude<GroundMemberParse, { reason: string }>): DraftGroundSite {
  return parse.kind === "ref"
    ? refGroundMember(parse.ref, parse.site_id, parse.label, null)
    : draftGroundMember(parse.site);
}

function _diffPaths(a: unknown, b: unknown, path: string, out: string[], cap: number): void {
  if (out.length >= cap || deepEqual(a, b)) return;
  const isObj = (v: unknown) => typeof v === "object" && v !== null && !Array.isArray(v);
  if (Array.isArray(a) && Array.isArray(b)) {
    if (a.length !== b.length) {
      out.push(`${path || "document"} (${a.length} vs ${b.length} entries)`);
      return;
    }
    for (let i = 0; i < a.length && out.length < cap; i++) {
      _diffPaths(a[i], b[i], `${path}[${i}]`, out, cap);
    }
    return;
  }
  if (isObj(a) && isObj(b)) {
    const keys = new Set([...Object.keys(a as object), ...Object.keys(b as object)]);
    for (const k of keys) {
      if (out.length >= cap) return;
      _diffPaths(
        (a as Record<string, unknown>)[k],
        (b as Record<string, unknown>)[k],
        path ? `${path}.${k}` : k,
        out,
        cap,
      );
    }
    return;
  }
  out.push(path || "document");
}

function _importConstellation(
  segId: string,
  source: Record<string, unknown>,
  startTime: string,
  issues: string[],
): DraftConstellation | null {
  const wrapper = Object.keys(source)[0] ?? "?";
  const constellation = source.constellation as Record<string, unknown> | undefined;
  if (!constellation) {
    issues.push(`segments.${segId}: uses ${wrapper} — the builder edits constellations only`);
    return null;
  }
  const before = issues.length;
  const badMode = unsupportedPhasingMode(constellation);
  if (badMode) {
    issues.push(`segments.${segId}: phasing mode ${badMode} is not editable yet`);
  }
  const orbitRaw = constellation.orbit;
  if (typeof orbitRaw !== "object" || orbitRaw === null) {
    issues.push(`segments.${segId}: orbit by reference is not editable yet`);
    return null;
  }
  const orbit = orbitRaw as Record<string, unknown>;
  const lacksShape = orbitLacksShape(orbit);
  if (lacksShape) issues.push(`segments.${segId}: element-form orbits are not editable yet`);
  const badPropagator = unsupportedPropagator(orbit);
  if (badPropagator) {
    issues.push(`segments.${segId}: propagator ${badPropagator} is not editable yet`);
  }
  if (String(orbit.epoch ?? startTime) !== startTime) {
    issues.push(
      `segments.${segId}: orbit epoch differs from the session start_time — the builder authors one session epoch`,
    );
  }
  if (issues.length > before || lacksShape) return null;
  const node = constellation.node;
  let nodeDraft: DraftNode | null = null;
  if (typeof node === "object" && node !== null) {
    try {
      nodeDraft = draftNodeFromDocument({ node: node as Record<string, unknown> });
    } catch (e) {
      issues.push(`segments.${segId}: ${e instanceof Error ? e.message : String(e)}`);
      return null;
    }
  }
  return {
    segment_id: segId,
    display_name: String(constellation.display_name ?? constellation.id ?? segId),
    node_ref: typeof node === "string" ? node : "",
    node_draft: nodeDraft,
    orbit: buildDraftOrbit(orbit),
    ...constellationGeometry(constellation),
  };
}

function _importGroundDraft(
  segId: string,
  raw: Record<string, unknown>,
  siteSet: Record<string, unknown>,
  issues: string[],
): DraftGroundSet | null {
  const before = issues.length;
  const members: DraftGroundSite[] = [];
  let stampNodeRef = "";
  let stampInstalled: Record<string, number> = {};
  for (const entry of (siteSet.sites as unknown[] | undefined) ?? []) {
    if (typeof entry === "string") {
      const parse = parseGroundMember(entry, null);
      if (!("reason" in parse)) members.push(_mintMember(parse));
      continue;
    }
    const wrapped = entry as Record<string, unknown>;
    try {
      const parse = parseGroundMember(null, wrapped);
      if ("reason" in parse) {
        issues.push(`segments.${segId}: a site entry is neither a ref nor an inline site`);
        continue;
      }
      const member = _mintMember(parse);
      members.push(member);
      const [firstNode] = member.site?.nodes ?? [];
      if (!stampNodeRef && firstNode) {
        stampNodeRef = firstNode.model_ref;
        stampInstalled = { ...firstNode.installed };
      }
    } catch (e) {
      issues.push(`segments.${segId}: ${e instanceof Error ? e.message : String(e)}`);
    }
  }
  const apply = (raw.apply ?? {}) as Record<string, unknown>;
  const preset = presetForSchedulingBlock(apply.scheduling);
  if (preset === null) {
    issues.push(`segments.${segId}: scheduling block matches no builder preset`);
  }
  const originated = (apply.originated_prefixes ?? null) as Record<string, unknown> | null;
  const base = newDraftGroundSet(
    stampNodeRef,
    stampInstalled,
    members.find((m) => m.site !== null)?.site?.body,
  );
  const draft: DraftGroundSet = {
    ...base,
    segment_id: segId,
    display_name: String(raw.display_name ?? siteSet.display_name ?? segId),
    members,
    scheduling_preset: preset ?? base.scheduling_preset,
    originated_ipv4: ((originated?.ipv4 as unknown[] | undefined) ?? []).map(String),
    tags: ((apply.tags as unknown[] | undefined) ?? []).map(String),
  };
  for (const overrideRaw of (raw.overrides as Record<string, unknown>[] | undefined) ?? []) {
    const match = (overrideRaw.match ?? {}) as Record<string, unknown>;
    const member = draft.members.find((m) => identifier(memberSiteId(m)) === match.site);
    const overridePreset = presetForSchedulingBlock(overrideRaw.scheduling);
    if (!member || overridePreset === null) {
      issues.push(
        `segments.${segId}: an override targets ${String(match.site)} with a block the builder cannot edit`,
      );
      continue;
    }
    member.scheduling_override = overridePreset;
  }
  return issues.length > before ? null : draft;
}

function _importEndpoint(
  where: string,
  raw: Record<string, unknown>,
  issues: string[],
): DraftLinkEndpoint | null {
  const before = issues.length;
  const select = (raw.select ?? {}) as Record<string, unknown>;
  let segmentId = "";
  let tag: string | null = null;
  if (typeof select.segment === "string") {
    segmentId = select.segment;
  } else if (Array.isArray(select.all)) {
    for (const leaf of select.all as Record<string, unknown>[]) {
      if (typeof leaf.segment === "string") segmentId = leaf.segment;
      else if (typeof leaf.tag === "string") tag = leaf.tag;
      else issues.push(`${where}: selector leaf the builder cannot edit`);
    }
    if (!segmentId) issues.push(`${where}: endpoint selects no segment`);
  } else {
    issues.push(`${where}: endpoint selector shape is not editable yet`);
  }
  const terminal = (raw.terminal ?? {}) as Record<string, unknown>;
  let role: MountRole | null = null;
  let medium: LinkMedium | null = null;
  for (const leaf of (terminal.all as Record<string, unknown>[] | undefined) ?? []) {
    if (typeof leaf.role === "string" && (MOUNT_ROLES as readonly string[]).includes(leaf.role)) {
      role = leaf.role as MountRole;
    } else if (
      typeof leaf.medium === "string" &&
      (LINK_MEDIA as readonly string[]).includes(leaf.medium)
    ) {
      medium = leaf.medium as LinkMedium;
    } else {
      issues.push(`${where}: terminal selector leaf the builder cannot edit`);
    }
  }
  if (role === null || medium === null) {
    issues.push(`${where}: terminal selector must carry one role and one medium`);
  }
  if (issues.length > before) return null;
  return {
    segment_id: segmentId,
    tag,
    role: role as MountRole,
    medium: medium as LinkMedium,
    min_elevation_deg:
      raw.min_elevation_deg === undefined ? null : Number(raw.min_elevation_deg),
  };
}

export function workspaceFromSessionDocument(
  document: Record<string, unknown>,
): WorkspaceImport {
  // A refused import is a pure no-op: it must advance no module id counter, and
  // the fidelity re-serialize can throw on a pathological id collision (// uniquify cap). The counter transaction (workspace.ts) snapshots every family
  // on entry and restores on any refusal (result.issues) or throw, keeping the
  // advanced counters only on a clean import — the guarantee across the seam.
  return runCounterTransaction((): WorkspaceImport => {
    try {
      return _importSessionDocument(document);
    } catch (e) {
      return {
        issues: [
          `the builder cannot reproduce this session: ${
            e instanceof Error ? e.message : String(e)
          }`,
        ],
      };
    }
  });
}

/** Parse a session document back into a workspace, or refuse with the
 *  offending paths. Wrapped by workspaceFromSessionDocument, which owns the
 *  counter-leak guard: a refused import advances no module id counter, and the
 *  fidelity re-serialize can throw on a pathological id collision. */
function _importSessionDocument(
  document: Record<string, unknown>,
): WorkspaceImport {
  const issues: string[] = [];
  const KNOWN_TOP = new Set([
    "session",
    "segments",
    "link_rules",
    "routing",
    "simulation",
    "time",
    "ephemeris",
  ]);
  for (const key of Object.keys(document)) {
    if (!KNOWN_TOP.has(key)) issues.push(`${key}: the builder cannot author this block yet`);
  }
  const session = (document.session ?? {}) as Record<string, unknown>;
  const ws = newWorkspace(String(session.name ?? "untitled-session"));
  const time = (document.time ?? null) as Record<string, unknown> | null;
  if (time) {
    ws.start_time = String(time.start_time ?? ws.start_time);
    ws.step_seconds = Number(time.step_seconds ?? 1);
    ws.compression = Number(time.compression ?? 1);
  } else {
    issues.push("time: missing — the builder always authors an explicit time block");
  }
  const limits = ((document.simulation as Record<string, unknown> | undefined)
    ?.candidate_limits ?? null) as Record<string, unknown> | null;
  if (limits) {
    ws.max_pairs_per_rule = Number(limits.max_pairs_per_rule ?? ws.max_pairs_per_rule);
    ws.max_pairs_per_tick = Number(limits.max_pairs_per_tick ?? ws.max_pairs_per_tick);
  }
  if (
    document.ephemeris !== undefined &&
    !deepEqual(document.ephemeris, DE440S_EPHEMERIS)
  ) {
    issues.push("ephemeris: a custom kernel manifest is not editable yet");
  }

  for (const raw of (document.segments as Record<string, unknown>[] | undefined) ?? []) {
    const segId = String(raw.id ?? "");
    if (!segId) {
      issues.push("segments: a segment carries no id");
      continue;
    }
    const source = raw.source;
    const placement = raw.placement as Record<string, unknown> | undefined;
    if (typeof source === "string") {
      ws.space_refs.push({ segment_id: segId, ref: source, label: refStem(source) });
    } else if (typeof source === "object" && source !== null) {
      const draft = _importConstellation(
        segId,
        source as Record<string, unknown>,
        ws.start_time,
        issues,
      );
      if (draft) ws.space.push(draft);
    } else if (placement) {
      const fromSiteSet = placement.from_site_set;
      if (typeof fromSiteSet === "string") {
        const apply = (raw.apply ?? {}) as Record<string, unknown>;
        const preset = presetForSchedulingBlock(apply.scheduling);
        if (preset === null) {
          issues.push(`segments.${segId}: scheduling block matches no builder preset`);
        } else {
          ws.ground_refs.push({
            segment_id: segId,
            ref: fromSiteSet,
            label: refStem(fromSiteSet),
            scheduling_preset: preset,
          });
        }
      } else if (typeof fromSiteSet === "object" && fromSiteSet !== null) {
        const siteSet = (fromSiteSet as Record<string, unknown>).site_set;
        if (typeof siteSet !== "object" || siteSet === null) {
          issues.push(`segments.${segId}: placement shape is not editable yet`);
        } else {
          const draft = _importGroundDraft(
            segId,
            raw,
            siteSet as Record<string, unknown>,
            issues,
          );
          if (draft) ws.ground.push(draft);
        }
      } else {
        issues.push(`segments.${segId}: placement shape is not editable yet`);
      }
    } else {
      issues.push(`segments.${segId}: segment shape the builder cannot edit yet`);
    }
  }

  for (const raw of (document.link_rules as Record<string, unknown>[] | undefined) ?? []) {
    const id = String(raw.id ?? "");
    const where = `link_rules.${id || "?"}`;
    const topology = (raw.topology ?? {}) as Record<string, unknown>;
    const mode = String(topology.mode ?? "");
    if (mode !== "visible_candidates" && mode !== "nearest_n") {
      issues.push(`${where}: topology ${mode || "?"} is not editable in the builder yet`);
      continue;
    }
    const endpoints = (raw.endpoints as Record<string, unknown>[] | undefined) ?? [];
    if (endpoints.length !== 2) {
      issues.push(`${where}: rules have exactly two endpoints`);
      continue;
    }
    const a = _importEndpoint(where, endpoints[0] as Record<string, unknown>, issues);
    const b = _importEndpoint(where, endpoints[1] as Record<string, unknown>, issues);
    if (!a || !b) continue;
    const constraints = (raw.constraints ?? {}) as Record<string, unknown>;
    ws.links.push({
      rule_id: mintLinkRuleId(),
      label: id,
      enabled: raw.enabled !== false,
      a,
      b,
      topology_mode: mode,
      topology_n: Number(topology.n ?? 2),
      max_range_km:
        constraints.max_range_km === undefined ? null : Number(constraints.max_range_km),
    });
  }

  const routing = (document.routing ?? null) as Record<string, unknown> | null;
  for (const raw of (routing?.domains as Record<string, unknown>[] | undefined) ?? []) {
    const id = String(raw.id ?? "");
    const where = `routing.domains.${id || "?"}`;
    const members: string[] = [];
    const selectors = (raw.selectors as Record<string, unknown>[] | undefined) ?? [];
    const [selector] = selectors;
    if (selectors.length === 1 && selector && typeof selector.segment === "string") {
      members.push(selector.segment);
    } else if (selectors.length === 1 && selector && Array.isArray(selector.any)) {
      for (const leaf of selector.any as Record<string, unknown>[]) {
        if (typeof leaf.segment === "string") members.push(leaf.segment);
        else issues.push(`${where}: selector leaf the builder cannot edit`);
      }
    } else {
      issues.push(`${where}: selector shape is not editable yet`);
      continue;
    }
    const timers = (raw.timers ?? null) as Record<string, unknown> | null;
    ws.routing_domains.push({
      domain_id: mintRoutingDomainId(),
      label: id,
      protocol: String(raw.protocol) as DraftRoutingDomain["protocol"],
      member_segment_ids: members,
      hello_interval_s: timers ? Number(timers.hello_interval_s) : null,
      hold_interval_s: timers ? Number(timers.hold_interval_s) : null,
    });
  }
  for (const raw of (routing?.boundaries as Record<string, unknown>[] | undefined) ?? []) {
    const over = String(raw.over ?? "");
    const where = `routing.boundaries.${over || "?"}`;
    const rule = ws.links.find((r) => emittedRuleId(r) === over);
    const exports = (raw.export as Record<string, unknown>[] | undefined) ?? [];
    const [out, back] = exports;
    const domainByEmitted = (emitted: unknown) =>
      ws.routing_domains.find((d) => emittedDomainId(d) === emitted);
    const fromDomain = out ? domainByEmitted(out.from) : undefined;
    const toDomain = out ? domainByEmitted(out.to) : undefined;
    const exchangeShape = (entry: Record<string, unknown> | undefined) =>
      entry !== undefined &&
      deepEqual(entry.prefixes, { aggregate_of: "originated" }) &&
      entry.install_via === "peer_loopback" &&
      typeof entry.export_node_loopbacks === "boolean";
    if (
      !rule ||
      !fromDomain ||
      !toDomain ||
      exports.length !== 2 ||
      !exchangeShape(out) ||
      !exchangeShape(back) ||
      back?.from !== out?.to ||
      back?.to !== out?.from ||
      back?.export_node_loopbacks !== out?.export_node_loopbacks
    ) {
      issues.push(`${where}: boundary shape is not the builder's exchange pattern`);
      continue;
    }
    ws.boundaries.push({
      boundary_id: mintBoundaryId(),
      over_rule_id: rule.rule_id,
      adapter: String(raw.adapter) as DraftBoundary["adapter"],
      from_domain_id: fromDomain.domain_id,
      to_domain_id: toDomain.domain_id,
      export_node_loopbacks: Boolean(out?.export_node_loopbacks),
    });
  }

  if (issues.length) return { issues };
  // The fidelity proof: a workspace that does not re-serialize to exactly
  // the source document refuses — otherwise "editing the running session"
  // would silently edit something else.
  const diffs: string[] = [];
  _diffPaths(document, toSessionDocument(ws), "", diffs, 6);
  if (diffs.length) {
    return { issues: diffs.map((p) => `${p}: the builder cannot reproduce this value`) };
  }
  reseedCounters(ws);
  return { workspace: ws };
}
