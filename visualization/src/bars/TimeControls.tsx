/** Time controls for historical playback mode. */

import { useState, useRef, useEffect, useCallback } from "react";

interface TimeControlsProps {
  onSeek: (simTime: string) => void;
  startTime: string;
  endTime: string;
}

const SPEEDS = [0.25, 0.5, 1, 2, 5, 10];

export function TimeControls({ onSeek, startTime, endTime }: TimeControlsProps) {
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [progress, setProgress] = useState(0); // 0-1
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

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

  const handleScrubberClick = (e: React.MouseEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const p = (e.clientX - rect.left) / rect.width;
    seekTo(p);
  };

  return (
    <div className="time-controls area-time-controls">
      <button className="time-btn" onClick={() => seekTo(progress - 300000 / duration)} title="-5min">
        ⏮
      </button>
      <button
        className={`time-btn ${playing ? "time-btn--active" : ""}`}
        onClick={() => setPlaying(!playing)}
        title={playing ? "Pause" : "Play"}
      >
        {playing ? "⏸" : "▶"}
      </button>
      <button className="time-btn" onClick={() => seekTo(progress + 300000 / duration)} title="+5min">
        ⏭
      </button>
      <div className="time-scrubber" onClick={handleScrubberClick}>
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
