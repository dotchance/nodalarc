// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
import { useState, useCallback, useEffect } from "react";
import { REST_URL, authHeaders } from "../config";
import type { PlaybackControlCommand, PlaybackState } from "../generated/vsApi";
import { apiErrorFromException, apiErrorMessage } from "../ui/apiError";

interface PlaybackControls {
  /** Null until a snapshot or a command answer states it. */
  paused: boolean | null;
  /** Null until a snapshot or a command answer states it. */
  speed: number | null;
  loading: boolean;
  /** Why the last command failed; null once one succeeds. */
  error: string | null;
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
export function usePlayback(snapshotPaused: boolean | null, snapshotSpeed: number | null): PlaybackControls {
  const [paused, setPaused] = useState<boolean | null>(null);
  const [speed, setSpeedState] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Sync from snapshot whenever it changes
  useEffect(() => {
    setPaused(snapshotPaused);
  }, [snapshotPaused]);

  useEffect(() => {
    setSpeedState(snapshotSpeed);
  }, [snapshotSpeed]);

  // True when the clock accepted the command.
  const sendCommand = useCallback(async (command: PlaybackControlCommand): Promise<boolean> => {
    setLoading(true);
    try {
      const res = await fetch(`${REST_URL}/api/v1/playback`, {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(command),
      });
      if (!res.ok) {
        setError(await apiErrorMessage(res));
        return false;
      }
      const answer = (await res.json()) as PlaybackState;
      setError(null);
      setPaused(answer.state === "paused");
      return true;
    } catch (err) {
      setError(apiErrorFromException(err));
      return false;
    } finally {
      setLoading(false);
    }
  }, []);

  return {
    paused,
    speed,
    loading,
    error,
    pause: useCallback(async () => {
      await sendCommand({ action: "pause" });
    }, [sendCommand]),
    resume: useCallback(async () => {
      await sendCommand({ action: "resume" });
    }, [sendCommand]),
    setSpeed: useCallback(async (factor: number) => {
      if (await sendCommand({ action: "set_speed", factor })) setSpeedState(factor);
    }, [sendCommand]),
    seekToNow: useCallback(async () => {
      await sendCommand({ action: "seek" });
    }, [sendCommand]),
    seek: useCallback(async (target_sim_time: string) => {
      await sendCommand({ action: "seek", target_sim_time });
    }, [sendCommand]),
  };
}
