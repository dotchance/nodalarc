// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * CoverageFootprint — the radial-falloff coverage disc on the current body's surface beneath the
 * SELECTED satellite only (ground stations get the elevation cone in <GroundStations>,
 * never this). Renders a CircleGeometry(radius, 96)
 * whose radius = computeConeRadius(MIN_ELEV_DEG=25, satAltKm) — reused verbatim from
 * globe/groundStations.ts so the footprint scale matches the rest of the scene — textured
 * with the exact radial-gradient ShaderMaterial (r = length(vUv-0.5)*2; discard r>0.98;
 * sinElev = sin((1-r)*PI/2); alpha = pow(sinElev, u_falloff)*0.15) tinted FOOTPRINT_COLOR
 * (0xff44aa). u_falloff is the satellite's beam_falloff_exponent (higher → tighter center,
 * e.g. Iridium 3.5; lower → broader, e.g. Starlink 2.0), defaulting to 2.0.
 *
 * Lives inside a <Body> (body-child), so its position is in that body's local frame.
 * Each frame it reads the satellite's body-LOCAL position from the shared registry (after
 * <Constellation> at priority -1 has written it) and places the disc at the sub-satellite
 * point on the surface, oriented so its local -Z faces outward. Geometry is rebuilt only when
 * the selected satellite changes or its altitude moves >1 km; otherwise only the u_falloff
 * uniform is refreshed (the legacy cheap-update path). Hidden whenever the selection is not a
 * satellite. Zero per-frame heap allocation — all THREE temporaries are module/ref scoped.
 */

import { useEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import { useFrame } from "@react-three/fiber";
import { computeConeRadius } from "../groundStations";
import { getNodeLocalPosition } from "./positions";
import { useBodyFrame } from "./BodyFrame";
import type { NodeState, Selection } from "../../types";

const FOOTPRINT_COLOR = new THREE.Color(0xff44aa);
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

interface CoverageFootprintProps {
  selection: Selection | null;
  nodes: NodeState[];
}

export function CoverageFootprint({ selection, nodes }: CoverageFootprintProps) {
  const meshRef = useRef<THREE.Mesh>(null);
  const bodyFrame = useBodyFrame();

  // The selected satellite's node (only satellites get the footprint).
  const sat = useMemo(() => {
    if (!selection || selection.type !== "satellite") return null;
    return nodes.find((n) => n.node_id === selection.id && n.node_type === "satellite") ?? null;
  }, [selection, nodes]);

  const altKm = sat?.alt_km ?? 0;
  const falloff = sat?.beam_falloff_exponent ?? DEFAULT_FALLOFF;

  // Rebuild geometry ONLY when the satellite changes or its altitude shifts >1 km (the legacy
  // gate). Quantizing altKm to whole km gives a stable memo key with that exact threshold, so
  // sub-km orbital drift never thrashes the geometry — only the u_falloff uniform updates.
  const altKmQuant = sat ? Math.round(altKm) : 0;
  const geometry = useMemo(
    () =>
      sat
        ? new THREE.CircleGeometry(
            computeConeRadius(
              MIN_ELEV_DEG,
              altKm,
              bodyFrame.radiusKm,
              bodyFrame.kmPerRenderUnit,
            ),
            SEGMENTS,
          )
        : null,
    // altKm intentionally excluded; altKmQuant is the >1km-change gate.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [sat?.node_id, altKmQuant, bodyFrame.radiusKm, bodyFrame.kmPerRenderUnit],
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
    [],
  );

  // Dispose the swapped-out geometry; React swaps the <mesh geometry> attribute but does not
  // free the previous BufferGeometry, so free it explicitly when a new one supersedes it.
  useEffect(() => () => geometry?.dispose(), [geometry]);
  useEffect(() => () => material.dispose(), [material]);

  // Default priority (0): runs after FrameDriver (-2) and Constellation (-1) so the satellite's
  // body-local position read here is this frame's. Refresh u_falloff cheaply each frame; place
  // and orient the disc at the sub-satellite surface point.
  useFrame(() => {
    const mesh = meshRef.current;
    if (!mesh) return;
    if (!sat) {
      mesh.visible = false;
      return;
    }
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

  if (!sat || !geometry) return null;

  return <mesh ref={meshRef} geometry={geometry} material={material} renderOrder={1} visible={false} />;
}
