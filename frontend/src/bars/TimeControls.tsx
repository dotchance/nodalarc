// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Time controls for historical playback mode. */

import { useState, useRef, useEffect, useCallback } from "react";
import type { RecentEvent } from "../types";

interface TimeControlsProps {
  onSeek: (simTime: string) => void;
  startTime: string;
  endTime: string;
  events?: RecentEvent[];
  externalPlaying?: boolean;
  onPlayingChange?: (playing: boolean) => void;
}

const SPEEDS = [0.25, 0.5, 1, 2, 5, 10];

export function TimeControls({ onSeek, startTime, endTime, events, externalPlaying, onPlayingChange }: TimeControlsProps) {
  const [internalPlaying, setInternalPlaying] = useState(false);
  const playing = externalPlaying ?? internalPlaying;
  const setPlaying = useCallback((val: boolean | ((prev: boolean) => boolean)) => {
    const next = typeof val === "function" ? val(playing) : val;
    setInternalPlaying(next);
    onPlayingChange?.(next);
  }, [playing, onPlayingChange]);
  const [speed, setSpeed] = useState(1);
  const [progress, setProgress] = useState(0); // 0-1
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const heatmapCanvasRef = useRef<HTMLCanvasElement>(null);

  // Sync external playing state
  useEffect(() => {
    if (externalPlaying !== undefined && externalPlaying !== internalPlaying) {
      setInternalPlaying(externalPlaying);
    }
  }, [externalPlaying]);

  const start = new Date(startTime).getTime();
  const end = new Date(endTime).getTime();
  const duration = Math.max(end - start, 1);

  const seekTo = useCallback(
    (p: number) => {
      const clamped = Math.max(0, Math.min(1, p));
      setProgress(clamped);
      const time = new Date(start + clamped * duration).toISOString();
      onSeek(time);
    },
    [start, duration, onSeek],
  );

  useEffect(() => {
    if (playing) {
      intervalRef.current = setInterval(() => {
        setProgress((prev) => {
          const next = prev + (speed / (duration / 1000)) * 0.1;
          if (next >= 1) {
            setPlaying(false);
            return 1;
          }
          const time = new Date(start + next * duration).toISOString();
          onSeek(time);
          return next;
        });
      }, 100);
    } else {
      if (intervalRef.current) clearInterval(intervalRef.current);
    }
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [playing, speed, start, duration, onSeek]);

  // Draw event density heatmap behind the scrubber
  useEffect(() => {
    const canvas = heatmapCanvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const rect = canvas.parentElement?.getBoundingClientRect();
    if (!rect) return;

    canvas.width = rect.width;
    canvas.height = rect.height;
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    if (!events || events.length === 0) return;

    // Bin events into buckets across the timeline
    const bucketCount = Math.max(1, Math.floor(canvas.width / 4));
    const buckets = new Array<number>(bucketCount).fill(0);

    for (const event of events) {
      const t = new Date(event.sim_time).getTime();
      const p = (t - start) / duration;
      if (p >= 0 && p <= 1) {
        const bucket = Math.min(bucketCount - 1, Math.floor(p * bucketCount));
        buckets[bucket] = (buckets[bucket] ?? 0) + 1;
      }
    }

    const maxCount = Math.max(1, ...buckets);
    const barWidth = canvas.width / bucketCount;

    for (let i = 0; i < bucketCount; i++) {
      const intensity = buckets[i]! / maxCount;
      if (intensity <= 0) continue;
      const alpha = 0.1 + intensity * 0.4;
      ctx.fillStyle = `rgba(68, 136, 255, ${alpha})`;
      const barHeight = intensity * canvas.height;
      ctx.fillRect(i * barWidth, canvas.height - barHeight, barWidth, barHeight);
    }
  }, [events, start, duration]);

  const scrubberRef = useRef<HTMLDivElement>(null);
  const draggingRef = useRef(false);

  const scrubFromEvent = (clientX: number) => {
    const el = scrubberRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const p = (clientX - rect.left) / rect.width;
    seekTo(p);
  };

  const handleScrubberDown = (e: React.MouseEvent<HTMLDivElement>) => {
    draggingRef.current = true;
    scrubFromEvent(e.clientX);
    e.preventDefault();
  };

  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      if (draggingRef.current) scrubFromEvent(e.clientX);
    };
    const onUp = () => { draggingRef.current = false; };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, [seekTo]);

  const stepSpeed = (direction: number) => {
    const idx = SPEEDS.indexOf(speed);
    const next = idx + direction;
    if (next >= 0 && next < SPEEDS.length) setSpeed(SPEEDS[next]!);
  };

  return (
    <div className="time-controls area-time-controls">
      <button className="time-btn" onClick={() => seekTo(progress - 300000 / duration)} title="Skip back 5min">
        ⏮
      </button>
      <button className="time-btn" onClick={() => stepSpeed(-1)} title="Slower">
        ◀
      </button>
      <button
        className={`time-btn ${playing ? "time-btn--active" : ""}`}
        onClick={() => setPlaying(!playing)}
        title={playing ? "Pause" : "Play"}
      >
        {playing ? "⏸" : "▶"}
      </button>
      <button className="time-btn" onClick={() => stepSpeed(1)} title="Faster">
        ▶
      </button>
      <button className="time-btn" onClick={() => seekTo(progress + 300000 / duration)} title="Skip forward 5min">
        ⏭
      </button>
      <div className="time-scrubber" ref={scrubberRef} onMouseDown={handleScrubberDown}>
        <canvas ref={heatmapCanvasRef} className="time-scrubber-heatmap" />
        <div className="time-scrubber-track" />
        <div className="time-scrubber-fill" style={{ width: `${progress * 100}%` }} />
        <div className="time-scrubber-thumb" style={{ left: `${progress * 100}%` }} />
      </div>
      <select
        className="speed-select"
        value={speed}
        onChange={(e) => setSpeed(Number(e.target.value))}
      >
        {SPEEDS.map((s) => (
          <option key={s} value={s}>
            {s}x
          </option>
        ))}
      </select>
    </div>
  );
}
