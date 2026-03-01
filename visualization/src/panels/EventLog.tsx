/** Event log — scrolling list of recent events with auto-scroll. */

import { useRef, useEffect, useState } from "react";
import { EventFilter } from "./EventFilter";
import { formatTime } from "../translate";
import type { RecentEvent, Selection } from "../types";

interface EventLogProps {
  events: RecentEvent[];
  onSelect: (sel: Selection | null) => void;
}

const DEFAULT_FILTERS: Record<string, boolean> = {
  link_up: true,
  link_down: true,
  adjacency: true,
  spf: false,
  convergence: true,
  inject: true,
};

export function EventLog({ events, onSelect }: EventLogProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [autoScroll, setAutoScroll] = useState(true);
  const [filters, setFilters] = useState(DEFAULT_FILTERS);

  useEffect(() => {
    if (autoScroll && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [events, autoScroll]);

  const handleScroll = () => {
    if (!scrollRef.current) return;
    const { scrollTop, scrollHeight, clientHeight } = scrollRef.current;
    setAutoScroll(scrollHeight - scrollTop - clientHeight < 30);
  };

  const filteredEvents = events.filter((e) => {
    // Map event types to filter categories
    if (e.event_type === "link_up" || e.event_type === "link_down") {
      return filters[e.event_type] ?? true;
    }
    if (e.event_type === "convergence") return filters["convergence"] ?? true;
    if (e.event_type.includes("adj")) return filters["adjacency"] ?? true;
    if (e.event_type.includes("spf")) return filters["spf"] ?? false;
    if (e.event_type.includes("inject")) return filters["inject"] ?? true;
    return true;
  });

  const handleEventClick = (event: RecentEvent) => {
    if (event.node_id) {
      const type = event.node_id.startsWith("gs-") ? "ground_station" : "satellite";
      onSelect({ type, id: event.node_id });
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", flex: 1, minHeight: 0 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h3>Events</h3>
        {!autoScroll && (
          <button
            onClick={() => setAutoScroll(true)}
            style={{ fontSize: 10, color: "var(--accent-blue)" }}
          >
            Jump to latest
          </button>
        )}
      </div>
      <EventFilter filters={filters} onToggle={(key) => {
        setFilters((prev) => ({ ...prev, [key]: !prev[key] }));
      }} />
      <div className="event-log" ref={scrollRef} onScroll={handleScroll}>
        {filteredEvents.map((e, i) => (
          <div
            className="event-entry"
            key={i}
            onClick={() => handleEventClick(e)}
          >
            <span className="event-time">{formatTime(e.sim_time)}</span>
            <span className={`event-type event-type--${e.event_type}`}>
              {e.event_type}
            </span>
            <span className="event-summary">{e.summary}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
