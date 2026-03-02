/** Hook for listing available sessions and triggering session switches. */

import { useState, useEffect, useCallback } from "react";
import { REST_URL } from "../config";
import type { SessionInfo } from "../types";

export function useSessionSwitcher(sessionStatus: string | null) {
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [switching, setSwitching] = useState(false);

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

  // When session_status transitions back to "ready" after switching, clear switching flag and refresh list
  useEffect(() => {
    if (switching && sessionStatus === "ready") {
      setSwitching(false);
      fetchSessions();
    }
  }, [switching, sessionStatus, fetchSessions]);

  const switchSession = useCallback(
    async (file: string) => {
      setSwitching(true);
      try {
        await fetch(`${REST_URL}/api/v1/sessions/switch`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ session: file }),
        });
      } catch {
        setSwitching(false);
      }
    },
    [],
  );

  return { sessions, switching, switchSession };
}
