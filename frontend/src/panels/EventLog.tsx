// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Event log — scrolling list with sortable, draggable columns. */

import React, { useRef, useEffect, useState, useMemo, useCallback } from "react";
import { EventFilter } from "./EventFilter";
import { formatTimeShort } from "../translate";
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

type ColKey = "time" | "node" | "type" | "summary";
type SortDir = "asc" | "desc";

interface ColDef {
  key: ColKey;
  label: string;
  cssClass: string;
}

const COLUMN_DEFS: Record<ColKey, ColDef> = {
  time: { key: "time", label: "Time", cssClass: "event-col-time" },
  node: { key: "node", label: "Node", cssClass: "event-col-node" },
  type: { key: "type", label: "Type", cssClass: "event-col-type" },
  summary: { key: "summary", label: "Detail", cssClass: "event-col-summary" },
};

const DEFAULT_COL_ORDER: ColKey[] = ["time", "node", "type", "summary"];

function renderCell(
  e: RecentEvent,
  col: ColKey,
  nodesById: ReadonlyMap<string, NodeState>,
): React.ReactNode {
  switch (col) {
    case "time":
      return <span className="event-time">{formatTimeShort(e.sim_time)}</span>;
    case "type":
      return (
        <span className={`event-type ${eventColorClass(e.event_type)}`}>
          {abbreviateType(e.event_type)}
        </span>
      );
    case "node":
      return <span className="event-node">{abbreviateNodeId(e.node_id, nodesById.get(e.node_id))}</span>;
    case "summary":
      return <span className="event-summary">{e.summary}</span>;
  }
}

function sortValue(e: RecentEvent, col: ColKey): string {
  switch (col) {
    case "time": return e.sim_time;
    case "type": return abbreviateType(e.event_type);
    case "node": return e.node_id;
    case "summary": return e.summary;
  }
}

export function EventLog({ events, nodes, onSelect, onFlyTo }: EventLogProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [autoScroll, setAutoScroll] = useState(true);
  const [filters, setFilters] = useState(DEFAULT_FILTERS);
  const [sortKey, setSortKey] = useState<ColKey>("time");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [colOrder, setColOrder] = useState<ColKey[]>(DEFAULT_COL_ORDER);
  const dragColRef = useRef<ColKey | null>(null);
  const [dragOverCol, setDragOverCol] = useState<ColKey | null>(null);
  const nodesById = useMemo(() => new Map(nodes.map((node) => [node.node_id, node])), [nodes]);

  const handleColumnClick = useCallback((key: ColKey) => {
    if (sortKey === key) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir(key === "time" ? "desc" : "asc");
    }
  }, [sortKey]);

  const handleDragStart = useCallback((key: ColKey, e: React.DragEvent) => {
    dragColRef.current = key;
    e.dataTransfer.effectAllowed = "move";
  }, []);

  const handleDragOver = useCallback((key: ColKey, e: React.DragEvent) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    setDragOverCol(key);
  }, []);

  const handleDrop = useCallback((targetKey: ColKey) => {
    const srcKey = dragColRef.current;
    if (!srcKey || srcKey === targetKey) {
      dragColRef.current = null;
      setDragOverCol(null);
      return;
    }
    setColOrder((prev) => {
      const next = [...prev];
      const srcIdx = next.indexOf(srcKey);
      const tgtIdx = next.indexOf(targetKey);
      next.splice(srcIdx, 1);
      next.splice(tgtIdx, 0, srcKey);
      return next;
    });
    dragColRef.current = null;
    setDragOverCol(null);
  }, []);

  const handleDragEnd = useCallback(() => {
    dragColRef.current = null;
    setDragOverCol(null);
  }, []);

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
    const sorted = [...filteredEvents];
    const dir = sortDir === "asc" ? 1 : -1;
    sorted.sort((a, b) => dir * sortValue(a, sortKey).localeCompare(sortValue(b, sortKey)));
    return sorted;
  }, [filteredEvents, sortKey, sortDir]);

  useEffect(() => {
    if (autoScroll && scrollRef.current) {
      scrollRef.current.scrollTop = 0;
    }
  }, [events, autoScroll]);

  const handleScroll = () => {
    if (!scrollRef.current) return;
    setAutoScroll(scrollRef.current.scrollTop < 30);
  };

  const handleEventClick = (event: RecentEvent) => {
    if (event.node_id) {
      const node = nodesById.get(event.node_id);
      if (!node) return;
      const type = selectionTypeForNode(node);
      onSelect({ type, id: event.node_id });
      onFlyTo?.(event.node_id);
    }
  };

  const sortIndicator = (key: ColKey) => {
    if (sortKey !== key) return "";
    return sortDir === "asc" ? " \u25B4" : " \u25BE";
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
      <div className="event-log-header">
        {colOrder.map((key) => {
          const def = COLUMN_DEFS[key];
          return (
            <span
              key={key}
              className={`${def.cssClass}${dragOverCol === key ? " event-col--drag-over" : ""}`}
              draggable
              onClick={() => handleColumnClick(key)}
              onDragStart={(e) => handleDragStart(key, e)}
              onDragOver={(e) => handleDragOver(key, e)}
              onDrop={() => handleDrop(key)}
              onDragEnd={handleDragEnd}
            >
              {def.label}{sortIndicator(key)}
            </span>
          );
        })}
      </div>
      <div className="event-log" ref={scrollRef} onScroll={handleScroll}>
        {sortedEvents.map((e, i) => (
          <div className="event-entry" key={i} onClick={() => handleEventClick(e)}>
            {colOrder.map((col) => (
              <span key={col} className={COLUMN_DEFS[col].cssClass}>
                {renderCell(e, col, nodesById)}
              </span>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}
