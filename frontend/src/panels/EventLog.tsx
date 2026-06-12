// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Event log — network event feed in the inspector, on the shared table
 *  language (sort, drag-reorder, resize). Row click selects + flies to the
 *  node. */

import { useRef, useEffect, useState, useMemo } from "react";
import { EventFilter } from "./EventFilter";
import { formatTimeShort } from "../translate";
import { DataTable, type SortState, type TableColumn } from "../ui/DataTable";
import type { NodeState, RecentEvent, Selection } from "../types";
import { isGroundNode, selectionTypeForNode } from "../networkIdentity";

interface EventLogProps {
  events: RecentEvent[];
  nodes: NodeState[];
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

function abbreviateNodeId(nodeId: string, node?: NodeState): string {
  if (node && isGroundNode(node)) return node.local_node_id ?? node.node_id;
  const match = nodeId.match(/P(\d+)S(\d+)/);
  if (match) return `P${match[1]}S${match[2]}`;
  return nodeId;
}

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

const COLUMNS: TableColumn[] = [
  { key: "time", label: "Time", width: 70, minWidth: 50, sortable: true },
  { key: "node", label: "Node", width: 86, minWidth: 50, sortable: true },
  { key: "type", label: "Type", width: 64, minWidth: 44, sortable: true },
  { key: "summary", label: "Detail", sortable: true, mono: false },
];

function sortValue(e: RecentEvent, col: string): string {
  switch (col) {
    case "time": return e.sim_time;
    case "type": return abbreviateType(e.event_type);
    case "node": return e.node_id;
    default: return e.summary;
  }
}

export function EventLog({ events, nodes, onSelect, onFlyTo }: EventLogProps) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const [autoScroll, setAutoScroll] = useState(true);
  const [filters, setFilters] = useState(DEFAULT_FILTERS);
  const [columns, setColumns] = useState(COLUMNS);
  const [sort, setSort] = useState<SortState | null>({ key: "time", dir: "desc" });
  const nodesById = useMemo(() => new Map(nodes.map((node) => [node.node_id, node])), [nodes]);

  const filteredEvents = events.filter((e) => {
    if (e.event_type === "link_up" || e.event_type === "link_down") {
      return filters[e.event_type] ?? true;
    }
    if (e.event_type === "convergence") return filters["convergence"] ?? true;
    if (e.event_type.includes("adj")) return filters["adjacency"] ?? true;
    if (e.event_type.includes("spf")) return filters["spf"] ?? false;
    if (e.event_type.includes("inject")) return filters["inject"] ?? true;
    return true;
  });

  const sortedEvents = useMemo(() => {
    if (!sort) return filteredEvents;
    const dir = sort.dir === "asc" ? 1 : -1;
    return [...filteredEvents].sort(
      (a, b) => dir * sortValue(a, sort.key).localeCompare(sortValue(b, sort.key)),
    );
  }, [filteredEvents, sort]);

  useEffect(() => {
    if (autoScroll && scrollRef.current) {
      scrollRef.current.scrollTop = 0;
    }
  }, [events, autoScroll]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const onScroll = () => setAutoScroll(el.scrollTop < 30);
    el.addEventListener("scroll", onScroll);
    return () => el.removeEventListener("scroll", onScroll);
  }, []);

  const handleEventClick = (event: RecentEvent) => {
    if (!event.node_id) return;
    const node = nodesById.get(event.node_id);
    if (!node) return;
    onSelect({ type: selectionTypeForNode(node), id: event.node_id });
    onFlyTo?.(event.node_id);
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", flex: 1, minHeight: 0 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h3>Events</h3>
        {!autoScroll && (
          <button className="event-jump-latest" onClick={() => setAutoScroll(true)}>
            Jump to latest
          </button>
        )}
      </div>
      <EventFilter
        filters={filters}
        onToggle={(key) => setFilters((prev) => ({ ...prev, [key]: !prev[key] }))}
      />
      <DataTable
        label="Network events"
        columns={columns}
        onColumnsChange={setColumns}
        rows={sortedEvents}
        rowKey={(e) => `${e.sim_time}-${e.node_id}-${e.event_type}-${e.summary}`}
        sort={sort}
        onSortChange={setSort}
        scrollRef={scrollRef}
        onRowClick={handleEventClick}
        emptyText="No events"
        renderCell={(e, key) => {
          switch (key) {
            case "time":
              return formatTimeShort(e.sim_time);
            case "type":
              return <span className={`event-type ${eventColorClass(e.event_type)}`}>{abbreviateType(e.event_type)}</span>;
            case "node":
              return abbreviateNodeId(e.node_id, nodesById.get(e.node_id));
            default:
              return e.summary;
          }
        }}
      />
    </div>
  );
}
