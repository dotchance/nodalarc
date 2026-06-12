// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Hook for listing available sessions and triggering session switches.
 *
 * The websocket lifecycle messages (session_transitioning / session_ready /
 * session_failed) are the single owner of "a switch is in flight" — the
 * snapshot is nulled for the whole transition window, so any state derived
 * from snapshot fields during a switch reads absence, not progress. This
 * hook's `switching` covers only the request window (POST accepted until the
 * websocket lifecycle takes over) and then follows that lifecycle down.
 */

import { useState, useEffect, useCallback, useRef } from "react";
import { REST_URL, authHeaders } from "../config";
import type { SessionInfo } from "../types";

export function useSessionSwitcher(sessionTransitioning: boolean) {
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [switching, setSwitching] = useState(false);
  // The websocket lifecycle must confirm the switch before its end clears it.
  const sawTransitionRef = useRef(false);

  const fetchSessions = useCallback(() => {
    fetch(`${REST_URL}/api/v1/sessions`, { headers: authHeaders() })
      .then((r) => r.json())
      .then((data: SessionInfo[]) => setSessions(data))
      .catch(() => {});
  }, []);

  // Fetch session list on mount
  useEffect(() => {
    fetchSessions();
  }, [fetchSessions]);

  const prevTransitioningRef = useRef(false);
  useEffect(() => {
    if (switching && sessionTransitioning) {
      sawTransitionRef.current = true;
    }
    // Lifecycle ended (session_ready or session_failed): the switch is over.
    if (switching && sawTransitionRef.current && !sessionTransitioning) {
      setSwitching(false);
      sawTransitionRef.current = false;
    }
    // ANY switch end changes which session is active — refresh the list for
    // backend-initiated switches too (deploys, uploads, other operators).
    if (prevTransitioningRef.current && !sessionTransitioning) {
      fetchSessions();
    }
    prevTransitioningRef.current = sessionTransitioning;
  }, [switching, sessionTransitioning, fetchSessions]);

  const switchSession = useCallback(
    async (file: string) => {
      if (switching) return;
      sawTransitionRef.current = false;
      setSwitching(true);
      try {
        const resp = await fetch(`${REST_URL}/api/v1/sessions/switch`, {
          method: "POST",
          headers: authHeaders({ "Content-Type": "application/json" }),
          body: JSON.stringify({ session: file }),
        });
        if (!resp.ok) {
          setSwitching(false);
        }
      } catch {
        setSwitching(false);
      }
    },
    [switching],
  );

  return { sessions, switching, switchSession };
}
