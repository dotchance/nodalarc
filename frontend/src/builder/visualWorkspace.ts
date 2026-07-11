/** Identity helpers for the backend-owned structured visual draft. */

import type { BuilderVisualDraftEnvelope } from "./generated/builderApi";
import type { Workspace } from "./workspace";

export function workspaceFromVisualDraft(draft: BuilderVisualDraftEnvelope): Workspace {
  if (draft.mode !== "structured" || !draft.workspace) {
    throw new Error("visual draft is not a structured workspace");
  }
  return structuredClone(draft.workspace) as Workspace;
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
    workspace,
    session_yaml: null,
  };
}
