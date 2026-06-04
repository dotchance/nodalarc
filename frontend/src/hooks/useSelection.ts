// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Shared selection state between globe and topology views. */

import { useState, useCallback } from "react";
import type { Selection } from "../types";

export function useSelection() {
  const [selection, setSelection] = useState<Selection | null>(null);
  // The "anchor" ground station for Selected Pair Mode: when a GS is selected and the user then
  // clicks a satellite, the sat panel opens straight to the GS<->sat pair decision.
  // The anchor persists while exploring satellites; selecting a new GS replaces it; clearing drops it.
  const [anchorGsId, setAnchorGsId] = useState<string | null>(null);

  const select = useCallback((sel: Selection | null) => {
    setSelection(sel);
    if (sel?.type === "ground_station") setAnchorGsId(sel.id);
    else if (sel === null) setAnchorGsId(null);
  }, []);

  const clearSelection = useCallback(() => {
    setSelection(null);
    setAnchorGsId(null);
  }, []);

  return { selection, select, clearSelection, anchorGsId };
}
