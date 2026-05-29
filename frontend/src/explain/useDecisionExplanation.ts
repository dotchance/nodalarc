// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Hook: live decision-explanation facts for the selected ground station. */

import { useEffect, useRef, useState } from "react";
import { fetchDecisionExplanation } from "./client";
import type { DecisionFacts } from "./types";

const REFRESH_MS = 2000;

export interface DecisionExplanationState {
  facts: DecisionFacts | null;
  loading: boolean;
  error: string | null;
}

/**
 * Fetch decision-explanation facts for `gsId`, refreshing on an interval so the
 * card tracks the live sim. `refreshKey` (e.g. snapshot sim_time) forces an
 * immediate refetch when the world advances. Passing null clears the state.
 */
export function useDecisionExplanation(
  gsId: string | null,
  refreshKey?: string | number,
): DecisionExplanationState {
  const [state, setState] = useState<DecisionExplanationState>({
    facts: null,
    loading: false,
    error: null,
  });
  const seq = useRef(0);

  useEffect(() => {
    if (!gsId) {
      setState({ facts: null, loading: false, error: null });
      return;
    }
    let alive = true;
    const myGs = gsId;
    const controller = new AbortController();
    seq.current += 1;

    const load = async () => {
      try {
        const facts = await fetchDecisionExplanation(myGs, controller.signal);
        if (alive) setState({ facts, loading: false, error: null });
      } catch (err) {
        if (alive && !controller.signal.aborted) {
          setState({ facts: null, loading: false, error: String(err) });
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
  }, [gsId, refreshKey]);

  return state;
}
