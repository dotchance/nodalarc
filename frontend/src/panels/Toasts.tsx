// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
// Toast notification system — non-modal, auto-dismissing, stackable.
//
// Categories: info (handover), warning (MBB fallback), error (failure)
// Fed from the same WebSocket events as EventLog.

import { useState, useEffect, useCallback, useRef } from "react";
import { Icon } from "../ui/icons/Icon";

export interface Toast {
  id: number;
  category: "info" | "warning" | "error";
  message: string;
  timestamp: number;
}

import type { RecentEvent } from "../types";

interface ToastsProps {
  events: RecentEvent[] | undefined;
}

const TOAST_DURATION_MS = 5000;
let nextId = 0;

export function Toasts({ events }: ToastsProps) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const lastEventCountRef = useRef(0);

  useEffect(() => {
    if (!events || events.length === 0) return;
    if (events.length <= lastEventCountRef.current) {
      lastEventCountRef.current = events.length;
      return;
    }

    const newEvents = events.slice(lastEventCountRef.current);
    lastEventCountRef.current = events.length;

    const newToasts: Toast[] = [];
    for (const ev of newEvents) {
      let category: Toast["category"] = "info";
      if (ev.event_type === "link_down" || ev.event_type === "down" || ev.event_type === "error") {
        category = "error";
      } else if (ev.event_type === "computation" || ev.event_type === "convergence") {
        category = "warning";
      }

      newToasts.push({
        id: nextId++,
        category,
        message: ev.summary,
        timestamp: performance.now(),
      });
    }

    if (newToasts.length > 0) {
      setToasts((prev) => [...prev, ...newToasts].slice(-8));
    }
  }, [events]);

  useEffect(() => {
    if (toasts.length === 0) return;
    const timer = setInterval(() => {
      const now = performance.now();
      setToasts((prev) => prev.filter((t) => now - t.timestamp < TOAST_DURATION_MS));
    }, 500);
    return () => clearInterval(timer);
  }, [toasts.length > 0]);

  const dismiss = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  if (toasts.length === 0) return null;

  return (
    <div className="toast-container">
      {toasts.map((toast) => (
        <div
          key={toast.id}
          className={`toast toast--${toast.category}`}
          onClick={() => dismiss(toast.id)}
        >
          <span className="toast-icon">
            <Icon
              name={
                toast.category === "error"
                  ? "circle-x"
                  : toast.category === "warning"
                    ? "triangle-alert"
                    : "info"
              }
              size={14}
            />
          </span>
          <span className="toast-message">{toast.message}</span>
        </div>
      ))}
    </div>
  );
}
