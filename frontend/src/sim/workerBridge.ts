// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
// Main-thread interface to the SGP4 Web Worker.
// Allocates the double-buffered SharedArrayBuffer, sends ephemeris data
// to the Worker, and reads interpolated positions for the render loop.

import type { SessionEphemeris, EphemerisNodeKeplerian } from "./ephemeris";
import type { KeplerianElements } from "./orbitalMath";

// --- Configuration ---

const MAX_SATELLITES = 10_000;
const SAMPLES_PER_WINDOW = 50;
const CONTROL_BYTES = 32;
const BUFFER_HEADER_BYTES = 16;

// --- State ---

let worker: Worker | null = null;
let control: Int32Array | null = null;
let bufferA: { header: Float64Array; positions: Float32Array } | null = null;
let bufferB: { header: Float64Array; positions: Float32Array } | null = null;
let satIds: string[] = [];
let satIdToIndex = new Map<string, number>();
let ready = false;
let sabSupported = typeof SharedArrayBuffer !== "undefined";

// --- SAB allocation ---

function computeSabSize(): number {
  const perBufferDataBytes = MAX_SATELLITES * SAMPLES_PER_WINDOW * 3 * 4;
  const perBufferTotalBytes = BUFFER_HEADER_BYTES + perBufferDataBytes;
  return CONTROL_BYTES + perBufferTotalBytes * 2;
}

function createWorkerAndSab(): void {
  if (!sabSupported) return;

  const sabSize = computeSabSize();
  const sab = new SharedArrayBuffer(sabSize);

  control = new Int32Array(sab, 0, CONTROL_BYTES / 4);

  const perBufferDataBytes = MAX_SATELLITES * SAMPLES_PER_WINDOW * 3 * 4;
  const perBufferTotalBytes = BUFFER_HEADER_BYTES + perBufferDataBytes;

  const aOffset = CONTROL_BYTES;
  const bOffset = CONTROL_BYTES + perBufferTotalBytes;

  bufferA = {
    header: new Float64Array(sab, aOffset, 2),
    positions: new Float32Array(sab, aOffset + BUFFER_HEADER_BYTES, MAX_SATELLITES * SAMPLES_PER_WINDOW * 3),
  };
  bufferB = {
    header: new Float64Array(sab, bOffset, 2),
    positions: new Float32Array(sab, bOffset + BUFFER_HEADER_BYTES, MAX_SATELLITES * SAMPLES_PER_WINDOW * 3),
  };

  worker = new Worker(new URL("./sgp4Worker.ts", import.meta.url), { type: "module" });

  worker.onmessage = (e: MessageEvent) => {
    if (e.data.type === "windowReady") {
      ready = true;
    }
  };

  worker.postMessage({
    type: "init",
    sab,
    maxSatellites: MAX_SATELLITES,
    samplesPerWindow: SAMPLES_PER_WINDOW,
  });
}

// --- Public API ---

export function initWorkerBridge(): void {
  if (worker) return;
  createWorkerAndSab();
}

export function isWorkerAvailable(): boolean {
  return worker !== null && sabSupported;
}

export function isWorkerReady(): boolean {
  return ready;
}

export function sendEphemeris(ephemeris: SessionEphemeris): void {
  if (!worker) return;

  const sats: { id: string; elements: KeplerianElements }[] = [];
  for (const [id, node] of Object.entries(ephemeris.nodes)) {
    if (node.type !== "keplerian") continue;
    const kep = node as EphemerisNodeKeplerian;
    if ((kep.reference_body ?? "earth") !== "earth") continue;
    sats.push({
      id,
      elements: {
        altitude_km: kep.altitude_km,
        inclination_deg: kep.inclination_deg,
        raan_deg: kep.raan_deg,
        true_anomaly_deg: kep.true_anomaly_deg,
      },
    });
  }

  satIds = sats.map((s) => s.id);
  satIdToIndex = new Map(satIds.map((id, i) => [id, i]));
  ready = false;

  worker.postMessage({
    type: "ephemeris",
    epochUnix: ephemeris.epoch_unix,
    satellites: sats,
  });
}

export function requestPropagate(simTimeUnix: number, playbackSpeed: number): void {
  if (!worker) return;
  worker.postMessage({ type: "propagate", simTimeUnix, playbackSpeed });
}

export function requestFlush(simTimeUnix: number, playbackSpeed: number): void {
  if (!worker) return;
  ready = false;
  worker.postMessage({ type: "flush", simTimeUnix, playbackSpeed });
}

export function interpolateFromBuffer(
  header: Float64Array,
  positions: Float32Array,
  satIndex: number,
  simTimeUnix: number,
  samplesPerWindow: number,
  target: { x: number; y: number; z: number },
): boolean {
  const windowStart = header[0]!;
  const sampleInterval = header[1]!;

  if (sampleInterval <= 0) return false;

  const relativeTime = simTimeUnix - windowStart;
  const sampleF = relativeTime / sampleInterval;

  if (sampleF < 0 || sampleF >= samplesPerWindow - 1) return false;

  const sampleLow = Math.floor(sampleF);
  const frac = sampleF - sampleLow;

  const offsetLow = (satIndex * samplesPerWindow + sampleLow) * 3;
  const offsetHigh = offsetLow + 3;

  const x0 = positions[offsetLow]!;
  const y0 = positions[offsetLow + 1]!;
  const z0 = positions[offsetLow + 2]!;
  const x1 = positions[offsetHigh]!;
  const y1 = positions[offsetHigh + 1]!;
  const z1 = positions[offsetHigh + 2]!;

  target.x = x0 + (x1 - x0) * frac;
  target.y = y0 + (y1 - y0) * frac;
  target.z = z0 + (z1 - z0) * frac;

  return true;
}

export function readPosition(
  nodeId: string,
  simTimeUnix: number,
  target: { x: number; y: number; z: number },
): boolean {
  if (!control || !bufferA || !bufferB || !ready) return false;

  const satIndex = satIdToIndex.get(nodeId);
  if (satIndex === undefined) return false;

  const activeIdx = Atomics.load(control, 0);
  const buf = activeIdx === 0 ? bufferA : bufferB;

  return interpolateFromBuffer(
    buf.header, buf.positions, satIndex, simTimeUnix,
    SAMPLES_PER_WINDOW, target,
  );
}

export function getWorkerSatIds(): readonly string[] {
  return satIds;
}

export function destroyWorkerBridge(): void {
  if (worker) {
    worker.terminate();
    worker = null;
  }
  control = null;
  bufferA = null;
  bufferB = null;
  satIds = [];
  satIdToIndex.clear();
  ready = false;
}
