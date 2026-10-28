// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** The link rules, routing domains and boundaries the page lists.
 *
 *  The backend lists every one the accepted session declares, each with the
 *  card value that writes it back unchanged, or none. A pending edit is laid
 *  over the object it edits: the object then shows what the edit's latest
 *  answer states, and an edit of an object the accepted session does not hold
 *  adds that object. Every fact comes from the listing or an answer; the page
 *  reads none of them from grammar JSON.
 */

import type {
  BuilderVisualLinkRule,
  BuilderVisualLinkRuleListing,
  BuilderVisualObjectEditResult,
  BuilderVisualRoutingBoundary,
  BuilderVisualRoutingBoundaryListing,
  BuilderVisualRoutingDomain,
  BuilderVisualRoutingDomainListing,
  BuilderVisualSessionListing,
  RoutingProtocol,
} from "./generated/builderApi";

/** One link rule as the page lists it. */
export interface ListedLinkRule {
  readonly id: string;
  /** The accepted session's rule; null for a rule only a pending edit holds. */
  readonly accepted: BuilderVisualLinkRuleListing | null;
  /** The latest answer for the rule's pending edit; null when none is pending. */
  readonly pending: BuilderVisualObjectEditResult | null;
  /** The card value of the rule as it now stands: its pending edit's while one
   *  is pending, otherwise the accepted rule's. Null when no card states it. */
  readonly card: BuilderVisualLinkRule | null;
  /** The segments the accepted rule names, then those its pending card value
   *  names. */
  readonly segmentIds: ReadonlyArray<string>;
}

/** One routing domain as the page lists it. */
export interface ListedRoutingDomain {
  readonly id: string;
  /** The accepted session's domain; null for a domain only a pending edit holds. */
  readonly accepted: BuilderVisualRoutingDomainListing | null;
  /** The latest answer for the domain's pending edit; null when none is pending. */
  readonly pending: BuilderVisualObjectEditResult | null;
  /** The card value of the domain as it now stands. Null when no card states it. */
  readonly card: BuilderVisualRoutingDomain | null;
  /** The domain's protocol as it now stands; null while its pending edit
   *  states none. */
  readonly protocol: RoutingProtocol | null;
}

/** One routing boundary as the page lists it, at its position in the list. */
export interface ListedRoutingBoundary {
  /** The accepted boundary; null while the boundary list has a pending edit. */
  readonly accepted: BuilderVisualRoutingBoundaryListing | null;
  /** The card value of the boundary as the list now stands. Null when no card
   *  states it. */
  readonly card: BuilderVisualRoutingBoundary | null;
  /** The rule the boundary exchanges over; null while the list's pending
   *  edit states none for it. */
  readonly overRuleId: string | null;
}

/** The routing boundary list as the page lists it. The list is edited as a
 *  whole, so a pending edit replaces every boundary at once. */
export interface ListedRoutingBoundaries {
  /** The latest answer for the list's pending edit; null when none is pending. */
  readonly pending: BuilderVisualObjectEditResult | null;
  /** Each boundary as the list now stands. Null while the list's pending edit
   *  is in a form no card states, so the page cannot tell its boundaries apart. */
  readonly items: ReadonlyArray<ListedRoutingBoundary> | null;
}

export interface ListedSessionObjects {
  readonly linkRules: ReadonlyArray<ListedLinkRule>;
  readonly routingDomains: ReadonlyArray<ListedRoutingDomain>;
  readonly boundaries: ListedRoutingBoundaries;
}

const LINK_RULES = "/link_rules";
const ROUTING_DOMAINS = "/routing/domains";
const ROUTING_BOUNDARIES = "/routing/boundaries";

/** The key a pending answer is found under: its list and member id, or the
 *  pointer of a list edited as a whole. */
function answerKey(answer: BuilderVisualObjectEditResult): string {
  const address = answer.address;
  return address.kind === "member" ? `${address.list_pointer}/${address.id}` : address.pointer;
}

function linkRuleCard(answer: BuilderVisualObjectEditResult): BuilderVisualLinkRule | null {
  const widget = answer.widget;
  if (widget === null) return null;
  if (widget.kind !== "link_rule") {
    throw new Error(`the pending edit of a link rule answered a ${widget.kind} card value`);
  }
  return widget.rule;
}

function routingDomainCard(
  answer: BuilderVisualObjectEditResult,
): BuilderVisualRoutingDomain | null {
  const widget = answer.widget;
  if (widget === null) return null;
  if (widget.kind !== "routing_domain") {
    throw new Error(`the pending edit of a routing domain answered a ${widget.kind} card value`);
  }
  return widget.domain;
}

function boundaryCards(
  answer: BuilderVisualObjectEditResult,
): ReadonlyArray<BuilderVisualRoutingBoundary | null> | null {
  const widget = answer.widget;
  if (widget === null) return null;
  if (widget.kind !== "routing_boundaries") {
    throw new Error(`the pending edit of the boundary list answered a ${widget.kind} card value`);
  }
  return widget.boundaries;
}

function listedLinkRule(
  id: string,
  accepted: BuilderVisualLinkRuleListing | null,
  pending: BuilderVisualObjectEditResult | null,
): ListedLinkRule {
  const card = pending === null ? (accepted?.widget ?? null) : linkRuleCard(pending);
  const pendingSegments =
    pending === null || card === null
      ? []
      : [card.a?.segment_id, card.b?.segment_id].filter(
          (segmentId): segmentId is string => segmentId !== undefined && segmentId !== "",
        );
  return {
    id,
    accepted,
    pending,
    card,
    segmentIds: [...new Set([...(accepted?.segment_ids ?? []), ...pendingSegments])],
  };
}

function listedRoutingDomain(
  id: string,
  accepted: BuilderVisualRoutingDomainListing | null,
  pending: BuilderVisualObjectEditResult | null,
): ListedRoutingDomain {
  if (pending === null) {
    if (accepted === null) throw new Error(`routing domain ${id} is neither accepted nor pending`);
    return { id, accepted, pending, card: accepted.widget, protocol: accepted.protocol };
  }
  const card = routingDomainCard(pending);
  return { id, accepted, pending, card, protocol: card?.protocol ?? null };
}

function listedBoundaries(
  accepted: ReadonlyArray<BuilderVisualRoutingBoundaryListing>,
  pending: BuilderVisualObjectEditResult | null,
): ListedRoutingBoundaries {
  if (pending === null) {
    return {
      pending,
      items: accepted.map((boundary) => ({
        accepted: boundary,
        card: boundary.widget,
        overRuleId: boundary.over_rule_id,
      })),
    };
  }
  const cards = boundaryCards(pending);
  return {
    pending,
    items:
      cards === null
        ? null
        : cards.map((card) => ({
            accepted: null,
            card,
            overRuleId: card?.over_rule_id ?? null,
          })),
  };
}

/** The accepted session's rules, domains and boundaries with the pending
 *  edits' latest answers laid over them. Each listed object keeps the
 *  accepted order; an object only a pending edit holds follows the accepted
 *  ones, in the order the edits were made. A draft with no accepted session
 *  lists none: rules, domains and boundaries are edited on an accepted
 *  session, so no edit of one can be pending. */
export function listSessionObjects(
  listing: BuilderVisualSessionListing | null,
  pendingAnswers: Iterable<BuilderVisualObjectEditResult>,
): ListedSessionObjects {
  const pending = new Map<string, BuilderVisualObjectEditResult>();
  for (const answer of pendingAnswers) {
    const key = answerKey(answer);
    if (pending.has(key)) throw new Error(`two pending edits address ${key}`);
    pending.set(key, answer);
  }
  if (listing === null) {
    if (pending.size > 0) {
      throw new Error("a pending edit addresses a draft that holds no accepted session");
    }
    return { linkRules: [], routingDomains: [], boundaries: { pending: null, items: [] } };
  }
  const take = (key: string): BuilderVisualObjectEditResult | null => {
    const answer = pending.get(key) ?? null;
    pending.delete(key);
    return answer;
  };
  const linkRules = listing.link_rules.map((rule) =>
    listedLinkRule(rule.rule_id, rule, take(`${LINK_RULES}/${rule.rule_id}`)),
  );
  const routingDomains = listing.routing_domains.map((domain) =>
    listedRoutingDomain(domain.domain_id, domain, take(`${ROUTING_DOMAINS}/${domain.domain_id}`)),
  );
  const boundaries = listedBoundaries(listing.boundaries, take(ROUTING_BOUNDARIES));
  for (const answer of pending.values()) {
    const address = answer.address;
    if (address.kind !== "member") {
      throw new Error(`a pending edit addresses an unknown list ${address.pointer}`);
    }
    if (address.list_pointer === LINK_RULES) {
      linkRules.push(listedLinkRule(address.id, null, answer));
    } else {
      routingDomains.push(listedRoutingDomain(address.id, null, answer));
    }
  }
  return { linkRules, routingDomains, boundaries };
}

/** Whether the object's editor window can show it. That window edits the
 *  accepted object through its card, so it needs the accepted card value and
 *  no pending edit laid over it. */
export function cardWindowShows(listed: {
  readonly accepted: { readonly widget: unknown } | null;
  readonly pending: BuilderVisualObjectEditResult | null;
}): boolean {
  return listed.pending === null && listed.accepted !== null && listed.accepted.widget !== null;
}

/** Why a row's object opens no window: an edit of it is pending, or no card
 *  writes it back unchanged. */
export function noWindowReason(kind: string, pending: boolean): string {
  return pending
    ? `An edit of this ${kind} is pending.`
    : `No card writes this ${kind} back unchanged. Edit it in the session YAML.`;
}
