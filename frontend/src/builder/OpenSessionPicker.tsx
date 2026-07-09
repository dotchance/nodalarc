// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** The "Open a session" picker window body (P9d leaf).
 *
 *  Pure presentation: your saved sessions and the shipped NodalArc sessions,
 *  each a row you open. The open gesture's teardown+import sequence lives in
 *  BuilderView and arrives as `onOpen`.
 */
import { Icon } from "../ui/icons/Icon";
import type { BuilderSessionListEntry } from "./builderTypes";

interface OpenSessionPickerProps {
  sessions: BuilderSessionListEntry[];
  sessionsError: string | null;
  onOpen: (entry: BuilderSessionListEntry) => void;
}

export function OpenSessionPicker({ sessions, sessionsError, onOpen }: OpenSessionPickerProps) {
  // The server names each entry's source root; the tiers speak the
  // library's own vocabulary (★ yours / nodalarc library).
  const yours = sessions.filter((s) => s.source === "user");
  const shipped = sessions.filter((s) => s.source === "nodalarc");
  const group = (label: string, list: BuilderSessionListEntry[]) =>
    list.length === 0 ? null : (
      <div className="builder-picker-group" key={label}>
        <div className="builder-outline-kind">{label}</div>
        {list.map((entry) => (
          <button
            className="builder-outline-row builder-picker-row"
            key={entry.file}
            onClick={() => onOpen(entry)}
            title={`Open ${entry.name}`}
          >
            <span className="builder-outline-name">
              <Icon name="folder-open" size={12} />
              {entry.name}
              {entry.active ? " · running" : ""}
            </span>
            <span className="builder-outline-count">{entry.constellation}</span>
          </button>
        ))}
      </div>
    );
  return (
    <div className="builder-picker" data-testid="builder-open-picker">
      {sessions.length === 0 && <div className="builder-zone-empty">no sessions found</div>}
      {group("★ yours", yours)}
      {group("nodalarc library", shipped)}
      {sessionsError && <div className="builder-warning">{sessionsError}</div>}
    </div>
  );
}
