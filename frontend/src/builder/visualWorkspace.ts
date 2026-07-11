/** UI workspace ↔ backend visual-authoring DTO adapter.
 *
 * This module maps only the typed visual application contract. It never reads,
 * writes, or interprets persisted NodalArc session/catalog grammar.
 */

import type {
  BuilderVisualDraftEnvelope,
  BuilderVisualWorkspace,
  JsonValue,
} from "./generated/builderApi";
import type { Workspace } from "./workspace";

const jsonRecord = (
  value: Record<string, unknown>,
): Readonly<Record<string, JsonValue>> =>
  value as Readonly<Record<string, JsonValue>>;

function requiredField<T>(value: T | undefined, path: string): T {
  if (value === undefined) {
    throw new Error(`backend visual draft omitted ${path}`);
  }
  return value;
}

function requiredValue<T>(value: T | null | undefined, path: string): T {
  if (value === undefined || value === null) {
    throw new Error(`backend visual draft has no ${path}`);
  }
  return value;
}

export function visualWorkspaceFromWorkspace(workspace: Workspace): BuilderVisualWorkspace {
  return {
    session_name: workspace.name,
    display_name: workspace.display_name,
    description: workspace.description,
    space: workspace.space.map((space) => ({
      segment_id: space.segment_id,
      display_name: space.display_name,
      node_ref: space.node_ref || null,
      node_draft: space.node_draft
        ? {
            id: space.node_draft.id,
            display_name: space.node_draft.display_name,
            forwarding: space.node_draft.forwarding,
            ethernet: space.node_draft.ethernet,
            terminals: space.node_draft.terminals.map((mount) => ({
              mount_id: mount.mount_id,
              role: mount.role,
              terminal_ref: mount.terminal_ref || null,
              count: mount.count,
              boresight: mount.boresight,
            })),
          }
        : null,
      orbit: { ...space.orbit },
      planes: space.planes,
      raan_spacing_deg: space.raan_spacing_deg,
      slots_per_plane: space.slots_per_plane,
      phasing_mode: space.phasing_mode,
      phase_offset_deg: space.phase_offset_deg,
    })),
    space_refs: workspace.space_refs.map((space) => ({
      segment_id: space.segment_id,
      source_ref: space.ref || null,
      label: space.label,
    })),
    ground: workspace.ground.map((ground) => ({
      segment_id: ground.segment_id,
      display_name: ground.display_name,
      members: ground.members.map((member) => ({
        member_id: member.member_id,
        kind: member.kind,
        ref: member.ref,
        site_id: member.site_id,
        label: member.label,
        summary: member.summary,
        site: member.site
          ? {
              site_id: member.site.site_id,
              display_name: member.site.display_name,
              body: member.site.body || null,
              lat_deg: member.site.lat_deg,
              lon_deg: member.site.lon_deg,
              alt_m: member.site.alt_m,
              lan_ipv4: member.site.lan_ipv4,
              tags: member.site.tags,
              nodes: member.site.nodes.map((node) => ({
                node_id: node.node_id,
                model_ref: node.model_ref || null,
                installed: node.installed,
                boresights: node.boresights,
                lo0_ipv4: node.lo0_ipv4,
                terr0_ipv4: node.terr0_ipv4,
              })),
            }
          : null,
        scheduling_override: member.scheduling_override
          ? jsonRecord(member.scheduling_override)
          : null,
      })),
      stamp: {
        node_ref: ground.stamp.node_ref || null,
        installed: ground.stamp.installed,
        boresights: ground.stamp.boresights,
        body: ground.stamp.body || null,
        lan_base: ground.stamp.lan_base,
        loopback_base: ground.stamp.loopback_base,
      },
      scheduling: jsonRecord(ground.scheduling),
      originated_ipv4: ground.originated_ipv4,
      tags: ground.tags,
    })),
    ground_refs: workspace.ground_refs.map((ground) => ({
      segment_id: ground.segment_id,
      site_set_ref: ground.ref,
      label: ground.label,
      scheduling: jsonRecord(ground.scheduling),
    })),
    links: workspace.links.map((link) => ({
      rule_id: link.rule_id,
      label: link.label,
      enabled: link.enabled,
      a: { ...link.a, role: link.a.role },
      b: { ...link.b, role: link.b.role },
      topology_mode: link.topology_mode,
      topology_n: link.topology_n,
      max_range_km: link.max_range_km,
    })),
    routing_domains: workspace.routing_domains.map((domain) => ({ ...domain })),
    boundaries: workspace.boundaries.map((boundary) => ({ ...boundary })),
    max_pairs_per_rule: workspace.max_pairs_per_rule,
    max_pairs_per_tick: workspace.max_pairs_per_tick,
    start_time: workspace.start_time,
    step_seconds: workspace.step_seconds,
    compression: workspace.compression,
  };
}

export function workspaceFromVisualDraft(draft: BuilderVisualDraftEnvelope): Workspace {
  if (draft.mode !== "structured" || !draft.workspace) {
    throw new Error("visual draft is not a structured workspace");
  }
  const workspace = draft.workspace;
  return {
    name: requiredField(workspace.session_name, "workspace.session_name"),
    display_name: requiredField(workspace.display_name, "workspace.display_name"),
    description: requiredField(workspace.description, "workspace.description"),
    space: requiredField(workspace.space, "workspace.space").map((space, spaceIndex) => ({
      segment_id: requiredField(space.segment_id, `workspace.space.${spaceIndex}.segment_id`),
      display_name: requiredField(space.display_name, `workspace.space.${spaceIndex}.display_name`),
      node_ref: space.node_ref ?? "",
      node_draft: space.node_draft
        ? {
            id: requiredField(space.node_draft.id, `workspace.space.${spaceIndex}.node.id`),
            display_name: requiredField(
              space.node_draft.display_name,
              `workspace.space.${spaceIndex}.node.display_name`,
            ),
            forwarding: requiredField(
              space.node_draft.forwarding,
              `workspace.space.${spaceIndex}.node.forwarding`,
            ),
            ethernet: [...requiredField(
              space.node_draft.ethernet,
              `workspace.space.${spaceIndex}.node.ethernet`,
            )],
            terminals: requiredField(
              space.node_draft.terminals,
              `workspace.space.${spaceIndex}.node.terminals`,
            ).map((mount, mountIndex) => ({
              mount_id: requiredField(
                mount.mount_id,
                `workspace.space.${spaceIndex}.node.terminals.${mountIndex}.mount_id`,
              ),
              role: requiredValue(
                mount.role,
                `workspace.space.${spaceIndex}.node.terminals.${mountIndex}.role`,
              ),
              terminal_ref: mount.terminal_ref ?? "",
              count: requiredValue(
                mount.count,
                `workspace.space.${spaceIndex}.node.terminals.${mountIndex}.count`,
              ),
              boresight: requiredField(
                mount.boresight,
                `workspace.space.${spaceIndex}.node.terminals.${mountIndex}.boresight`,
              ),
            })),
          }
        : null,
      orbit: {
        central_body: requiredValue(space.orbit?.central_body, `workspace.space.${spaceIndex}.orbit.central_body`),
        shape_kind: requiredValue(space.orbit?.shape_kind, `workspace.space.${spaceIndex}.orbit.shape_kind`),
        altitude_km: requiredValue(space.orbit?.altitude_km, `workspace.space.${spaceIndex}.orbit.altitude_km`),
        perigee_altitude_km: requiredValue(space.orbit?.perigee_altitude_km, `workspace.space.${spaceIndex}.orbit.perigee_altitude_km`),
        apogee_altitude_km: requiredValue(space.orbit?.apogee_altitude_km, `workspace.space.${spaceIndex}.orbit.apogee_altitude_km`),
        inclination_deg: requiredValue(space.orbit?.inclination_deg, `workspace.space.${spaceIndex}.orbit.inclination_deg`),
        raan_deg: requiredValue(space.orbit?.raan_deg, `workspace.space.${spaceIndex}.orbit.raan_deg`),
        argument_of_perigee_deg: requiredValue(space.orbit?.argument_of_perigee_deg, `workspace.space.${spaceIndex}.orbit.argument_of_perigee_deg`),
        mean_anomaly_deg: requiredValue(space.orbit?.mean_anomaly_deg, `workspace.space.${spaceIndex}.orbit.mean_anomaly_deg`),
        propagator: requiredValue(space.orbit?.propagator, `workspace.space.${spaceIndex}.orbit.propagator`),
      },
      planes: requiredValue(space.planes, `workspace.space.${spaceIndex}.planes`),
      raan_spacing_deg: requiredValue(space.raan_spacing_deg, `workspace.space.${spaceIndex}.raan_spacing_deg`),
      slots_per_plane: requiredValue(space.slots_per_plane, `workspace.space.${spaceIndex}.slots_per_plane`),
      phasing_mode: space.phasing_mode,
      phase_offset_deg: space.phase_offset_deg,
    })),
    space_refs: requiredField(workspace.space_refs, "workspace.space_refs").map((space) => ({
      segment_id: requiredField(space.segment_id, "workspace.space_refs.segment_id"),
      ref: space.source_ref ?? "",
      label: requiredField(space.label, "workspace.space_refs.label"),
    })),
    ground: requiredField(workspace.ground, "workspace.ground").map((ground, groundIndex) => ({
      segment_id: requiredField(ground.segment_id, `workspace.ground.${groundIndex}.segment_id`),
      display_name: requiredField(ground.display_name, `workspace.ground.${groundIndex}.display_name`),
      members: requiredField(
        ground.members,
        `workspace.ground.${groundIndex}.members`,
      ).map((member, memberIndex) => ({
        member_id: requiredField(
          member.member_id,
          `workspace.ground.${groundIndex}.members.${memberIndex}.member_id`,
        ),
        kind: member.kind,
        ref: member.ref ?? null,
        site_id: requiredField(
          member.site_id,
          `workspace.ground.${groundIndex}.members.${memberIndex}.site_id`,
        ),
        label: requiredField(
          member.label,
          `workspace.ground.${groundIndex}.members.${memberIndex}.label`,
        ),
        summary: member.summary ?? null,
        site: member.site
          ? {
              site_id: requiredField(member.site.site_id, "ground member site id"),
              display_name: requiredField(member.site.display_name, "ground member display name"),
              body: requiredValue(member.site.body, "ground member body"),
              lat_deg: requiredValue(member.site.lat_deg, "ground member latitude"),
              lon_deg: requiredValue(member.site.lon_deg, "ground member longitude"),
              alt_m: requiredValue(member.site.alt_m, "ground member altitude"),
              lan_ipv4: requiredField(member.site.lan_ipv4, "ground member LAN"),
              tags: [...requiredField(member.site.tags, "ground member tags")],
              nodes: requiredField(member.site.nodes, "ground member nodes").map((node) => ({
                node_id: requiredField(node.node_id, "ground member node id"),
                model_ref: node.model_ref ?? "",
                installed: { ...requiredField(node.installed, "ground member installed terminals") },
                boresights: { ...requiredField(node.boresights, "ground member boresights") },
                lo0_ipv4: requiredField(node.lo0_ipv4, "ground member loopback"),
                terr0_ipv4: requiredField(node.terr0_ipv4, "ground member LAN interface"),
              })),
            }
          : null,
        scheduling_override: member.scheduling_override
          ? ({ ...member.scheduling_override } as Record<string, unknown>)
          : null,
      })),
      stamp: {
        node_ref: ground.stamp.node_ref ?? "",
        installed: { ...requiredField(ground.stamp.installed, "ground stamp installed terminals") },
        boresights: { ...requiredField(ground.stamp.boresights, "ground stamp boresights") },
        body: requiredValue(ground.stamp.body, "ground stamp body"),
        lan_base: requiredField(ground.stamp.lan_base, "ground stamp LAN base"),
        loopback_base: requiredField(ground.stamp.loopback_base, "ground stamp loopback base"),
      },
      scheduling: { ...requiredField(ground.scheduling, "ground scheduling") },
      originated_ipv4: [...requiredField(ground.originated_ipv4, "ground originated prefixes")],
      tags: [...requiredField(ground.tags, "ground tags")],
    })),
    ground_refs: requiredField(workspace.ground_refs, "workspace.ground_refs").map((ground) => ({
      segment_id: requiredField(ground.segment_id, "workspace.ground_refs.segment_id"),
      ref: ground.site_set_ref ?? "",
      label: requiredField(ground.label, "workspace.ground_refs.label"),
      scheduling: { ...requiredField(ground.scheduling, "workspace.ground_refs.scheduling") },
    })),
    links: requiredField(workspace.links, "workspace.links").map((link, linkIndex) => ({
      rule_id: requiredField(link.rule_id, `workspace.links.${linkIndex}.rule_id`),
      label: requiredField(link.label, `workspace.links.${linkIndex}.label`),
      enabled: requiredField(link.enabled, `workspace.links.${linkIndex}.enabled`),
      a: {
        segment_id: requiredField(link.a?.segment_id, `workspace.links.${linkIndex}.a.segment_id`),
        tag: link.a?.tag ?? null,
        role: requiredValue(link.a?.role, `workspace.links.${linkIndex}.a.role`),
        medium: requiredValue(link.a?.medium, `workspace.links.${linkIndex}.a.medium`),
        min_elevation_deg: link.a?.min_elevation_deg ?? null,
      },
      b: {
        segment_id: requiredField(link.b?.segment_id, `workspace.links.${linkIndex}.b.segment_id`),
        tag: link.b?.tag ?? null,
        role: requiredValue(link.b?.role, `workspace.links.${linkIndex}.b.role`),
        medium: requiredValue(link.b?.medium, `workspace.links.${linkIndex}.b.medium`),
        min_elevation_deg: link.b?.min_elevation_deg ?? null,
      },
      topology_mode: requiredValue(
        link.topology_mode,
        `workspace.links.${linkIndex}.topology_mode`,
      ),
      topology_n: requiredValue(link.topology_n, `workspace.links.${linkIndex}.topology_n`),
      max_range_km: link.max_range_km ?? null,
    })),
    routing_domains: requiredField(
      workspace.routing_domains,
      "workspace.routing_domains",
    ).map((domain, domainIndex) => ({
      domain_id: requiredField(
        domain.domain_id,
        `workspace.routing_domains.${domainIndex}.domain_id`,
      ),
      label: requiredField(domain.label, `workspace.routing_domains.${domainIndex}.label`),
      protocol: requiredValue(
        domain.protocol,
        `workspace.routing_domains.${domainIndex}.protocol`,
      ),
      member_segment_ids: [...requiredField(
        domain.member_segment_ids,
        `workspace.routing_domains.${domainIndex}.member_segment_ids`,
      )],
      hello_interval_s: domain.hello_interval_s ?? null,
      hold_interval_s: domain.hold_interval_s ?? null,
    })),
    boundaries: requiredField(workspace.boundaries, "workspace.boundaries").map((boundary, boundaryIndex) => ({
      boundary_id: requiredField(
        boundary.boundary_id,
        `workspace.boundaries.${boundaryIndex}.boundary_id`,
      ),
      over_rule_id: requiredField(
        boundary.over_rule_id,
        `workspace.boundaries.${boundaryIndex}.over_rule_id`,
      ),
      adapter: requiredValue(
        boundary.adapter,
        `workspace.boundaries.${boundaryIndex}.adapter`,
      ),
      from_domain_id: requiredField(
        boundary.from_domain_id,
        `workspace.boundaries.${boundaryIndex}.from_domain_id`,
      ),
      to_domain_id: requiredField(
        boundary.to_domain_id,
        `workspace.boundaries.${boundaryIndex}.to_domain_id`,
      ),
      export_node_loopbacks: requiredField(
        boundary.export_node_loopbacks,
        `workspace.boundaries.${boundaryIndex}.export_node_loopbacks`,
      ),
    })),
    max_pairs_per_rule: requiredValue(workspace.max_pairs_per_rule, "workspace.max_pairs_per_rule"),
    max_pairs_per_tick: requiredValue(workspace.max_pairs_per_tick, "workspace.max_pairs_per_tick"),
    start_time: requiredField(workspace.start_time, "workspace.start_time"),
    step_seconds: requiredValue(workspace.step_seconds, "workspace.step_seconds"),
    compression: requiredValue(workspace.compression, "workspace.compression"),
  };
}

export function structuredDraftFromWorkspace(
  base: BuilderVisualDraftEnvelope,
  workspace: Workspace,
  draftRevision: number,
): BuilderVisualDraftEnvelope {
  if (base.mode !== "structured") {
    throw new Error("cannot apply a visual workspace to an opaque YAML draft");
  }
  return {
    ...base,
    draft_revision: draftRevision,
    workspace: visualWorkspaceFromWorkspace(workspace),
    session_yaml: null,
  };
}
