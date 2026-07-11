/** Identity helpers for the backend-owned structured visual draft. */

import type { BuilderVisualDraftEnvelope } from "./generated/builderApi";
import type { Workspace } from "./workspace";

export function workspaceFromVisualDraft(draft: BuilderVisualDraftEnvelope): Workspace {
  const workspace = draft.authoring_workspace ?? draft.applied_workspace;
  if (!workspace) throw new Error("visual draft has no graphical projection");
  return structuredClone(workspace) as Workspace;
}
