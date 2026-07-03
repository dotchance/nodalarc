// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Workspace state: the applied session drafts plus undo and autosave.
 *
 *  The edit→resolve loop lives in BuilderView, which serializes the
 *  workspace WITH any open windows' working copies overlaid — the canvas
 *  previews what is being edited while the workspace itself only changes on
 *  Apply. The rendered world is always the resolver's expansion of that
 *  serialization — or the resolver's error, verbatim. No builder-local
 *  expansion, ever.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  newDraftConstellation,
  reseedCounters,
  newRefGroundSet,
  newRefSegment,
  newWorkspace,
  type DraftConstellation,
  type DraftGroundSet,
  type DraftGroundSite,
  type DraftBoundary,
  type DraftLinkEndpoint,
  type DraftLinkRule,
  type DraftRoutingDomain,
  type DraftOrbit,
  type RefGroundSet,
  type Workspace,
} from "./workspace";

const AUTOSAVE_KEY = "nodalarc-builder-draft";
const AUTOSAVE_DEBOUNCE_MS = 800;
const HISTORY_LIMIT = 100;

export function useWorkspace() {
  const [workspace, setWorkspace] = useState<Workspace | null>(null);

  // Trust mechanics: every mutation lands in a bounded history (undo) and a
  // debounced localStorage autosave (restore-after-crash/refresh).
  const historyRef = useRef<(Workspace | null)[]>([]);
  const skipHistoryRef = useRef(false);
  const previousRef = useRef<Workspace | null>(null);
  useEffect(() => {
    if (skipHistoryRef.current) {
      skipHistoryRef.current = false;
    } else if (previousRef.current !== workspace) {
      historyRef.current.push(previousRef.current);
      if (historyRef.current.length > HISTORY_LIMIT) historyRef.current.shift();
    }
    previousRef.current = workspace;
  }, [workspace]);

  const undo = useCallback(() => {
    if (historyRef.current.length === 0) return;
    const past = historyRef.current.pop() as Workspace | null;
    skipHistoryRef.current = true;
    setWorkspace(past);
  }, []);

  useEffect(() => {
    if (!workspace) return;
    const timer = setTimeout(() => {
      try {
        localStorage.setItem(AUTOSAVE_KEY, JSON.stringify(workspace));
      } catch {
        // Quota/private-mode failures must never break editing.
      }
    }, AUTOSAVE_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [workspace]);

  /** True when an autosaved draft exists to offer on the start card. */
  const hasAutosave = useCallback((): boolean => {
    try {
      return localStorage.getItem(AUTOSAVE_KEY) !== null;
    } catch {
      return false;
    }
  }, []);

  const restoreAutosave = useCallback((): boolean => {
    try {
      const raw = localStorage.getItem(AUTOSAVE_KEY);
      if (!raw) return false;
      const restored = JSON.parse(raw) as Workspace;
      // Fresh module counters would re-mint ids the restored draft already
      // uses — reseed past everything it carries.
      reseedCounters(restored);
      setWorkspace(restored);
      return true;
    } catch {
      return false;
    }
  }, []);

  const discardAutosave = useCallback(() => {
    try {
      localStorage.removeItem(AUTOSAVE_KEY);
    } catch {
      // Nothing to discard when storage is unavailable.
    }
  }, []);

  const startNew = useCallback((name: string) => {
    setWorkspace(newWorkspace(name));
  }, []);

  /** Session-level plumbing: name, time, and the candidate budget. */
  const updateSession = useCallback(
    (
      patch: Partial<
        Pick<
          Workspace,
          | "name"
          | "start_time"
          | "step_seconds"
          | "compression"
          | "max_pairs_per_rule"
          | "max_pairs_per_tick"
        >
      >,
    ) => {
      setWorkspace((prev) => (prev ? { ...prev, ...patch } : prev));
    },
    [],
  );

  const close = useCallback(() => setWorkspace(null), []);

  // Library "use" gestures are self-ensuring: using a block with no open
  // workspace starts one - building never dead-ends on missing state.
  const addConstellation = useCallback((nodeRef: string) => {
    setWorkspace((prev) => {
      const workspace = prev ?? newWorkspace("untitled-session");
      return { ...workspace, space: [...workspace.space, newDraftConstellation(nodeRef)] };
    });
  }, []);

  const addConstellationRef = useCallback((ref: string, label: string) => {
    setWorkspace((prev) => {
      const workspace = prev ?? newWorkspace("untitled-session");
      return { ...workspace, space_refs: [...workspace.space_refs, newRefSegment(ref, label)] };
    });
  }, []);

  /** Add an already-built draft (a fork of a library block). */
  const addDraft = useCallback((draft: DraftConstellation) => {
    setWorkspace((prev) => {
      const workspace = prev ?? newWorkspace("untitled-session");
      return { ...workspace, space: [...workspace.space, draft] };
    });
  }, []);

  const removeRefSegment = useCallback((segmentId: string) => {
    setWorkspace((prev) =>
      prev
        ? { ...prev, space_refs: prev.space_refs.filter((r) => r.segment_id !== segmentId) }
        : prev,
    );
  }, []);

  /** Customize-a-block: swap a placed reference for its forked draft. */
  const replaceRefWithDraft = useCallback(
    (segmentId: string, draft: DraftConstellation) => {
      setWorkspace((prev) =>
        prev
          ? {
              ...prev,
              space_refs: prev.space_refs.filter((r) => r.segment_id !== segmentId),
              space: [...prev.space, draft],
            }
          : prev,
      );
    },
    [],
  );

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

  const addGroundRef = useCallback((ref: string, label: string) => {
    setWorkspace((prev) => {
      const workspace = prev ?? newWorkspace("untitled-session");
      return { ...workspace, ground_refs: [...workspace.ground_refs, newRefGroundSet(ref, label)] };
    });
  }, []);

  const updateGroundRef = useCallback(
    (segmentId: string, patch: Partial<RefGroundSet>) => {
      setWorkspace((prev) =>
        prev
          ? {
              ...prev,
              ground_refs: prev.ground_refs.map((placed) =>
                placed.segment_id === segmentId ? { ...placed, ...patch } : placed,
              ),
            }
          : prev,
      );
    },
    [],
  );

  const removeGroundRef = useCallback((segmentId: string) => {
    setWorkspace((prev) =>
      prev
        ? { ...prev, ground_refs: prev.ground_refs.filter((r) => r.segment_id !== segmentId) }
        : prev,
    );
  }, []);

  /** Add an authored (or forked) ground segment draft. */
  const addGroundDraft = useCallback((draft: DraftGroundSet) => {
    setWorkspace((prev) => {
      const workspace = prev ?? newWorkspace("untitled-session");
      return { ...workspace, ground: [...workspace.ground, draft] };
    });
  }, []);

  /** Place a defined site into the LAST ground segment draft — self-ensuring:
   *  with no draft (or no workspace) open, makeDraft starts one, so using a
   *  site from the Library never dead-ends. */
  const addGroundMember = useCallback(
    (member: DraftGroundSite, makeDraft: () => DraftGroundSet) => {
      setWorkspace((prev) => {
        const workspace = prev ?? newWorkspace("untitled-session");
        if (workspace.ground.length === 0) {
          const draft = makeDraft();
          return { ...workspace, ground: [{ ...draft, members: [member] }] };
        }
        const last = workspace.ground[workspace.ground.length - 1] as DraftGroundSet;
        return {
          ...workspace,
          ground: workspace.ground.map((draft) =>
            draft === last ? { ...draft, members: [...draft.members, member] } : draft,
          ),
        };
      });
    },
    [],
  );

  /** Connect two placed segments (self-ensuring is NOT needed here: a link
   *  rule requires segments, so the workspace always exists first). */
  const addLinkRule = useCallback((rule: DraftLinkRule) => {
    setWorkspace((prev) => (prev ? { ...prev, links: [...prev.links, rule] } : prev));
  }, []);

  const updateLinkRule = useCallback((ruleId: string, patch: Partial<DraftLinkRule>) => {
    setWorkspace((prev) =>
      prev
        ? {
            ...prev,
            links: prev.links.map((rule) =>
              rule.rule_id === ruleId ? { ...rule, ...patch } : rule,
            ),
          }
        : prev,
    );
  }, []);

  const updateLinkEndpoint = useCallback(
    (ruleId: string, side: "a" | "b", patch: Partial<DraftLinkEndpoint>) => {
      setWorkspace((prev) =>
        prev
          ? {
              ...prev,
              links: prev.links.map((rule) =>
                rule.rule_id === ruleId
                  ? { ...rule, [side]: { ...rule[side], ...patch } }
                  : rule,
              ),
            }
          : prev,
      );
    },
    [],
  );

  const removeLinkRule = useCallback((ruleId: string) => {
    setWorkspace((prev) =>
      prev ? { ...prev, links: prev.links.filter((rule) => rule.rule_id !== ruleId) } : prev,
    );
  }, []);

  const addRoutingDomain = useCallback((domain: DraftRoutingDomain) => {
    setWorkspace((prev) =>
      prev ? { ...prev, routing_domains: [...prev.routing_domains, domain] } : prev,
    );
  }, []);

  const updateRoutingDomain = useCallback(
    (domainId: string, patch: Partial<DraftRoutingDomain>) => {
      setWorkspace((prev) =>
        prev
          ? {
              ...prev,
              routing_domains: prev.routing_domains.map((domain) =>
                domain.domain_id === domainId ? { ...domain, ...patch } : domain,
              ),
            }
          : prev,
      );
    },
    [],
  );

  const removeRoutingDomain = useCallback((domainId: string) => {
    setWorkspace((prev) =>
      prev
        ? {
            ...prev,
            routing_domains: prev.routing_domains.filter((d) => d.domain_id !== domainId),
          }
        : prev,
    );
  }, []);

  const addBoundary = useCallback((boundary: DraftBoundary) => {
    setWorkspace((prev) =>
      prev ? { ...prev, boundaries: [...prev.boundaries, boundary] } : prev,
    );
  }, []);

  const updateBoundary = useCallback(
    (boundaryId: string, patch: Partial<DraftBoundary>) => {
      setWorkspace((prev) =>
        prev
          ? {
              ...prev,
              boundaries: prev.boundaries.map((boundary) =>
                boundary.boundary_id === boundaryId ? { ...boundary, ...patch } : boundary,
              ),
            }
          : prev,
      );
    },
    [],
  );

  const removeBoundary = useCallback((boundaryId: string) => {
    setWorkspace((prev) =>
      prev
        ? { ...prev, boundaries: prev.boundaries.filter((b) => b.boundary_id !== boundaryId) }
        : prev,
    );
  }, []);

  /** Customize-a-block for ground: swap a placed reference for its fork. */
  const replaceGroundRefWithDraft = useCallback(
    (segmentId: string, draft: DraftGroundSet) => {
      setWorkspace((prev) =>
        prev
          ? {
              ...prev,
              ground_refs: prev.ground_refs.filter((r) => r.segment_id !== segmentId),
              ground: [...prev.ground, draft],
            }
          : prev,
      );
    },
    [],
  );

  const updateGroundDraft = useCallback(
    (segmentId: string, patch: Partial<DraftGroundSet>) => {
      setWorkspace((prev) =>
        prev
          ? {
              ...prev,
              ground: prev.ground.map((draft) =>
                draft.segment_id === segmentId ? { ...draft, ...patch } : draft,
              ),
            }
          : prev,
      );
    },
    [],
  );

  const removeGroundDraft = useCallback((segmentId: string) => {
    setWorkspace((prev) =>
      prev
        ? { ...prev, ground: prev.ground.filter((d) => d.segment_id !== segmentId) }
        : prev,
    );
  }, []);

  return {
    workspace,
    startNew,
    updateSession,
    undo,
    hasAutosave,
    restoreAutosave,
    discardAutosave,
    close,
    addConstellation,
    addConstellationRef,
    addDraft,
    removeRefSegment,
    replaceRefWithDraft,
    removeConstellation,
    updateConstellation,
    updateOrbit,
    addGroundRef,
    updateGroundRef,
    removeGroundRef,
    addGroundDraft,
    addGroundMember,
    replaceGroundRefWithDraft,
    updateGroundDraft,
    removeGroundDraft,
    addLinkRule,
    updateLinkRule,
    updateLinkEndpoint,
    removeLinkRule,
    addRoutingDomain,
    updateRoutingDomain,
    removeRoutingDomain,
    addBoundary,
    updateBoundary,
    removeBoundary,
  };
}
