// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** The resolver wall's owning editor target.
 *
 *  A resolve refusal names a subject — a link rule, a routing domain, or a
 *  segment. This maps that subject back to the editor window that owns it, so
 *  the wall opens the right editor. A rule or domain is found by its grammar id
 *  among the objects the backend lists, with the pending edits laid over them.
 *  Every listed rule has a window. A segment is found in the PREVIEW workspace
 *  (applied + dirty overlays). It
 *  matches by the stable backend-issued subject id — never by parsing labels,
 *  prose, or runtime ids. A subject with no window to show it returns null, and
 *  the caller shows the session-level wall instead: a refusal is never dropped.
 */
import type { BuilderResolveError } from "./builderTypes";
import { cardWindowShows, type ListedSessionObjects } from "./sessionListing";
import { targetKey, type EditorTarget } from "./useEditorWindows";
import type { Workspace } from "./workspace";

export function wallTarget(
  preview: Workspace | null,
  listed: ListedSessionObjects,
  resolveError: BuilderResolveError | null,
): { target: EditorTarget; key: string } | null {
  if (!preview || !resolveError) return null;
  const subject = resolveError.subject;
  if (subject?.kind === "link_rule") {
    const rule = listed.linkRules.find((r) => r.id === subject.id);
    if (rule) {
      const target: EditorTarget = { kind: "link", id: rule.id };
      return { target, key: targetKey(target) };
    }
  }
  if (subject?.kind === "routing_domain") {
    const domain = listed.routingDomains.find((d) => d.id === subject.id);
    if (domain && cardWindowShows(domain)) {
      const target: EditorTarget = { kind: "domain", id: domain.id };
      return { target, key: targetKey(target) };
    }
  }
  const segmentId = resolveError.segment_id;
  if (segmentId) {
    if (preview.space.some((d) => d.segment_id === segmentId)) {
      const target: EditorTarget = { kind: "segment", id: segmentId };
      return { target, key: targetKey(target) };
    }
    if (preview.ground.some((d) => d.segment_id === segmentId)) {
      const target: EditorTarget = { kind: "ground", id: segmentId };
      return { target, key: targetKey(target) };
    }
  }
  return null;
}
