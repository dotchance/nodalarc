/** Full-screen session catalog overlay. */

import { useState, useCallback, useMemo } from "react";
import type { Manifest } from "./catalogTypes";
import type { SessionInfo } from "../types";
import { CatalogFilters } from "./CatalogFilters";
import { SessionCard } from "./SessionCard";

interface SessionCatalogProps {
  manifest: Manifest | null;
  activeSessionId: string | null;
  onDeploy: (id: string) => void;
  onClose: (() => void) | undefined;
  deploying: boolean;
  fallbackSessions: SessionInfo[];
}

export function SessionCatalog({ manifest, activeSessionId, onDeploy, onClose, deploying, fallbackSessions }: SessionCatalogProps) {
  const [search, setSearch] = useState("");
  const [activeTags, setActiveTags] = useState<Set<string>>(new Set());

  const toggleTag = useCallback((tag: string) => {
    setActiveTags((prev) => {
      const next = new Set(prev);
      if (next.has(tag)) next.delete(tag);
      else next.add(tag);
      return next;
    });
  }, []);

  const clearFilters = useCallback(() => {
    setSearch("");
    setActiveTags(new Set());
  }, []);

  const sessions = manifest?.sessions ?? [];
  const scenarios = manifest?.scenarios ?? [];

  const filteredSessions = useMemo(() => {
    const q = search.toLowerCase();
    return sessions.filter((s) => {
      if (q && !s.name.toLowerCase().includes(q) && !s.description.toLowerCase().includes(q) && !s.constellation.toLowerCase().includes(q)) {
        return false;
      }
      for (const tag of activeTags) {
        if (!s.tags.includes(tag)) return false;
      }
      return true;
    });
  }, [sessions, search, activeTags]);

  // Fallback: no manifest, show basic session list from VS-API
  if (!manifest) {
    return (
      <div className="catalog-overlay">
        <h1 className="catalog-header">NODAL ARC</h1>
        <p className="catalog-subtitle">Orbital Network Emulation Lab</p>
        {fallbackSessions.length > 0 ? (
          <>
            <p className="catalog-fallback-warning">
              Could not load session catalog. Showing sessions from VS-API.
            </p>
            <div className="catalog-grid">
              {fallbackSessions.map((s) => (
                <div key={s.file} className={`catalog-card ${s.active ? "catalog-card--active" : ""}`}>
                  <div className="catalog-card-header">
                    <h3>{s.name}</h3>
                  </div>
                  <div className="catalog-card-stats">
                    <span>{s.constellation}</span>
                    <span>{s.routing_stack}</span>
                  </div>
                  <button
                    className={`catalog-deploy-btn ${s.active ? "catalog-deploy-btn--running" : deploying ? "catalog-deploy-btn--deploying" : ""}`}
                    onClick={() => onDeploy(s.file.replace("configs/sessions/", "").replace(".yaml", ""))}
                    disabled={s.active || deploying}
                  >
                    {s.active ? "Running" : deploying ? "Deploying..." : "Deploy"}
                  </button>
                </div>
              ))}
            </div>
          </>
        ) : (
          <p className="catalog-fallback-warning">
            Loading catalog...
          </p>
        )}
        {onClose && (
          <div className="catalog-footer">
            <span>Press Esc to close</span>
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="catalog-overlay">
      <h1 className="catalog-header">NODAL ARC</h1>
      <p className="catalog-subtitle">Orbital Network Emulation Lab</p>

      <CatalogFilters
        sessions={sessions}
        search={search}
        onSearchChange={setSearch}
        activeTags={activeTags}
        onToggleTag={toggleTag}
        onClear={clearFilters}
      />

      <div className="catalog-grid">
        {filteredSessions.length === 0 ? (
          <div className="catalog-empty">No sessions match your filters.</div>
        ) : (
          filteredSessions.map((s) => (
            <SessionCard
              key={s.id}
              session={s}
              scenarios={scenarios}
              isActive={s.id === activeSessionId}
              deploying={deploying}
              onDeploy={onDeploy}
            />
          ))
        )}
      </div>

      <div className="catalog-footer">
        <span>{sessions.length} sessions &middot; {scenarios.length} scenarios</span>
        {onClose && <span>Press Esc to close</span>}
      </div>
    </div>
  );
}
