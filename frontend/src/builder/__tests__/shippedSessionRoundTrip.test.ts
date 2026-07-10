// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Stopgap contract test (deliverable #2): the builder's coverage of the
 *  shipped (hand-written) product sessions, declared as an explicit
 *  COMPATIBILITY MATRIX rather than a soft "at least one" count.
 *
 *  Each shipped session is fed through the builder's import then export and
 *  must match its DECLARED outcome exactly:
 *    - "round-trip": import succeeds and re-serializes to the byte-identical
 *      source document — the builder fully models it.
 *    - "refuse": import returns typed issues naming a construct the builder
 *      does not model yet — never a silent mangle. The declared `because`
 *      substring must appear, so a refusal cannot quietly change its reason.
 *
 *  Hard gates (a merge blocker if either breaks):
 *    1. earth-leo-simple — one representative, ordinary shipped session — MUST
 *       round-trip. It is the proof that the builder faithfully authors at
 *       least one real product example, not just refuses everything.
 *    2. Every fixture matches its declared outcome exactly. A round-trip that
 *       starts refusing, a refusal that starts round-tripping, or a new/renamed
 *       fixture with no declared entry all fail the build — no silent category
 *       change slips through.
 *
 *  The frontend has no YAML library, so the fixtures are pre-generated JSON
 *  (tests/fixtures/shipped-sessions-json/, kept in sync with the shipped YAML by
 *  a Python drift guard in test_builder_serializer_contract.py). This bounds
 *  drift between the two grammar implementations; the aligned architecture
 *  (backend-owned grammar) is in specs/session-builder-requirement.md.
 */
import { describe, it, expect } from "vitest";
import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { workspaceFromSessionDocument } from "../workspaceImport";
import { toSessionDocument } from "../workspace";

const HERE = dirname(fileURLToPath(import.meta.url));
const FIXTURES = resolve(HERE, "../../../../tests/fixtures/shipped-sessions-json");
const FILES = readdirSync(FIXTURES)
  .filter((f) => f.endsWith(".json"))
  .sort();

const load = (file: string): Record<string, unknown> =>
  JSON.parse(readFileSync(resolve(FIXTURES, file), "utf8")) as Record<string, unknown>;

/** The representative session the builder must faithfully author. */
const REQUIRED_ROUND_TRIP = "earth-leo-simple.json";

type Expectation =
  | { outcome: "round-trip" }
  | { outcome: "refuse"; because: string };

/** The declared compatibility of every shipped session. Editing this table is
 *  a deliberate act: it records exactly what the builder can and cannot author,
 *  and each entry is enforced below. */
const MATRIX: Record<string, Expectation> = {
  // Fully modelled — import + canonical re-serialize with no semantic loss.
  "earth-leo-simple.json": { outcome: "round-trip" },
  "earth-geo-inmarsat.json": { outcome: "round-trip" },
  "earth-geo-tdrs.json": { outcome: "round-trip" },
  "earth-meo-gps.json": { outcome: "round-trip" },
  // Fixed ISL pair lists — the builder authors topology by rule, not by
  // enumerated pairs. Refused explicitly until explicit_pairs is a construct.
  "earth-leo-polar.json": { outcome: "refuse", because: "explicit_pairs" },
  "earth-leo-walker.json": { outcome: "refuse", because: "explicit_pairs" },
  // Addressing pools + dispatch + per-site originated prefixes: real grammar
  // the builder does not model as first-class constructs yet (it must not be
  // approximated with builder-specific controls — see the owner requirement).
  "earth-leo-heo-geo-luna-reachability.json": {
    outcome: "refuse",
    because: "addressing",
  },
};

describe("shipped session compatibility matrix (stopgap deliverable #2)", () => {
  it("has shipped fixtures to test", () => {
    expect(FILES.length).toBeGreaterThan(0);
  });

  it("the matrix declares every shipped fixture, and only real fixtures", () => {
    // A new or renamed shipped session with no declared expectation fails here,
    // forcing it to be classified round-trip vs refuse — never silently skipped.
    expect(FILES.slice().sort()).toEqual(Object.keys(MATRIX).sort());
  });

  it(`HARD GATE: ${REQUIRED_ROUND_TRIP} round-trips canonically`, () => {
    const original = load(REQUIRED_ROUND_TRIP);
    const result = workspaceFromSessionDocument(original);
    // No typed refusal: the builder models every construct in this session.
    expect(result.issues).toBeUndefined();
    // Byte-for-byte reassembly — toEqual reports the exact differing fields if
    // this ever regresses, so we never loosen the equality to make it pass.
    expect(toSessionDocument(result.workspace!)).toEqual(original);
  });

  for (const file of FILES) {
    const expectation = MATRIX[file];
    if (!expectation) continue; // covered by the completeness test above
    it(`${file}: ${expectation.outcome}${
      expectation.outcome === "refuse" ? ` (${expectation.because})` : ""
    }`, () => {
      const original = load(file);
      const result = workspaceFromSessionDocument(original);
      if (expectation.outcome === "round-trip") {
        expect(result.issues).toBeUndefined();
        expect(toSessionDocument(result.workspace!)).toEqual(original);
      } else {
        // Typed, non-empty refusal naming the declared unmodelled construct.
        expect(result.issues).toBeDefined();
        expect(result.issues!.length).toBeGreaterThan(0);
        expect(result.issues!.some((i) => i.includes(expectation.because))).toBe(true);
      }
    });
  }
});
