// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
import { useState, useCallback, useEffect } from "react";
import { REST_URL, authHeaders } from "../config";

interface PlaybackState {
  paused: boolean;
  speed: number;
  loading: boolean;
  pause: () => Promise<void>;
  resume: () => Promise<void>;
  setSpeed: (factor: number) => Promise<void>;
  seekToNow: () => Promise<void>;
  seek: (targetSimTime: string) => Promise<void>;
}

/**
 * Playback hook.  Accepts snapshot-driven paused/speed so the UI always
 * reflects backend state (pushed via WebSocket), not just local guesses.
 */
export function usePlayback(snapshotPaused?: boolean, snapshotSpeed?: number): PlaybackState {
  const [paused, setPaused] = useState(false);
  const [speed, setSpeedState] = useState(1.0);
  const [loading, setLoading] = useState(false);

  // Sync from snapshot whenever it changes
  useEffect(() => {
    if (snapshotPaused !== undefined) setPaused(snapshotPaused);
  }, [snapshotPaused]);

  useEffect(() => {
    if (snapshotSpeed !== undefined && snapshotSpeed > 0) setSpeedState(snapshotSpeed);
  }, [snapshotSpeed]);

  const sendCommand = useCallback(async (body: Record<string, unknown>) => {
    setLoading(true);
    try {
      const res = await fetch(`${REST_URL}/api/v1/playback`, {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (data.paused !== undefined) setPaused(data.paused);
      if (data.speed !== undefined) setSpeedState(data.speed);
    } catch {
      // Non-fatal
    } finally {
      setLoading(false);
    }
  }, []);

  return {
    paused,
    speed,
    loading,
    pause: useCallback(() => sendCommand({ action: "pause" }), [sendCommand]),
    resume: useCallback(() => sendCommand({ action: "resume" }), [sendCommand]),
    setSpeed: useCallback((f: number) => sendCommand({ action: "set_speed", factor: f }), [sendCommand]),
    seekToNow: useCallback(() => sendCommand({ action: "seek" }), [sendCommand]),
    seek: useCallback((t: string) => sendCommand({ action: "seek", target_sim_time: t }), [sendCommand]),
  };
}
