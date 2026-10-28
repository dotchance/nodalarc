// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** The outline's Links and Routing groups: every link rule, routing domain
 *  and boundary the session declares, as the backend lists them with the
 *  pending edits laid over them. Every link rule opens its window. When the
 *  session declares no routing domain, the Routing group shows the resolver's
 *  default domains, read-only.
 */

import { Icon, type IconName } from "../ui/icons/Icon";
import { resolvedDomains, type GuideWorldNode } from "./BuildGuide";
import { cardWindowShows, noWindowReason, type ListedSessionObjects } from "./sessionListing";
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

/** One outline row. A row whose object has no window to open says why. */
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
  target: EditorTarget | null;
  why: string;
  isOpen: (key: string) => boolean;
  onOpen: (target: EditorTarget) => void;
}) {
  if (target === null) {
    return (
      <div className="builder-library-entry" title={why}>
        <span className="builder-outline-name builder-outline-row--segment">
          <Icon name={icon} size={12} />
          {name}
        </span>
        <span className="builder-outline-count">{detail}</span>
      </div>
    );
  }
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
          target={cardWindowShows(domain) ? { kind: "domain", id: domain.id } : null}
          why={
            cardWindowShows(domain)
              ? `Edit ${domain.id}`
              : noWindowReason("routing domain", domain.pending !== null)
          }
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
          target={null}
          why={noWindowReason("boundary list", true)}
          isOpen={isOpen}
          onOpen={onOpen}
        />
      ) : (
        boundaryItems.map((boundary, index) => {
          const windowId =
            cardWindowShows({ accepted: boundary.accepted, pending: boundaries.pending })
              ? (boundary.accepted?.widget?.boundary_id ?? null)
              : null;
          return (
            <OutlineRow
              key={index}
              icon="columns-2"
              name={boundary.overRuleId === null ? "boundary" : `over ${boundary.overRuleId}`}
              detail={details(
                boundary.card?.adapter,
                boundaries.pending === null ? null : "pending edit",
              )}
              target={windowId === null ? null : { kind: "boundary", id: windowId }}
              why={
                windowId === null
                  ? noWindowReason("boundary", boundaries.pending !== null)
                  : "Edit boundary"
              }
              isOpen={isOpen}
              onOpen={onOpen}
            />
          );
        })
      )}
    </>
  );
}
