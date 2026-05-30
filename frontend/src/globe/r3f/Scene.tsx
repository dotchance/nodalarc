// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * The R3F scene root. Earth-only today; constellations (instanced satellites),
 * ground stations, envelope cones, beams, and the selection overlay are added as
 * child subtrees of a <Body>. NOT yet wired into the app — the imperative globe/
 * stays live until this declarative scene reaches parity, so the working
 * visualization is never broken during the migration.
 */

import { Universe } from "./Universe";
import { Body } from "./Body";
import { EARTH_RADIUS_KM } from "./units";

export function Scene() {
  return (
    <Universe>
      <Body id="earth" radiusKm={EARTH_RADIUS_KM} />
    </Universe>
  );
}
