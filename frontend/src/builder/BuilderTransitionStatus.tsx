/** Typed deployment-transition polling and runtime proof presentation. */

import { useEffect, useState } from "react";
import { getSessionTransition } from "./builderApiClient";
import type {
  BuilderDigests,
  TransitionOperation,
  TransitionOperationState,
} from "./generated/builderApi";

const TERMINAL_STATES = new Set<TransitionOperationState>([
  "succeeded",
  "failed",
  "cancelled",
]);

export function transitionIsTerminal(state: TransitionOperationState): boolean {
  return TERMINAL_STATES.has(state);
}

export function useBuilderTransitionOperation(
  operationId: string | null,
  pollIntervalMs = 750,
): { operation: TransitionOperation | null; error: string | null } {
  const [snapshot, setSnapshot] = useState<{
    operationId: string;
    operation: TransitionOperation;
  } | null>(null);
  const [pollFailure, setPollFailure] = useState<{
    operationId: string;
    message: string;
  } | null>(null);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    setSnapshot(null);
    setPollFailure(null);
    if (!operationId) return;

    const poll = async () => {
      try {
        const result = await getSessionTransition(operationId);
        if (cancelled) return;
        if (result.operation_id !== operationId) {
          throw new Error(
            `transition response ${result.operation_id} does not match requested operation ${operationId}`,
          );
        }
        setSnapshot({ operationId, operation: result });
        setPollFailure(null);
        if (!transitionIsTerminal(result.state)) {
          timer = setTimeout(() => void poll(), pollIntervalMs);
        }
      } catch (cause) {
        if (cancelled) return;
        setPollFailure({
          operationId,
          message: cause instanceof Error ? cause.message : String(cause),
        });
        timer = setTimeout(() => void poll(), pollIntervalMs);
      }
    };
    void poll();
    return () => {
      cancelled = true;
      if (timer !== null) clearTimeout(timer);
    };
  }, [operationId, pollIntervalMs]);

  return {
    operation:
      snapshot?.operationId === operationId ? snapshot.operation : null,
    error:
      pollFailure?.operationId === operationId ? pollFailure.message : null,
  };
}

function DigestEvidence({
  label,
  reviewed,
  runtime,
}: {
  label: string;
  reviewed: string | null;
  runtime: string | null;
}) {
  const status =
    reviewed === null
      ? "review unavailable"
      : runtime === null
        ? "awaiting runtime proof"
        : reviewed === runtime
          ? "match"
          : "MISMATCH";
  return (
    <div className="builder-transition-digest" data-digest-status={status}>
      <strong>{label}</strong> · {status}
      <div className="builder-site-derived">reviewed: {reviewed ?? "not reported"}</div>
      <div className="builder-site-derived">runtime: {runtime ?? "not reported"}</div>
    </div>
  );
}

export function BuilderTransitionStatus({
  operationId,
  operation,
  pollError,
  reviewed,
}: {
  operationId: string;
  operation: TransitionOperation | null;
  pollError: string | null;
  reviewed: BuilderDigests;
}) {
  const stage = operation?.state ?? "accepted";
  return (
    <div
      className="builder-library-note"
      data-testid="builder-transition-status"
      role="status"
      aria-live="polite"
    >
      <div>
        deployment {operationId} · stage <strong>{stage}</strong>
      </div>
      {pollError && <div className="builder-warning">transition status unavailable: {pollError}</div>}
      {operation?.failure && (
        <div className="builder-warning" data-testid="builder-transition-failure">
          {operation.failure.code}: {operation.failure.message}
        </div>
      )}
      {operation && (
        <>
          <div className="builder-site-derived">
            runtime release {operation.facts.release} · build {operation.facts.build}
          </div>
          <div className="builder-transition-events" aria-label="Deployment stages">
            {operation.events.map((event, index) => (
              <span key={`${event.state}:${event.occurred_at}:${index}`}>
                {index > 0 ? " → " : ""}
                {event.state}
              </span>
            ))}
          </div>
          <DigestEvidence
            label="document digest"
            reviewed={reviewed.document}
            runtime={operation.facts.document_digest ?? null}
          />
          <DigestEvidence
            label="closure digest"
            reviewed={reviewed.dependency}
            runtime={operation.facts.closure_digest ?? null}
          />
          <DigestEvidence
            label="semantic digest"
            reviewed={reviewed.resolved_semantic ?? null}
            runtime={operation.facts.resolved_semantic_digest ?? null}
          />
        </>
      )}
    </div>
  );
}
