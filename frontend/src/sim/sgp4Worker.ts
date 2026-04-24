// Copyright 2024-2026 .chance (dotchance)
// Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
// SGP4 Web Worker — orbital propagation off the main thread.
// Writes satellite positions into a double-buffered SharedArrayBuffer.
// Main thread reads the active buffer with zero contention.

import { propagateToSceneXYZ, type KeplerianElements } from "./orbitalMath";

// --- Message types ---

interface InitMsg {
  type: "init";
  sab: SharedArrayBuffer;
  maxSatellites: number;
  samplesPerWindow: number;
}

interface EphemerisMsg {
  type: "ephemeris";
  epochUnix: number;
  satellites: { id: string; elements: KeplerianElements }[];
}

interface PropagateMsg {
  type: "propagate";
  simTimeUnix: number;
  playbackSpeed: number;
}

interface FlushMsg {
  type: "flush";
  simTimeUnix: number;
  playbackSpeed: number;
}

type WorkerMsg = InitMsg | EphemerisMsg | PropagateMsg | FlushMsg;

// --- SAB layout constants ---
// Control block: 32 bytes (Int32Array view)
//   [0] activeBufferIndex (0 or 1)
//   [1] satelliteCount
//   [2] samplesPerWindow
//   [3] reserved
// Per-buffer header: 16 bytes (Float64Array view)
//   [0] windowStartSimTime (unix seconds)
//   [1] sampleInterval (sim-seconds)
// Per-buffer data: satelliteCount * samplesPerWindow * 12 bytes

const CONTROL_BYTES = 32;
const BUFFER_HEADER_BYTES = 16;

// --- Worker state ---

let control: Int32Array;
let bufferA: { header: Float64Array; positions: Float32Array };
let bufferB: { header: Float64Array; positions: Float32Array };
let maxSats = 0;
let samplesPerWindow = 50;

let epochUnix = 0;
let satellites: { id: string; elements: KeplerianElements }[] = [];

function initBuffers(sab: SharedArrayBuffer, maxSatellites: number, samples: number): void {
  maxSats = maxSatellites;
  samplesPerWindow = samples;

  control = new Int32Array(sab, 0, CONTROL_BYTES / 4);

  const perBufferDataBytes = maxSats * samples * 3 * 4; // 3 floats * 4 bytes
  const perBufferTotalBytes = BUFFER_HEADER_BYTES + perBufferDataBytes;

  const aOffset = CONTROL_BYTES;
  const bOffset = CONTROL_BYTES + perBufferTotalBytes;

  bufferA = {
    header: new Float64Array(sab, aOffset, 2),
    positions: new Float32Array(sab, aOffset + BUFFER_HEADER_BYTES, maxSats * samples * 3),
  };
  bufferB = {
    header: new Float64Array(sab, bOffset, 2),
    positions: new Float32Array(sab, bOffset + BUFFER_HEADER_BYTES, maxSats * samples * 3),
  };

  Atomics.store(control, 0, 0);
  Atomics.store(control, 1, 0);
  Atomics.store(control, 2, samples);
}

function getInactiveBuffer(): typeof bufferA {
  const active = Atomics.load(control, 0);
  return active === 0 ? bufferB : bufferA;
}

function flipActiveBuffer(): void {
  const active = Atomics.load(control, 0);
  Atomics.store(control, 0, active === 0 ? 1 : 0);
}

function computeWindow(startSimTime: number, sampleInterval: number): void {
  const buf = getInactiveBuffer();
  buf.header[0] = startSimTime;
  buf.header[1] = sampleInterval;

  for (let si = 0; si < satellites.length && si < maxSats; si++) {
    const sat = satellites[si]!;
    for (let j = 0; j < samplesPerWindow; j++) {
      const t = startSimTime + j * sampleInterval;
      const [x, y, z] = propagateToSceneXYZ(sat.elements, epochUnix, t);
      const offset = (si * samplesPerWindow + j) * 3;
      buf.positions[offset] = x;
      buf.positions[offset + 1] = y;
      buf.positions[offset + 2] = z;
    }
  }

  Atomics.store(control, 1, Math.min(satellites.length, maxSats));
  flipActiveBuffer();

  self.postMessage({ type: "windowReady", startSimTime, sampleInterval });
}

// --- Message handler ---

self.onmessage = (e: MessageEvent<WorkerMsg>) => {
  const msg = e.data;

  switch (msg.type) {
    case "init":
      initBuffers(msg.sab, msg.maxSatellites, msg.samplesPerWindow);
      break;

    case "ephemeris":
      epochUnix = msg.epochUnix;
      satellites = msg.satellites;
      self.postMessage({ type: "ephemerisLoaded", count: satellites.length });
      break;

    case "propagate": {
      if (satellites.length === 0) break;
      const sampleInterval = Math.max(0.1, 0.1 * Math.max(1, msg.playbackSpeed));
      const startTime = msg.simTimeUnix;
      computeWindow(startTime, sampleInterval);
      break;
    }

    case "flush": {
      if (satellites.length === 0) break;
      const sampleInterval = Math.max(0.1, 0.1 * Math.max(1, msg.playbackSpeed));
      computeWindow(msg.simTimeUnix, sampleInterval);
      break;
    }
  }
};
