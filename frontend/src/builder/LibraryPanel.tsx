// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Your library — the top-level component-building surface.
 *
 *  Always visible in the outline, independent of any session: author a new
 *  terminal or node from scratch, see everything you have saved, export or
 *  delete entries. Components built here are immediately usable anywhere a
 *  picker offers the catalog — the library is the amortizer, but never the
 *  only path.
 */

import { Button } from "../ui/Button";
import {
  deleteUserObject,
  exportCatalogObject,
  useBuilderCatalog,
} from "./useBuilderWorld";

interface LibraryPanelProps {
  onNewTerminal: () => void;
  onNewNode: () => void;
}

export function LibraryPanel({ onNewTerminal, onNewNode }: LibraryPanelProps) {
  const nodes = useBuilderCatalog("nodes");
  const terminals = useBuilderCatalog("terminals");
  const userEntries = [...nodes.entries, ...terminals.entries].filter((entry) =>
    entry.ref.startsWith("user:"),
  );

  return (
    <div className="builder-outline-group" data-testid="builder-library">
      <div className="builder-outline-kind">Your library</div>
      <div className="builder-preset-row">
        <Button onClick={onNewTerminal}>+ new terminal</Button>
        <Button onClick={onNewNode}>+ new node</Button>
      </div>
      {userEntries.length === 0 ? (
        <div className="builder-zone-empty">
          nothing saved yet — author a component or customize a shipped one
        </div>
      ) : (
        userEntries.map((entry) => (
          <div className="builder-outline-row builder-outline-row--static" key={entry.ref}>
            <span title={entry.ref}>
              {entry.display_name ?? entry.id} · {entry.family.replace(/s$/, "")}
            </span>
            <span className="builder-library-actions">
              <button
                className="builder-library-action"
                title="Export file"
                onClick={() => void exportCatalogObject(entry.ref)}
              >
                ⤓
              </button>
              <button
                className="builder-library-action"
                title="Delete from your library"
                onClick={() =>
                  void deleteUserObject(entry.ref).then(() => {
                    void nodes.refresh();
                    void terminals.refresh();
                  })
                }
              >
                ✕
              </button>
            </span>
          </div>
        ))
      )}
    </div>
  );
}
