/** Graphical projection selection from the backend-issued visual draft. */

import { describe, expect, it } from "vitest";
import type { BuilderVisualDraftEnvelope } from "../generated/builderApi";
import { workspaceFromVisualDraft } from "../visualWorkspace";
import { newWorkspace } from "./fixtures/workspaceFixtures";

function envelope(): BuilderVisualDraftEnvelope {
  const applied = newWorkspace("projection");
  applied.projection_revision = 4;
  const authoring = structuredClone(applied);
  authoring.description = "authoring projection";
  return {
    contract_version: 2,
    draft_revision: 4,
    projection_status: "applied",
    target_ref: "user:sessions/projection.yaml",
    source_ref: null,
    expected_session_revision: null,
    catalog_documents: [],
    session_name_is_placeholder: false,
    reserved_authoring_ids: [],
    session_yaml: "session:\n  name: projection\n",
    authoring_workspace: authoring,
    applied_workspace: applied,
    applied_revision: 4,
    applied_session: { session: { name: "projection" } },
  };
}

describe("workspaceFromVisualDraft", () => {
  it("uses the backend authoring projection without minting a revision", () => {
    const draft = envelope();
    const workspace = workspaceFromVisualDraft(draft);
    expect(workspace.description).toBe("authoring projection");
    workspace.description = "local edit";
    expect(draft.authoring_workspace?.description).toBe("authoring projection");
    expect(draft.draft_revision).toBe(4);
  });

  it("falls back to the applied projection", () => {
    const draft = { ...envelope(), authoring_workspace: null };
    expect(workspaceFromVisualDraft(draft).session_name).toBe("projection");
  });

  it("refuses a never-valid draft instead of inventing an empty world", () => {
    const draft: BuilderVisualDraftEnvelope = {
      ...envelope(),
      projection_status: "no_valid_projection",
      authoring_workspace: null,
      applied_workspace: null,
      applied_revision: null,
      applied_session: null,
    };
    expect(() => workspaceFromVisualDraft(draft)).toThrow(
      "visual draft has no graphical projection",
    );
  });
});
