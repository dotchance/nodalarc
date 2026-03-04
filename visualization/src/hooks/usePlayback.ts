import { useState, useCallback } from "react";
import { REST_URL } from "../config";

interface PlaybackState {
  paused: boolean;
  speed: number;
  loading: boolean;
  pause: () => Promise<void>;
  resume: () => Promise<void>;
  setSpeed: (factor: number) => Promise<void>;
}

export function usePlayback(): PlaybackState {
  const [paused, setPaused] = useState(false);
  const [speed, setSpeedState] = useState(1.0);
  const [loading, setLoading] = useState(false);

  const sendCommand = useCallback(async (body: Record<string, unknown>) => {
    setLoading(true);
    try {
      const res = await fetch(`${REST_URL}/api/v1/playback`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
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
  };
}
