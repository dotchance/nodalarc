// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Stopgap contract test (deliverable #1): emit a representative corpus of the
 *  builder serializer's output (`toSessionDocument`) so the Python resolver
 *  contract test (`tests/unit/test_builder_serializer_contract.py`) can prove
 *  every builder-produced document resolves through real NodalArc. The corpus
 *  is checked in; this test regenerates it deterministically (fixed start_time)
 *  so a serializer change surfaces as a corpus diff. The frontend has no YAML
 *  library, so the corpus is JSON — the resolver ingests it identically.
 *
 *  This does NOT make the builder "aligned" (grammar still lives in two places);
 *  it bounds the drift. See specs/session-builder-requirement.md (ARCH target).
 */
import { describe, it, expect } from "vitest";
import { writeFileSync, mkdirSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import {
  newWorkspace,
  newDraftConstellation,
  newRefSegment,
  newDraftGroundSet,
  mintSiteMembers,
  parseSiteLines,
  defaultLinkRule,
  toSessionDocument,
  type Workspace,
  SCHEDULING_PRESETS,
} from "../workspace";

const HERE = dirname(fileURLToPath(import.meta.url));
const CORPUS = resolve(HERE, "../../../../tests/fixtures/builder-serializer-corpus");
const START = "2026-06-08T00:00:00Z";

/** A fresh workspace with a FIXED start_time so serializer output is
 *  deterministic (newWorkspace defaults start_time to "now"). */
const mk = (name: string): Workspace => {
  const w = newWorkspace(name);
  w.start_time = START;
  return w;
};
const seg = (segment_id: string, label: string, kind: "space" | "ground") => ({
  segment_id,
  label,
  kind,
});

/** Each case exercises a distinct composition the builder can author. */
const builders: Record<string, () => Workspace> = {
  "a-inline-constellation": () => {
    const w = mk("corpus-a-inline");
    w.space.push(newDraftConstellation("nodalarc:nodes/space/starlink-v2-mesh.yaml"));
    return w;
  },
  "b-ref-constellation": () => {
    const w = mk("corpus-b-ref");
    w.space_refs.push(
      newRefSegment("nodalarc:constellations/earth/leo/earth-leo-ring-36.yaml", "leo-ring"),
    );
    return w;
  },
  "c-authored-ground": () => {
    const w = mk("corpus-c-ground");
    const c = newDraftConstellation("nodalarc:nodes/space/starlink-v2-mesh.yaml");
    w.space.push(c);
    const g = newDraftGroundSet("nodalarc:nodes/ground/leo-gateway.yaml", { access_ka: 2 });
    g.members = mintSiteMembers(g, parseSiteLines("Denver, 39.7, -104.9").rows);
    w.ground.push(g);
    w.links.push(defaultLinkRule(seg(g.segment_id, "ground", "ground"), seg(c.segment_id, "space", "space")));
    return w;
  },
  "d-siteset-by-ref": () => {
    const w = mk("corpus-d-siteset-ref");
    const r = newRefSegment("nodalarc:constellations/earth/leo/earth-leo-ring-36.yaml", "leo-ring");
    w.space_refs.push(r);
    w.ground_refs.push({
      segment_id: "corpus-ground-ref",
      ref: "nodalarc:site-sets/earth/leo/earth-leo-starlink-pop-sites.yaml",
      label: "pop-sites",
      scheduling: SCHEDULING_PRESETS["leo-fast-handover"].block,
    });
    w.links.push(
      defaultLinkRule(seg("corpus-ground-ref", "pop-sites", "ground"), seg(r.segment_id, "leo-ring", "space")),
    );
    return w;
  },
  "e-mixed-multisegment": () => {
    const w = mk("corpus-e-mixed");
    const c = newDraftConstellation("nodalarc:nodes/space/starlink-v2-mesh.yaml");
    w.space.push(c);
    const r = newRefSegment("nodalarc:constellations/earth/geo/earth-geo-ring-8.yaml", "geo-ring");
    w.space_refs.push(r);
    const g = newDraftGroundSet("nodalarc:nodes/ground/leo-gateway.yaml", { access_ka: 2 });
    g.members = mintSiteMembers(g, parseSiteLines("Denver, 39.7, -104.9\nPerth, -31.9, 115.8").rows);
    w.ground.push(g);
    w.links.push(defaultLinkRule(seg(g.segment_id, "ground", "ground"), seg(c.segment_id, "space", "space")));
    return w;
  },
};

describe("builder serializer corpus (stopgap deliverable #1)", () => {
  it("emits a deterministic JSON corpus for the Python resolver contract", () => {
    mkdirSync(CORPUS, { recursive: true });
    let n = 0;
    for (const [name, build] of Object.entries(builders)) {
      const doc = toSessionDocument(build());
      writeFileSync(resolve(CORPUS, `${name}.json`), `${JSON.stringify(doc, null, 2)}\n`);
      n += 1;
    }
    expect(n).toBeGreaterThan(0);
    expect(readdirSync(CORPUS).filter((f) => f.endsWith(".json")).length).toBe(n);
  });
});
