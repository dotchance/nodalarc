// Copyright 2024-2026 .chance (dotchance)
// SPDX-License-Identifier: AGPL-3.0-only
/** The outline's Links and Routing groups: every link rule, routing domain
 *  and boundary the session declares, as the backend lists them with the
 *  pending edits laid over them. Every link rule and routing domain opens its
 *  window, and every boundary opens the window of the boundary list. When the
 *  session declares no routing domain, the Routing group shows the resolver's
 *  default domains, read-only.
 */

import { Icon, type IconName } from "../ui/icons/Icon";
import { resolvedDomains, type GuideWorldNode } from "./BuildGuide";
import type { ListedSessionObjects } from "./sessionListing";
import { targetKey, type EditorTarget } from "./useEditorWindows";

interface LinkAndRoutingOutlineProps {
  listed: ListedSessionObjects;
  /** Whether the session places a segment. The group headings show from the
   *  first segment on, before the first rule or domain. */
  hasSegments: boolean;
  /** The resolved world's nodes; null while the session does not resolve. */
  resolvedNodes: ReadonlyArray<GuideWorldNode> | null;
  /** The refusal the last compile answered, if any. */
  refusal: string | null;
  isOpen: (key: string) => boolean;
  onOpen: (target: EditorTarget) => void;
}

/** The boundary list is edited in one window. */
const BOUNDARIES: EditorTarget = { kind: "boundaries" };

/** One outline row. It opens its object's window. */
function OutlineRow({
  icon,
  name,
  detail,
  target,
  why,
  isOpen,
  onOpen,
}: {
  icon: IconName;
  name: string;
  detail: string;
  target: EditorTarget;
  why: string;
  isOpen: (key: string) => boolean;
  onOpen: (target: EditorTarget) => void;
}) {
  return (
    <button
      className={`builder-outline-row builder-outline-row--segment${
        isOpen(targetKey(target)) ? " builder-outline-row--selected" : ""
      }`}
      onClick={() => onOpen(target)}
      title={why}
    >
      <span className="builder-outline-name">
        <Icon name={icon} size={12} />
        {name}
      </span>
      <span className="builder-outline-count">{detail}</span>
    </button>
  );
}

function details(...parts: ReadonlyArray<string | null | undefined>): string {
  return parts.filter((part): part is string => !!part).join(" · ");
}

/** The resolver's default routing for a session that declares no domain. */
function ResolvedRouting({
  nodes,
  refusal,
}: {
  nodes: ReadonlyArray<GuideWorldNode> | null;
  refusal: string | null;
}) {
  if (nodes === null) {
    return (
      <div className="builder-zone-empty">
        {refusal === null
          ? "Routing omitted. Not checked yet."
          : `Routing omitted. Not resolved: ${refusal}`}
      </div>
    );
  }
  const domains = resolvedDomains(nodes);
  if (domains.length === 0) {
    return (
      <div className="builder-zone-empty">
        Routing omitted, and no node runs a routing workload.
      </div>
    );
  }
  return (
    <>
      <div className="builder-zone-empty">Routing omitted. The resolver's default domains:</div>
      {domains.map((domain) => (
        <div
          className="builder-library-entry"
          key={domain.domainId}
          title="The session declares no routing domain, so the resolver's default applies."
        >
          <span className="builder-outline-name builder-outline-row--segment">
            <Icon name="network" size={12} />
            {domain.domainId}
          </span>
          <span className="builder-outline-count">
            {`${domain.protocol} · ${domain.participants} node${
              domain.participants === 1 ? "" : "s"
            }`}
          </span>
        </div>
      ))}
    </>
  );
}

export function LinkAndRoutingOutline({
  listed,
  hasSegments,
  resolvedNodes,
  refusal,
  isOpen,
  onOpen,
}: LinkAndRoutingOutlineProps) {
  const { linkRules, routingDomains, boundaries } = listed;
  const boundaryItems = boundaries.items;
  return (
    <>
      {(hasSegments || linkRules.length > 0) && (
        <div className="builder-outline-kind">Links</div>
      )}
      {linkRules.map((rule) => (
        <OutlineRow
          key={rule.id}
          icon="spline"
          name={rule.id}
          detail={details(
            rule.card?.a?.role,
            rule.card?.enabled === false ? "off" : null,
            rule.pending === null ? null : "pending edit",
          )}
          target={{ kind: "link", id: rule.id }}
          why={`Edit ${rule.id}`}
          isOpen={isOpen}
          onOpen={onOpen}
        />
      ))}
      {(hasSegments || routingDomains.length > 0) && (
        <div className="builder-outline-kind">Routing</div>
      )}
      {routingDomains.map((domain) => (
        <OutlineRow
          key={domain.id}
          icon="network"
          name={domain.id}
          detail={details(domain.protocol, domain.pending === null ? null : "pending edit")}
          target={{ kind: "domain", id: domain.id }}
          why={`Edit ${domain.id}`}
          isOpen={isOpen}
          onOpen={onOpen}
        />
      ))}
      {hasSegments && routingDomains.length === 0 && (
        <ResolvedRouting nodes={resolvedNodes} refusal={refusal} />
      )}
      {boundaryItems === null ? (
        <OutlineRow
          icon="columns-2"
          name="boundaries"
          detail="pending edit"
          target={BOUNDARIES}
          why="Edit the boundaries"
          isOpen={isOpen}
          onOpen={onOpen}
        />
      ) : (
        boundaryItems.map((boundary, index) => (
          <OutlineRow
            key={index}
            icon="columns-2"
            name={boundary.overRuleId === null ? "boundary" : `over ${boundary.overRuleId}`}
            detail={details(
              boundary.card?.adapter,
              boundaries.pending === null ? null : "pending edit",
            )}
            target={BOUNDARIES}
            why="Edit the boundaries"
            isOpen={isOpen}
            onOpen={onOpen}
          />
        ))
      )}
    </>
  );
}
