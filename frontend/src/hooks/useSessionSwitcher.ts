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
import type { CatalogSessionSwitchRequest } from "../builder/generated/builderApi";
import { apiErrorFromException, apiErrorMessage } from "../ui/apiError";

/** How a switch request ended: accepted by VS-API, or refused with its reason. */
export type SessionSwitchResult = { ok: true } | { ok: false; message: string };

export function useSessionSwitcher(sessionTransitioning: boolean) {
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  // Why the session list did not load; null once it loaded.
  const [sessionsError, setSessionsError] = useState<string | null>(null);
  const [switching, setSwitching] = useState(false);
  // The websocket lifecycle must confirm the switch before its end clears it.
  const sawTransitionRef = useRef(false);

  const fetchSessions = useCallback(async () => {
    try {
      const response = await fetch(`${REST_URL}/api/v1/sessions`, { headers: authHeaders() });
      if (!response.ok) {
        setSessionsError(await apiErrorMessage(response));
        return;
      }
      setSessions((await response.json()) as SessionInfo[]);
      setSessionsError(null);
    } catch (err) {
      setSessionsError(apiErrorFromException(err));
    }
  }, []);

  // Fetch session list on mount
  useEffect(() => {
    void fetchSessions();
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
      void fetchSessions();
    }
    prevTransitioningRef.current = sessionTransitioning;
  }, [switching, sessionTransitioning, fetchSessions]);

  const switchSession = useCallback(
    async (session: SessionInfo, recordHistory: boolean): Promise<SessionSwitchResult> => {
      if (switching) return { ok: false, message: "A session switch is already in progress" };
      if (
        !session.deploy_allowed
        || !session.source_revision
        || !session.document_digest
        || !session.dependency_digest
      ) {
        return { ok: false, message: `${session.source_id.session_ref} cannot be deployed` };
      }
      sawTransitionRef.current = false;
      setSwitching(true);
      try {
        const request: CatalogSessionSwitchRequest = {
          source: session.source_id,
          expected_source_revision: session.source_revision,
          expected_document_digest: session.document_digest,
          expected_dependency_digest: session.dependency_digest,
          record_history: recordHistory,
        };
        const resp = await fetch(`${REST_URL}/api/v1/sessions/switch`, {
          method: "POST",
          headers: authHeaders({ "Content-Type": "application/json" }),
          body: JSON.stringify(request),
        });
        if (!resp.ok) {
          setSwitching(false);
          return { ok: false, message: await apiErrorMessage(resp) };
        }
        return { ok: true };
      } catch (err) {
        setSwitching(false);
        return { ok: false, message: apiErrorFromException(err) };
      }
    },
    [switching],
  );

  return { sessions, sessionsError, switching, switchSession };
}
