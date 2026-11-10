// Copyright 2024-2026 .chance (dotchance)
// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
/** A document the session proposes, open in the component window.
 *
 *  The backend opens the document with its pending edit applied. The window's
 *  gestures read the session's proposals, and OK holds the document as the
 *  session's pending edit; nothing is saved to the library.
 */
import { useEffect, useState } from "react";
import { CatalogDraftEditorWindow, type ProposalSession } from "./CatalogDraftEditorWindow";
import { openCatalogDraftProposal } from "./builderApiClient";
import type {
  BuilderVisualAuthoringFacts,
  CatalogComponentDraftEnvelope,
} from "./generated/builderApi";

export function ProposalWindow({
  proposalRef,
  session,
  authoring,
  onClose,
}: {
  proposalRef: string;
  session: ProposalSession;
  authoring: BuilderVisualAuthoringFacts;
  onClose: () => void;
}) {
  const [opened, setOpened] = useState<CatalogComponentDraftEnvelope | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const { context } = session;
  useEffect(() => {
    let current = true;
    setOpened(null);
    setFailure(null);
    openCatalogDraftProposal({ session: context(), ref: proposalRef }).then(
      (envelope) => {
        if (current) setOpened(envelope);
      },
      (cause: unknown) => {
        if (current) setFailure(cause instanceof Error ? cause.message : String(cause));
      },
    );
    return () => {
      current = false;
    };
    // Opened once per document; later gestures read the session as it stands.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [proposalRef]);
  if (failure !== null) {
    return <div className="builder-warning">{proposalRef} did not open: {failure}</div>;
  }
  if (opened === null) {
    return <div className="builder-site-derived">opening {proposalRef}…</div>;
  }
  return (
    <CatalogDraftEditorWindow
      key={proposalRef}
      initialDraft={opened}
      authoring={authoring}
      session={session}
      onSaved={() => {
        throw new Error("a proposed document is held as a pending edit, never saved alone");
      }}
      onClose={onClose}
    />
  );
}
