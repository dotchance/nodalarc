// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Hook: bounded observed decision window for the selected ground station. */

import { useEffect, useState } from "react";
import { fetchDecisionTimeline } from "./client";
import type { GsDecisionTimelineFacts } from "./types";

const REFRESH_MS = 2000;

export interface DecisionTimelineState {
  timeline: GsDecisionTimelineFacts | null;
  loading: boolean;
  error: string | null;
}

export function useDecisionTimeline(gsId: string | null): DecisionTimelineState {
  const [state, setState] = useState<DecisionTimelineState>({
    timeline: null,
    loading: false,
    error: null,
  });

  useEffect(() => {
    if (!gsId) {
      setState({ timeline: null, loading: false, error: null });
      return;
    }
    let alive = true;
    const controller = new AbortController();

    const load = async () => {
      try {
        const timeline = await fetchDecisionTimeline(gsId, controller.signal);
        if (alive) setState({ timeline, loading: false, error: null });
      } catch (err) {
        if (alive && !controller.signal.aborted) {
          setState({ timeline: null, loading: false, error: String(err) });
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
  }, [gsId]);

  return state;
}
