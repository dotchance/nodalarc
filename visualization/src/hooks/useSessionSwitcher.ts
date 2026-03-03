/** Hook for listing available sessions and triggering session switches. */

import { useState, useEffect, useCallback, useRef } from "react";
import { REST_URL } from "../config";
import type { SessionInfo } from "../types";

export function useSessionSwitcher(sessionStatus: string | null) {
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [switching, setSwitching] = useState(false);
  // Must see "switching" status from backend before accepting "ready" as done
  const sawSwitchingRef = useRef(false);

  const fetchSessions = useCallback(() => {
    fetch(`${REST_URL}/api/v1/sessions`)
      .then((r) => r.json())
      .then((data: SessionInfo[]) => setSessions(data))
      .catch(() => {});
  }, []);

  // Fetch session list on mount
  useEffect(() => {
    fetchSessions();
  }, [fetchSessions]);

  // Track when backend confirms it's switching
  useEffect(() => {
    if (switching && sessionStatus === "switching") {
      sawSwitchingRef.current = true;
    }
  }, [switching, sessionStatus]);

  // Only clear overlay once we've seen "switching" then "ready" (or "error")
  useEffect(() => {
    if (switching && sawSwitchingRef.current && (sessionStatus === "ready" || sessionStatus === "error")) {
      setSwitching(false);
      sawSwitchingRef.current = false;
      fetchSessions();
    }
  }, [switching, sessionStatus, fetchSessions]);

  const switchSession = useCallback(
    async (file: string) => {
      if (switching) return;
      sawSwitchingRef.current = false;
      setSwitching(true);
      try {
        const resp = await fetch(`${REST_URL}/api/v1/sessions/switch`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
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
