// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** The choice to record a session run's history, the same wherever a session is deployed. */

export function RecordHistoryToggle({
  checked,
  onChange,
  className,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  /** Where it sits: each surface lays the toggle out in its own bar. */
  className: string;
}) {
  return (
    <label
      className={className}
      title="Keep this session run's state snapshots and link events for later analysis"
    >
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      Record session history
    </label>
  );
}
