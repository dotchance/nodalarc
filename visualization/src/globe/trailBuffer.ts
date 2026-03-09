/** Pure ring buffer for orbital trail geometry.
 *
 *  Extracted from orbitalTrails.ts so the draw-order and arc-length
 *  logic can be tested without Three.js.
 */

export interface TrailSample {
  x: number;
  y: number;
  z: number;
}

export interface TrailBufferState {
  /** Ring buffer of recorded positions (x,y,z triples). */
  buf: Float32Array;
  /** Next write index (0..capacity-1). */
  head: number;
  /** Number of valid samples currently stored. */
  count: number;
  /** Maximum number of samples the buffer can hold. */
  capacity: number;
}

export function createTrailBuffer(capacity: number): TrailBufferState {
  return {
    buf: new Float32Array(capacity * 3),
    head: 0,
    count: 0,
    capacity,
  };
}

export function pushSample(
  state: TrailBufferState,
  x: number,
  y: number,
  z: number,
): void {
  const i3 = state.head * 3;
  state.buf[i3] = x;
  state.buf[i3 + 1] = y;
  state.buf[i3 + 2] = z;
  state.head = (state.head + 1) % state.capacity;
  if (state.count < state.capacity) state.count++;
}

/**
 * Extract draw-order points from the ring buffer.
 * Returns points ordered oldest (index 0) to newest (index N-1).
 * Stops when cumulative arc length exceeds maxArcLength.
 */
export function extractDrawPoints(
  state: TrailBufferState,
  maxArcLength: number,
): TrailSample[] {
  if (state.count === 0) return [];

  const cap = state.capacity;

  // Newest point index in ring buffer
  const newestRing = (state.head - 1 + cap) % cap;

  // Walk backward from newest, accumulating arc length
  let drawCount = 1;
  let arcLen = 0;
  let prevX = state.buf[newestRing * 3]!;
  let prevY = state.buf[newestRing * 3 + 1]!;
  let prevZ = state.buf[newestRing * 3 + 2]!;

  for (let k = 1; k < state.count; k++) {
    const ringIdx = (newestRing - k + cap) % cap;
    const rx = state.buf[ringIdx * 3]!;
    const ry = state.buf[ringIdx * 3 + 1]!;
    const rz = state.buf[ringIdx * 3 + 2]!;
    const dx = rx - prevX;
    const dy = ry - prevY;
    const dz = rz - prevZ;
    arcLen += Math.sqrt(dx * dx + dy * dy + dz * dz);
    if (arcLen > maxArcLength) break;
    drawCount++;
    prevX = rx;
    prevY = ry;
    prevZ = rz;
  }

  // Build result in draw order: oldest (index 0) to newest (index N-1)
  const result: TrailSample[] = new Array(drawCount);
  for (let j = 0; j < drawCount; j++) {
    const k = drawCount - 1 - j; // offset from newest
    const ringIdx = (newestRing - k + cap) % cap;
    const s3 = ringIdx * 3;
    result[j] = {
      x: state.buf[s3]!,
      y: state.buf[s3 + 1]!,
      z: state.buf[s3 + 2]!,
    };
  }

  return result;
}
