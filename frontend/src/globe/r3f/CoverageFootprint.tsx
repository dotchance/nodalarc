// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * CoverageFootprint — radial-falloff coverage discs on the current body's surface beneath
 * satellites (ground stations get the elevation cone in <GroundStations>, never this).
 * Each disc is a CircleGeometry(radius, 96) whose radius = computeConeRadius(elevDeg, satAltKm)
 * — reused verbatim from globe/groundStations.ts so the footprint scale matches the rest of
 * the scene — textured with the exact radial-gradient ShaderMaterial (r = length(vUv-0.5)*2;
 * discard r>0.98; sinElev = sin((1-r)*PI/2); alpha = pow(sinElev, u_falloff)*0.15) tinted
 * FOOTPRINT_COLOR. u_falloff is the satellite's beam_falloff_exponent (higher → tighter
 * center, e.g. Iridium 3.5; lower → broader, e.g. Starlink 2.0), defaulting to 2.0.
 *
 * Which satellites get a disc: the SELECTED satellite always; plus any node ids the hosting
 * surface requests through the `beams` prop (the builder shows every satellite of a segment
 * whose editor window is open). The elevation floor likewise comes from the hosting surface
 * when it has real terminal data — `beams.elevationFor` returning null means the node has no
 * access terminal, so no beam is drawn rather than a made-up one. Without the prop the legacy
 * display default (25°) applies, as in the live view where per-node terminal data does not
 * reach the snapshot.
 *
 * Lives inside a <Body> (body-child), so positions are in that body's local frame. Each frame
 * a disc reads its satellite's body-LOCAL position from the shared registry (after
 * <Constellation> at priority -1 has written it) and places itself at the sub-satellite point
 * on the surface, oriented so its local -Z faces outward. Geometry rebuilds only when the
 * satellite or its altitude (>1 km) changes; otherwise only the u_falloff uniform is
 * refreshed. Zero per-frame heap allocation — all THREE temporaries are module/ref scoped.
 */

import { useEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import { useFrame } from "@react-three/fiber";
import { computeConeRadius } from "../groundStations";
import { tokens } from "../../styles/tokens";
import { getNodeLocalPosition } from "./positions";
import { useBodyFrame } from "./BodyFrame";
import type { NodeState, Selection } from "../../types";

const FOOTPRINT_COLOR = new THREE.Color(tokens.colorFootprint);
const MIN_ELEV_DEG = 25;
const SEGMENTS = 96;
const DEFAULT_FALLOFF = 2.0;

const vertexShader = `
  varying vec2 vUv;
  void main() {
    vUv = uv;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

const fragmentShader = `
  uniform float u_falloff;
  uniform vec3 u_color;
  varying vec2 vUv;

  const float PI = 3.141592653589793;

  void main() {
    float r = length(vUv - 0.5) * 2.0;
    if (r > 0.98) discard;
    float sinElev = sin((1.0 - r) * PI * 0.5);
    float alpha = pow(sinElev, u_falloff) * 0.15;
    gl_FragColor = vec4(u_color, alpha);
  }
`;

/** Local axis oriented to face outward (the disc's local -Z), reproducing the legacy port. */
const _FOOTPRINT_LOCAL_Z_AXIS = new THREE.Vector3(0, 0, -1);

// Reusable per-frame temporaries — hoisted to module scope (zero-alloc steady state).
const _localPos = new THREE.Vector3();
const _outward = new THREE.Vector3();
const _surfacePos = new THREE.Vector3();

/** Beam requests from the hosting surface: extra satellites to draw discs
 *  for, and the elevation floor per node from real terminal data (null =
 *  no access terminal = no beam). */
export interface BeamFootprints {
  nodeIds: readonly string[];
  elevationFor: (nodeId: string) => number | null;
}

interface FootprintDiscProps {
  sat: NodeState;
  elevDeg: number;
}

function FootprintDisc({ sat, elevDeg }: FootprintDiscProps) {
  const meshRef = useRef<THREE.Mesh>(null);
  const bodyFrame = useBodyFrame();

  const altKm = sat.alt_km ?? 0;
  const falloff = sat.beam_falloff_exponent ?? DEFAULT_FALLOFF;

  // Rebuild geometry ONLY when the satellite changes or its altitude shifts >1 km (the legacy
  // gate). Quantizing altKm to whole km gives a stable memo key with that exact threshold, so
  // sub-km orbital drift never thrashes the geometry — only the u_falloff uniform updates.
  const altKmQuant = Math.round(altKm);
  const geometry = useMemo(
    () =>
      new THREE.CircleGeometry(
        computeConeRadius(elevDeg, altKm, bodyFrame.radiusKm, bodyFrame.kmPerRenderUnit),
        SEGMENTS,
      ),
    // altKm intentionally excluded; altKmQuant is the >1km-change gate.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [sat.node_id, altKmQuant, elevDeg, bodyFrame.radiusKm, bodyFrame.kmPerRenderUnit],
  );

  const material = useMemo(
    () =>
      new THREE.ShaderMaterial({
        uniforms: {
          u_falloff: { value: falloff },
          u_color: { value: FOOTPRINT_COLOR },
        },
        vertexShader,
        fragmentShader,
        transparent: true,
        side: THREE.DoubleSide,
        depthWrite: false,
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  // Dispose the swapped-out geometry; React swaps the <mesh geometry> attribute but does not
  // free the previous BufferGeometry, so free it explicitly when a new one supersedes it.
  useEffect(() => () => geometry.dispose(), [geometry]);
  useEffect(() => () => material.dispose(), [material]);

  // Default priority (0): runs after FrameDriver (-2) and Constellation (-1) so the satellite's
  // body-local position read here is this frame's. Refresh u_falloff cheaply each frame; place
  // and orient the disc at the sub-satellite surface point.
  useFrame(() => {
    const mesh = meshRef.current;
    if (!mesh) return;
    const falloffUniform = material.uniforms.u_falloff;
    if (falloffUniform) falloffUniform.value = falloff;

    if (!getNodeLocalPosition(sat.node_id, _localPos)) {
      mesh.visible = false;
      return;
    }
    _outward.copy(_localPos).normalize();
    _surfacePos.copy(_outward).multiplyScalar(bodyFrame.radiusRender * 1.002);

    mesh.position.copy(_surfacePos);
    mesh.quaternion.setFromUnitVectors(_FOOTPRINT_LOCAL_Z_AXIS, _outward);
    mesh.visible = true;
  });

  return (
    <mesh ref={meshRef} geometry={geometry} material={material} renderOrder={1} visible={false} />
  );
}

interface CoverageFootprintProps {
  selection: Selection | null;
  nodes: NodeState[];
  beams?: BeamFootprints;
}

export function CoverageFootprint({ selection, nodes, beams }: CoverageFootprintProps) {
  // One disc per satellite that earned one: the selection, plus the hosting
  // surface's requests — deduped, restricted to THIS body's nodes (the scene
  // renders one CoverageFootprint per body).
  const discs = useMemo(() => {
    const wanted = new Set<string>();
    if (selection?.type === "satellite") wanted.add(selection.id);
    for (const id of beams?.nodeIds ?? []) wanted.add(id);
    const out: { sat: NodeState; elevDeg: number }[] = [];
    for (const sat of nodes) {
      if (sat.node_type !== "satellite" || !wanted.has(sat.node_id)) continue;
      const elevDeg = beams ? beams.elevationFor(sat.node_id) : MIN_ELEV_DEG;
      if (elevDeg === null) continue;
      out.push({ sat, elevDeg });
    }
    return out;
  }, [selection, nodes, beams]);

  return (
    <>
      {discs.map(({ sat, elevDeg }) => (
        <FootprintDisc key={sat.node_id} sat={sat} elevDeg={elevDeg} />
      ))}
    </>
  );
}
