/** Filter bar for session catalog — search + tag pills. */

import type { ManifestSession } from "./catalogTypes";

interface CatalogFiltersProps {
  sessions: ManifestSession[];
  search: string;
  onSearchChange: (value: string) => void;
  activeTags: Set<string>;
  onToggleTag: (tag: string) => void;
  onClear: () => void;
}

export function CatalogFilters({ sessions, search, onSearchChange, activeTags, onToggleTag, onClear }: CatalogFiltersProps) {
  const allTags = Array.from(
    new Set(sessions.flatMap((s) => s.tags))
  ).sort();

  const hasFilters = search.length > 0 || activeTags.size > 0;

  return (
    <div className="catalog-filter-bar">
      <input
        className="catalog-search-input"
        type="text"
        placeholder="Search sessions..."
        value={search}
        onChange={(e) => onSearchChange(e.target.value)}
      />
      {allTags.map((tag) => (
        <button
          key={tag}
          className={`catalog-tag-pill ${activeTags.has(tag) ? "catalog-tag-pill--active" : ""}`}
          onClick={() => onToggleTag(tag)}
        >
          {tag}
        </button>
      ))}
      {hasFilters && (
        <button className="catalog-clear-link" onClick={onClear}>
          Clear filters
        </button>
      )}
    </div>
  );
}
