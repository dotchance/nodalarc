/** Fetch /manifest.json once on mount, cache in state. */

import { useState, useEffect } from "react";
import type { Manifest } from "../catalog/catalogTypes";

interface UseManifestResult {
  manifest: Manifest | null;
  loading: boolean;
  error: string | null;
}

export function useManifest(): UseManifestResult {
  const [manifest, setManifest] = useState<Manifest | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/manifest.json")
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then((data: Manifest) => {
        setManifest(data);
        setLoading(false);
      })
      .catch((err) => {
        setError(err instanceof Error ? err.message : String(err));
        setLoading(false);
      });
  }, []);

  return { manifest, loading, error };
}
