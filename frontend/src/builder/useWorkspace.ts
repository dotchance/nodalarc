// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Workspace state + the edit→resolve loop.
 *
 *  Every mutation reserializes the workspace through toSessionDocument and
 *  (debounced) resolve-checks it; the rendered world is always the
 *  resolver's expansion of the current draft — or the resolver's error,
 *  verbatim. No builder-local expansion, ever.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  newDraftConstellation,
  newWorkspace,
  toSessionDocument,
  type DraftConstellation,
  type DraftOrbit,
  type Workspace,
} from "./workspace";

const RESOLVE_DEBOUNCE_MS = 400;

export function useWorkspace(resolveDocument: (document: unknown) => void) {
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const resolveRef = useRef(resolveDocument);
  resolveRef.current = resolveDocument;

  useEffect(() => {
    if (!workspace || workspace.space.length === 0) return;
    const timer = setTimeout(() => {
      resolveRef.current(toSessionDocument(workspace));
    }, RESOLVE_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [workspace]);

  const startNew = useCallback((name: string) => {
    setWorkspace(newWorkspace(name));
  }, []);

  const close = useCallback(() => setWorkspace(null), []);

  const addConstellation = useCallback((nodeRef: string) => {
    setWorkspace((prev) =>
      prev ? { ...prev, space: [...prev.space, newDraftConstellation(nodeRef)] } : prev,
    );
  }, []);

  const removeConstellation = useCallback((segmentId: string) => {
    setWorkspace((prev) =>
      prev
        ? { ...prev, space: prev.space.filter((d) => d.segment_id !== segmentId) }
        : prev,
    );
  }, []);

  const updateConstellation = useCallback(
    (segmentId: string, patch: Partial<DraftConstellation>) => {
      setWorkspace((prev) =>
        prev
          ? {
              ...prev,
              space: prev.space.map((draft) =>
                draft.segment_id === segmentId ? { ...draft, ...patch } : draft,
              ),
            }
          : prev,
      );
    },
    [],
  );

  const updateOrbit = useCallback(
    (segmentId: string, patch: Partial<DraftOrbit>) => {
      setWorkspace((prev) =>
        prev
          ? {
              ...prev,
              space: prev.space.map((draft) =>
                draft.segment_id === segmentId
                  ? { ...draft, orbit: { ...draft.orbit, ...patch } }
                  : draft,
              ),
            }
          : prev,
      );
    },
    [],
  );

  const setGroundSiteSet = useCallback((ref: string | null) => {
    setWorkspace((prev) => (prev ? { ...prev, ground_site_set_ref: ref } : prev));
  }, []);

  return {
    workspace,
    startNew,
    close,
    addConstellation,
    removeConstellation,
    updateConstellation,
    updateOrbit,
    setGroundSiteSet,
  };
}
