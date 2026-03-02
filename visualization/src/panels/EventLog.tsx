/** Event log — scrolling list of recent events with auto-scroll. */

import { useRef, useEffect, useState } from "react";
import { EventFilter } from "./EventFilter";
import { formatTime } from "../translate";
import type { RecentEvent, Selection } from "../types";

interface EventLogProps {
  events: RecentEvent[];
  onSelect: (sel: Selection | null) => void;
  onFlyTo?: (nodeId: string) => void;
}

const TYPE_ABBREV: Record<string, string> = {
  link_up: "LINK UP",
  link_down: "LINK DN",
  adjacency_up: "ADJ UP",
  adjacency_down: "ADJ DOWN",
  spf_start: "SPF",
  spf_end: "SPF END",
  convergence: "CONV",
  scenario_inject: "INJECT",
  scenario_reconciliation: "RECON",
  adapter: "ADAPT",
};

function abbreviateType(eventType: string): string {
  return TYPE_ABBREV[eventType] ?? eventType.toUpperCase().replace(/_/g, " ");
}

/** Abbreviate node ID: P03S07 for satellites, station name for GS */
function abbreviateNodeId(nodeId: string): string {
  if (nodeId.startsWith("gs-")) return nodeId.replace("gs-", "");
  const match = nodeId.match(/P(\d+)S(\d+)/);
  if (match) return `P${match[1]}S${match[2]}`;
  return nodeId;
}

/** Color class for event type */
function eventColorClass(eventType: string): string {
  if (eventType.includes("up") || eventType === "link_up" || eventType === "adjacency_up") {
    return "event-type--up";
  }
  if (eventType.includes("down") || eventType === "link_down" || eventType === "adjacency_down") {
    return "event-type--down";
  }
  if (eventType.includes("spf") || eventType === "convergence") {
    return "event-type--computation";
  }
  return "event-type--info";
}

const DEFAULT_FILTERS: Record<string, boolean> = {
  link_up: true,
  link_down: true,
  adjacency: true,
  spf: false,
  convergence: true,
  inject: true,
};

export function EventLog({ events, onSelect, onFlyTo }: EventLogProps) {
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
      onFlyTo?.(event.node_id);
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
            <span className={`event-type ${eventColorClass(e.event_type)}`}>
              {abbreviateType(e.event_type)}
            </span>
            <span className="event-node">{abbreviateNodeId(e.node_id)}</span>
            <span className="event-summary">{e.summary}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
