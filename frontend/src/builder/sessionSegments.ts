// Copyright 2024-2026 .chance (dotchance)
// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
/** The segments of a session document and the documents the session proposes,
 *  read for the editors that name and open them. */

import type { BuilderProposedCatalogDocument, JsonValue } from "./generated/builderApi";

export type JsonObject = Readonly<Record<string, JsonValue>>;

/** One segment of the session: its id, the name it is shown with, and whether
 *  it places space nodes (`source`) or ground sites (`placement`). */
export interface SessionSegment {
  readonly segment_id: string;
  readonly label: string;
  readonly kind: "space" | "ground";
  /** The segment as the session holds it. */
  readonly segment: JsonObject;
}

export function isJsonObject(value: JsonValue | undefined): value is JsonObject {
  return value !== null && value !== undefined && typeof value === "object" && !Array.isArray(value);
}

/** One segment document as the editors read it; null for anything that is
 *  not a segment with an id. */
export function sessionSegment(segment: JsonValue | undefined): SessionSegment | null {
  if (!isJsonObject(segment) || typeof segment.id !== "string") return null;
  return {
    segment_id: segment.id,
    label: typeof segment.display_name === "string" ? segment.display_name : segment.id,
    kind: "placement" in segment ? "ground" : "space",
    segment,
  };
}

/** Every segment the session declares, in session order. */
export function sessionSegments(session: JsonObject | null | undefined): SessionSegment[] {
  const segments = session?.segments;
  if (!Array.isArray(segments)) return [];
  return segments.flatMap((segment) => {
    const read = sessionSegment(segment);
    return read === null ? [] : [read];
  });
}

/** The body of the document the session proposes at `ref` under its family
 *  key (`constellation`, `orbit`, `site_set`, ...); null when the session
 *  proposes no document there. */
export function proposedBody(
  proposals: ReadonlyArray<BuilderProposedCatalogDocument> | undefined,
  ref: string | null | undefined,
  familyKey: string,
): JsonObject | null {
  if (!ref) return null;
  const proposal = (proposals ?? []).find((candidate) => candidate.ref === ref);
  const body = proposal?.document[familyKey];
  return isJsonObject(body) ? body : null;
}

/** The file name of a catalog ref without its extension: a component's id. */
export function refStem(ref: string): string {
  return ref.slice(ref.lastIndexOf("/") + 1).replace(/\.ya?ml$/, "");
}
