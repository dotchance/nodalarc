// Copyright 2024-2026 .chance (dotchance)
// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
/** One YAML buffer over a backend document.
 *
 *  The buffer holds the text the user types, the text the document holds, and
 *  the backend's answer to each generation of the text. The session pane and
 *  the component window both keep their buffer here, apply it after the same
 *  pause in typing, and replace it with canonical text the same way.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import type { BuilderIssue } from "./generated/builderApi";

/** The pause in typing after which the buffer is applied. */
export const YAML_APPLY_DELAY_MS = 500;

export interface YamlBufferState {
  text: string;
  appliedText: string;
  generation: number;
  /** The newest generation handed to an apply, or set from the document. The
   *  automatic apply sends only a newer generation. */
  sentGeneration: number;
  dirty: boolean;
  /** Whether the document holds `appliedText`. A refused apply leaves the
   *  document as it was, so this stays false until an apply is accepted. An
   *  edit of the buffer does not change it. */
  applied: boolean;
  canonicalizationRequired: boolean;
  canonicalizationAccepted: boolean;
  /** The backend's findings for this generation. A new generation holds none
   *  until the backend answers it. */
  issues: readonly BuilderIssue[];
  /** Why the latest apply of this generation failed with no answer from the
   *  backend for it; null when none failed. A new generation holds none. */
  applyFailure: string | null;
}

/** The backend's answer to one apply of a buffer generation. */
export interface YamlApplyAnswer {
  readonly applied: boolean;
  readonly yaml_text: string;
  readonly canonicalization_required: boolean;
  readonly issues?: readonly BuilderIssue[] | null;
}

export const EMPTY_YAML_BUFFER: YamlBufferState = {
  text: "",
  appliedText: "",
  generation: 0,
  sentGeneration: 0,
  applyFailure: null,
  dirty: false,
  applied: false,
  canonicalizationRequired: false,
  canonicalizationAccepted: false,
  issues: [],
};

/** The buffer showing `text`, which the document holds. */
export function adoptedYamlBuffer(
  text: string,
  generation: number,
  canonicalizationRequired = false,
): YamlBufferState {
  return {
    text,
    appliedText: text,
    generation,
    sentGeneration: generation,
    applyFailure: null,
    dirty: false,
    applied: true,
    canonicalizationRequired,
    canonicalizationAccepted: false,
    issues: [],
  };
}

/** The buffer after the backend answered an apply of its generation. */
export function answeredYamlBuffer(
  previous: YamlBufferState,
  generation: number,
  answer: YamlApplyAnswer,
): YamlBufferState {
  const issues = answer.issues ?? [];
  if (!answer.applied) {
    return {
      ...previous,
      text: answer.yaml_text,
      generation,
      sentGeneration: generation,
      applyFailure: null,
      dirty: answer.yaml_text !== previous.appliedText,
      applied: false,
      canonicalizationRequired: false,
      canonicalizationAccepted: false,
      issues,
    };
  }
  return {
    text: answer.yaml_text,
    appliedText: answer.yaml_text,
    generation,
    sentGeneration: generation,
    applyFailure: null,
    dirty: false,
    applied: true,
    canonicalizationRequired: answer.canonicalization_required,
    canonicalizationAccepted: false,
    issues,
  };
}

/** Whether the automatic apply sends the buffer: no apply was handed its
 *  generation, and the document does not hold its text. */
export function yamlBufferAwaitsApply(buffer: YamlBufferState): boolean {
  return buffer.generation !== buffer.sentGeneration && (buffer.dirty || !buffer.applied);
}

/** Whether the buffer holds text the graphical editors must not edit around:
 *  unapplied text, or applied text whose canonical form was not accepted. */
export function yamlBufferBlocksGraphicalEditing(buffer: YamlBufferState): boolean {
  return (
    buffer.dirty ||
    !buffer.applied ||
    (buffer.canonicalizationRequired && !buffer.canonicalizationAccepted)
  );
}

export function useYamlBuffer(initial: YamlBufferState = EMPTY_YAML_BUFFER) {
  const [state, setState] = useState<YamlBufferState>(initial);
  const ref = useRef<YamlBufferState>(initial);

  const set = useCallback((next: YamlBufferState) => {
    ref.current = next;
    setState(next);
  }, []);

  /** The user's text as a new generation. The document stays as it was, so
   *  `applied` stands, and an older generation's findings are not this one's. */
  const edit = useCallback(
    (text: string) => {
      const current = ref.current;
      set({
        ...current,
        text,
        generation: current.generation + 1,
        dirty: text !== current.appliedText,
        issues: [],
        applyFailure: null,
      });
    },
    [set],
  );

  /** Mark `generation` handed to an apply before it waits its turn, so the
   *  automatic apply never sends it again. */
  const markSent = useCallback(
    (generation: number) => {
      const current = ref.current;
      if (current.generation === generation && current.sentGeneration !== generation) {
        set({ ...current, sentGeneration: generation });
      }
    },
    [set],
  );

  /** Hold an apply failure against the generation it belongs to, so it shows
   *  until a newer generation replaces it. */
  const holdFailure = useCallback(
    (generation: number, cause: unknown) => {
      const current = ref.current;
      if (current.generation !== generation) return;
      set({ ...current, applyFailure: cause instanceof Error ? cause.message : String(cause) });
    },
    [set],
  );

  /** Replace the text with the canonical text the backend produced, as a new
   *  generation the caller applies. Returns that generation. */
  const canonicalize = useCallback(
    (canonical: string) => {
      const current = ref.current;
      const generation = current.generation + 1;
      set({
        ...current,
        text: canonical,
        generation,
        dirty: canonical !== current.appliedText,
        canonicalizationAccepted: true,
        issues: [],
        applyFailure: null,
      });
      return generation;
    },
    [set],
  );

  return { state, ref, set, edit, markSent, holdFailure, canonicalize };
}

/** Apply a buffer generation the document does not hold once, after a pause
 *  in typing, while `allowed`. A refused generation is not sent again; the
 *  next edit makes a newer one. */
export function useYamlAutoApply(
  buffer: YamlBufferState,
  allowed: boolean,
  applyOnce: (generation: number) => void,
): void {
  const awaits = yamlBufferAwaitsApply(buffer);
  const generation = buffer.generation;
  useEffect(() => {
    if (!allowed || !awaits) return;
    const timer = setTimeout(() => applyOnce(generation), YAML_APPLY_DELAY_MS);
    return () => clearTimeout(timer);
  }, [allowed, awaits, generation, applyOnce]);
}
