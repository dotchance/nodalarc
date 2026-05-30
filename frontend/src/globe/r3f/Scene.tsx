// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * The R3F scene root. Earth (blue-marble + atmosphere) in the Earth body frame, the
 * inertial starfield at the universe root. Constellations (instanced satellites), ground
 * stations, beams, overlays, and the selection layer are added as further child subtrees
 * in later phases. Mounted only behind the `?r3f` flag (App) — the imperative globe stays
 * live until this declarative scene reaches parity, so the working visualization is never
 * broken during the migration.
 */

import { Universe } from "./Universe";
import { Body } from "./Body";
import { Earth, Starfield } from "./Earth";
import { EARTH_RADIUS_KM } from "./units";

export function Scene() {
  return (
    <Universe>
      <Starfield />
      <Body id="earth" radiusKm={EARTH_RADIUS_KM}>
        <Earth />
      </Body>
    </Universe>
  );
}
