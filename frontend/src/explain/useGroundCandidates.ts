// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * Hook: live `ground-link-decisions` (the candidate set) for one node, sliced server-side to that
 * node, refreshing on the sim clock. The single fetch the globe's on-select sat tinting consumes
 * (via gsCandidateRelations); the node panels share the same client.fetchGroundDecisions source.
 */

import { useEffect, useState } from "react";
import { fetchGroundDecisions, type GroundDecisionsSnapshot } from "./client";

const REFRESH_MS = 2000;

export interface GroundCandidatesState {
  data: GroundDecisionsSnapshot | null;
  loading: boolean;
  error: string | null;
}

export function useGroundCandidates(
  nodeId: string | null,
  refreshKey?: string | number,
): GroundCandidatesState {
  const [state, setState] = useState<GroundCandidatesState>({
    data: null,
    loading: false,
    error: null,
  });

  useEffect(() => {
    if (!nodeId) {
      setState({ data: null, loading: false, error: null });
      return;
    }
    let alive = true;
    const controller = new AbortController();

    const load = async () => {
      try {
        const data = await fetchGroundDecisions(nodeId, controller.signal);
        if (alive) setState({ data, loading: false, error: null });
      } catch (err) {
        if (alive && !controller.signal.aborted) {
          setState({ data: null, loading: false, error: String(err) });
        }
      }
    };

    setState((s) => ({ ...s, loading: true }));
    void load();
    const timer = window.setInterval(load, REFRESH_MS);
    return () => {
      alive = false;
      controller.abort();
      window.clearInterval(timer);
    };
  }, [nodeId, refreshKey]);

  return state;
}
