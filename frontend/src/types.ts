// Copyright 2024-2026 .chance (dotchance)
// SPDX-License-Identifier: AGPL-3.0-only
/** Page-local types. The wire types VS-API serves are generated into
 *  generated/vsApi.ts and builder/generated/builderApi.ts.
 */

/** App-level selection state */
type SelectionType = "satellite" | "ground_station" | "link" | null;

export interface Selection {
  type: SelectionType;
  id: string; // node_id or "nodeA:nodeB" for links
}

/** View modes */
export type ViewMode = "globe" | "topology" | "split" | "dashboard" | "builder";
export type ColorMode = "regime" | "area" | "plane";
export type GlobeMode = "blue-marble" | "day-night" | "political";

/** Reference frame for the globe view.
 *  - "earth-fixed": Earth static, satellites trace ground tracks, stars rotate
 *    at sidereal rate beneath the fixed Earth. Current-behavior default.
 *  - "earth-inertial": observer fixed in inertial space, Earth visibly rotates
 *    at sidereal rate, satellites visibly traverse orbits, stars stationary.
 *
 *  Namespace reserves the "earth-*" prefix so future body and barycenter
 *  reference frames can be added without renaming existing values. */
export type ReferenceFrame = "earth-fixed" | "earth-inertial";
