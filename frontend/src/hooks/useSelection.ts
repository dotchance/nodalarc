// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Shared selection state between globe and topology views. */

import { useState, useCallback } from "react";
import type { Selection } from "../types";

export function useSelection() {
  const [selection, setSelection] = useState<Selection | null>(null);

  const select = useCallback((sel: Selection | null) => {
    setSelection(sel);
  }, []);

  const clearSelection = useCallback(() => {
    setSelection(null);
  }, []);

  return { selection, select, clearSelection };
}
