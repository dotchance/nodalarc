// Copyright 2024-2026 .chance (dotchance)
// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
/**
 * Earth visuals: three globe modes
 * (blue-marble | day-night | political) selected by `globeMode`:
 *   blue-marble: Phong-lit textured sphere (sun directional + ambient), boundaries shown.
 *   day-night:   custom terminator shader (city lights on the night side),
 *                boundaries shown.
 *   political:   matte depth-writing schematic globe with boundaries on top —
 *                no photoreal atmosphere; the surface must occlude far-side
 *                geometry or links/satellites read as visible through the planet.
 * Atmosphere (backside rim glow) shows in the photoreal modes only; the
 * starfield is inertial (scene root).
 *
 * The sun directional light lives in the Earth body frame and is positioned from sim_time by
 * the approximate UTC declination/hour-angle model (NOT gmst — that drives the frame
 * rotation); its post-rotation world direction feeds the day/night shader each frame.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { useFrame, useLoader, useThree } from "@react-three/fiber";
import type { GlobeMode, ReferenceFrame } from "../../types";
import { skyAt } from "../../sim/bodySamples";
import { interpolatedSimTimeMs } from "../../sim/simClock";
import { sunDirection, sunRelativeToOrigin, toRenderVector } from "./bodyPose";
import { tokens } from "../../styles/tokens";
import { useBodyFrame } from "./BodyFrame";
import { EARTH_RADIUS_RENDER } from "./units";

const ATMO_VERT = `
varying vec3 vNormal;
varying vec3 vViewDir;
void main() {
  vNormal = normalize(normalMatrix * normal);
  vec4 mvPos = modelViewMatrix * vec4(position, 1.0);
  vViewDir = normalize(-mvPos.xyz);
  gl_Position = projectionMatrix * mvPos;
}
`;
const ATMO_FRAG = `
varying vec3 vNormal;
varying vec3 vViewDir;
void main() {
  float rim = 1.0 - max(0.0, dot(vNormal, vViewDir));
  float intensity = pow(rim, 3.0) * 0.6;
  gl_FragColor = vec4(0.4, 0.65, 1.0, intensity);
}
`;

// Day/night terminator shader (globe/earth.ts createDayNightEarth) — verbatim.
const DAYNIGHT_VERT = `
varying vec2 vUv;
varying vec3 vWorldNormal;
void main() {
  vUv = uv;
  vWorldNormal = normalize((modelMatrix * vec4(normal, 0.0)).xyz);
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}
`;
// Lighting constants interpolate from tokens at load — tune in tokens.ts.
const DAYNIGHT_FRAG = `
uniform sampler2D u_dayMap;
uniform sampler2D u_nightMap;
uniform vec3 u_sunDirection;
varying vec2 vUv;
varying vec3 vWorldNormal;
void main() {
  vec3 N = normalize(vWorldNormal);
  float NdotL = dot(N, u_sunDirection);
  float blend = smoothstep(${(-tokens.earthTwilightHalfWidth).toFixed(3)}, ${tokens.earthTwilightHalfWidth.toFixed(3)}, NdotL);
  vec3 dayColor = texture2D(u_dayMap, vUv).rgb;
  vec3 nightColor = texture2D(u_nightMap, vUv).rgb;
  float dayShading = ${tokens.earthDayFloor.toFixed(3)} + ${(1 - tokens.earthDayFloor).toFixed(3)} * max(0.0, NdotL);
  dayColor *= dayShading;
  nightColor *= ${tokens.earthNightLightsLevel.toFixed(3)};
  vec3 color = mix(nightColor, dayColor, blend);
  gl_FragColor = vec4(color, 1.0);
}
`;

function Atmosphere({ radiusRender, visible }: { radiusRender: number; visible: boolean }) {
  return (
    <mesh visible={visible}>
      <sphereGeometry args={[radiusRender * 1.015, 32, 32]} />
      <shaderMaterial
        vertexShader={ATMO_VERT}
        fragmentShader={ATMO_FRAG}
        side={THREE.BackSide}
        blending={THREE.AdditiveBlending}
        transparent
        depthWrite={false}
      />
    </mesh>
  );
}

/** Shaders that light themselves from the Sun's world direction (Earth's day/night). */
const sunlitShaders = new Set<THREE.ShaderMaterial>();
/** The Sun's direction on world render axes this frame; null when no samples cover it. */
const sunWorldDirection = new THREE.Vector3();
let sunDirectionKnown = false;

/** The Sun: a point light at the Sun's position, the sun disc on the star shell, and the
 *  direction Earth's day/night shader reads. Positions come from OME's body samples at the
 *  interpolated sim time, on the world axes of the reference frame, relative to the render
 *  origin. A point light lights each body from the Sun as seen from that body; a directional
 *  light along the Sun's direction from Earth (the rejected alternative) lit Mars from a
 *  direction up to tens of degrees wrong. The light has no distance falloff (decay 0), so its
 *  intensity is the directional light's. It lives at the scene root, so every body is lit,
 *  with or without Earth in the session. Runs after FrameDriver (-2) and before the
 *  default-priority consumers. */
export function SunLight({
  referenceFrame,
  kmPerRenderUnit,
  originBodyId,
}: {
  referenceFrame: ReferenceFrame;
  kmPerRenderUnit: number | null;
  originBodyId: string | null;
}) {
  const lightRef = useRef<THREE.PointLight>(null);
  const spriteRef = useRef<THREE.Sprite>(null);
  const camera = useThree((s) => s.camera);
  const cameraWorld = useMemo(() => new THREE.Vector3(), []);
  const sunWorld = useMemo(() => new THREE.Vector3(), []);
  const towardSun = useMemo(() => new THREE.Vector3(), []);
  const sunTexture = useMemo(makeSunTexture, []);
  useEffect(() => () => sunTexture.dispose(), [sunTexture]);

  useFrame(() => {
    const light = lightRef.current;
    const sprite = spriteRef.current;
    const interpMs = interpolatedSimTimeMs(performance.now());
    const sky = interpMs === null ? null : skyAt(interpMs / 1000);
    const direction = sky ? sunDirection(sky, referenceFrame) : null;
    const sunKm =
      sky && originBodyId !== null ? sunRelativeToOrigin(sky, referenceFrame, originBodyId) : null;
    sunDirectionKnown = direction !== null && sunKm !== null && kmPerRenderUnit !== null;
    if (light) light.visible = sunDirectionKnown;
    if (sprite) sprite.visible = sunDirectionKnown;
    if (!direction || !sunKm || kmPerRenderUnit === null) return;
    toRenderVector(direction, sunWorldDirection);
    for (const shader of sunlitShaders) {
      shader.uniforms.u_sunDirection!.value.copy(sunWorldDirection);
    }
    toRenderVector(sunKm, sunWorld, 1 / kmPerRenderUnit);
    if (light) light.position.copy(sunWorld);
    if (sprite) {
      camera.getWorldPosition(cameraWorld);
      towardSun.copy(sunWorld).sub(cameraWorld).normalize();
      const shell = starShellRadiusForCameraFar(camera.far) * 0.98;
      sprite.position.copy(cameraWorld).addScaledVector(towardSun, shell);
      const scale = shell * tokens.sceneSunDiscScale;
      sprite.scale.set(scale, scale, 1);
    }
  }, -1);

  return (
    <>
      <pointLight
        ref={lightRef}
        color={0xffffff}
        intensity={tokens.sceneSunIntensity}
        decay={0}
        distance={0}
        visible={false}
      />
      <sprite ref={spriteRef} frustumCulled={false} visible={false}>
        <spriteMaterial
          map={sunTexture}
          transparent
          depthWrite={false}
          blending={THREE.AdditiveBlending}
        />
      </sprite>
    </>
  );
}

/** Radial sun texture: hot core fading through the sun tint to transparent.
 *  Static, generated once — colors come from tokens at load. */
function makeSunTexture(): THREE.CanvasTexture {
  const size = 128;
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext("2d")!;
  const tint = "#" + tokens.colorSunTint.toString(16).padStart(6, "0");
  const g = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
  g.addColorStop(0, "#ffffff");
  g.addColorStop(0.18, tint);
  g.addColorStop(0.32, tint + "66");
  g.addColorStop(1, tint + "00");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, size, size);
  return new THREE.CanvasTexture(canvas);
}

/** Country boundaries: Natural Earth 110m as one LineSegments at
 *  R*1.001, drawn in every globe mode. Loaded once, async. */
function Boundaries({ radiusRender }: { radiusRender: number }) {
  const [geometry, setGeometry] = useState<THREE.BufferGeometry | null>(null);

  useEffect(() => {
    let alive = true;
    let ownedGeometry: THREE.BufferGeometry | null = null;
    const r = radiusRender * 1.001;
    const toXYZ = (lon: number, lat: number): [number, number, number] => {
      const latR = (lat * Math.PI) / 180;
      const lonR = (lon * Math.PI) / 180;
      return [
        r * Math.cos(latR) * Math.cos(lonR),
        r * Math.sin(latR),
        -r * Math.cos(latR) * Math.sin(lonR),
      ];
    };
    void (async () => {
      try {
        const resp = await fetch("/ne_110m_countries.geojson");
        if (!resp.ok) {
          console.error(`country boundaries failed to load: HTTP ${resp.status} — political mode will show no borders`);
          return;
        }
        const geojson = await resp.json();
        if (!alive) return;
        const verts: number[] = [];
        for (const feature of geojson.features ?? []) {
          const geom = feature.geometry;
          if (!geom) continue;
          const polys: number[][][] =
            geom.type === "Polygon"
              ? geom.coordinates
              : geom.type === "MultiPolygon"
                ? geom.coordinates.flat()
                : [];
          for (const ring of polys) {
            let prev: [number, number, number] | null = null;
            for (const coord of ring) {
              if (coord[0] === undefined || coord[1] === undefined) continue;
              const pt = toXYZ(coord[0], coord[1]);
              if (prev) verts.push(prev[0], prev[1], prev[2], pt[0], pt[1], pt[2]);
              prev = pt;
            }
          }
        }
        if (!verts.length) return;
        const g = new THREE.BufferGeometry();
        g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(verts), 3));
        ownedGeometry = g;
        setGeometry(g);
      } catch (err) {
        console.error("country boundaries failed to load — political mode will show no borders", err);
      }
    })();
    return () => {
      alive = false;
      ownedGeometry?.dispose();
    };
  }, []);

  return (
    <lineSegments geometry={geometry ?? undefined} visible={geometry !== null} renderOrder={2}>
      <lineBasicMaterial color={tokens.colorBoundaries} transparent opacity={0.55} depthWrite={false} />
    </lineSegments>
  );
}

/** The Earth body appearance — blue-marble + day/night meshes + atmosphere + sun + borders. */
export function Earth({ globeMode }: { globeMode: GlobeMode }) {
  const { radiusRender } = useBodyFrame();
  const textures = useLoader(THREE.TextureLoader, ["/earth-blue-marble.jpg", "/earth-night.jpg"]);
  const dayTexture = textures[0]!;
  const nightTexture = textures[1]!;
  useMemo(() => {
    dayTexture.colorSpace = THREE.SRGBColorSpace;
    nightTexture.colorSpace = THREE.SRGBColorSpace;
  }, [dayTexture, nightTexture]);

  const dayNightMaterial = useMemo(
    () =>
      new THREE.ShaderMaterial({
        uniforms: {
          u_dayMap: { value: dayTexture },
          u_nightMap: { value: nightTexture },
          u_sunDirection: { value: new THREE.Vector3(1, 0, 0) },
        },
        vertexShader: DAYNIGHT_VERT,
        fragmentShader: DAYNIGHT_FRAG,
      }),
    [dayTexture, nightTexture],
  );
  useEffect(() => () => dayNightMaterial.dispose(), [dayNightMaterial]);
  useEffect(() => {
    sunlitShaders.add(dayNightMaterial);
    if (sunDirectionKnown) dayNightMaterial.uniforms.u_sunDirection!.value.copy(sunWorldDirection);
    return () => {
      sunlitShaders.delete(dayNightMaterial);
    };
  }, [dayNightMaterial]);

  const showBlueMarble = globeMode === "blue-marble";
  const showDayNight = globeMode === "day-night";
  const showPolitical = globeMode === "political";

  return (
    <>
      <mesh visible={showBlueMarble}>
        <sphereGeometry args={[radiusRender, 64, 64]} />
        <meshPhongMaterial map={dayTexture} shininess={5} />
      </mesh>
      <mesh visible={showDayNight} material={dayNightMaterial}>
        <sphereGeometry args={[radiusRender, 64, 64]} />
      </mesh>
      {/* Political mode is the schematic view: a matte, depth-writing globe so
          far-side geometry is truthfully occluded (without a surface, links and
          satellites behind the planet would read as visible through it), with
          borders on top and no photoreal atmosphere — the unconditional
          additive atmosphere shell was what washed this mode out before. */}
      <mesh visible={showPolitical}>
        <sphereGeometry args={[radiusRender, 64, 64]} />
        <meshBasicMaterial color={tokens.colorBodyPolitical} />
      </mesh>
      <Atmosphere radiusRender={radiusRender} visible={!showPolitical} />
      <Boundaries radiusRender={radiusRender} />
    </>
  );
}

export function Moon() {
  const { radiusRender } = useBodyFrame();
  const moonTexture = useLoader(THREE.TextureLoader, "/moon-lroc-color.jpg");
  useMemo(() => {
    moonTexture.colorSpace = THREE.SRGBColorSpace;
    moonTexture.anisotropy = 4;
  }, [moonTexture]);
  return (
    <mesh>
      <sphereGeometry args={[radiusRender, 96, 96]} />
      <meshStandardMaterial map={moonTexture} roughness={1.0} metalness={0.0} />
    </mesh>
  );
}

/** Mars: the Viking colour mosaic, with MOLA heights as a bump map so terrain shows relief
 *  under the Sun's light. Both maps share the mosaic's layout (180 W at the left edge), the
 *  layout the Earth and Moon maps use. The relief is shading only and its scale is a display
 *  choice: the sphere keeps the body's radius and no position moves. */
export function Mars() {
  const { radiusRender } = useBodyFrame();
  const colorTexture = useLoader(THREE.TextureLoader, "/mars-viking-color.jpg");
  const heightTexture = useLoader(THREE.TextureLoader, "/mars-mola-height.png");
  useMemo(() => {
    colorTexture.colorSpace = THREE.SRGBColorSpace;
    colorTexture.anisotropy = 4;
    heightTexture.anisotropy = 4;
  }, [colorTexture, heightTexture]);
  return (
    <mesh>
      <sphereGeometry args={[radiusRender, 128, 128]} />
      <meshStandardMaterial
        map={colorTexture}
        bumpMap={heightTexture}
        bumpScale={4}
        roughness={1.0}
        metalness={0.0}
      />
    </mesh>
  );
}

export function starShellRadiusForCameraFar(cameraFar: number): number {
  if (!Number.isFinite(cameraFar) || cameraFar <= 0) {
    throw new Error(`invalid camera far plane for starfield: ${cameraFar}`);
  }
  const desired = Math.max(EARTH_RADIUS_RENDER * 80, cameraFar * 0.45);
  return Math.min(desired, cameraFar * 0.9);
}

function seededRandom(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (1664525 * state + 1013904223) >>> 0;
    return state / 0x100000000;
  };
}

/**
 * Camera-centered inertial star background.
 *
 * Stars are not scene-local geometry: at cislunar scale a finite shell around Earth becomes
 * visible and can sit between Earth and Luna. The points stay centered on the active camera in
 * the star frame, so the sky behaves like a distant background while the frame driver still owns
 * Earth-fixed vs inertial sky rotation.
 */
export function Starfield() {
  const pointsRef = useRef<THREE.Points>(null);
  const camera = useThree((s) => s.camera);
  const cameraWorld = useMemo(() => new THREE.Vector3(), []);
  const cameraLocal = useMemo(() => new THREE.Vector3(), []);
  const geometry = useMemo(() => {
    const g = new THREE.BufferGeometry();
    const count = 3000;
    const random = seededRandom(0x5eed1234);
    const pos = new Float32Array(count * 3);
    for (let i = 0; i < count; i++) {
      const theta = random() * Math.PI * 2;
      const phi = Math.acos(2 * random() - 1);
      pos[i * 3] = Math.sin(phi) * Math.cos(theta);
      pos[i * 3 + 1] = Math.sin(phi) * Math.sin(theta);
      pos[i * 3 + 2] = Math.cos(phi);
    }
    g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    return g;
  }, []);
  useEffect(() => () => geometry.dispose(), [geometry]);
  useFrame(() => {
    const points = pointsRef.current;
    if (!points) return;
    const parent = points.parent;
    camera.getWorldPosition(cameraWorld);
    if (parent) {
      parent.worldToLocal(cameraLocal.copy(cameraWorld));
      points.position.copy(cameraLocal);
    } else {
      points.position.copy(cameraWorld);
    }
    points.scale.setScalar(starShellRadiusForCameraFar(camera.far));
  });
  return (
    <points ref={pointsRef} geometry={geometry} frustumCulled={false}>
      <pointsMaterial
        color={0xffffff}
        size={1.0}
        sizeAttenuation={false}
        transparent
        opacity={0.6}
        depthWrite={false}
      />
    </points>
  );
}
