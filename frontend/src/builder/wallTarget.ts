// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** The resolver wall's owning editor target.
 *
 *  A resolve refusal names a subject — a link rule, a routing domain, or a
 *  segment. This maps that subject back to the editor window that owns it, so
 *  the wall opens the right editor. It reads the PREVIEW workspace (applied +
 *  dirty overlays) and matches by the stable backend-issued subject id — never
 *  by parsing labels, prose, or runtime ids. A subject with no matching draft returns null, and the caller
 *  shows the session-level wall instead: a refusal is never dropped.
 */
import type { BuilderResolveError } from "./builderTypes";
import { emittedDomainId, emittedRuleId, type Workspace } from "./workspace";
import { targetKey, type EditorTarget } from "./useEditorWindows";

export function wallTarget(
  preview: Workspace | null,
  resolveError: BuilderResolveError | null,
): { target: EditorTarget; key: string } | null {
  if (!preview || !resolveError) return null;
  const subject = resolveError.subject;
  if (subject?.kind === "link_rule") {
    const rule = preview.links.find((r) => emittedRuleId(r) === subject.id);
    if (rule) {
      const target: EditorTarget = { kind: "link", id: rule.rule_id };
      return { target, key: targetKey(target) };
    }
  }
  if (subject?.kind === "routing_domain") {
    const domain = preview.routing_domains.find((d) => emittedDomainId(d) === subject.id);
    if (domain) {
      const target: EditorTarget = { kind: "domain", id: domain.domain_id };
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
