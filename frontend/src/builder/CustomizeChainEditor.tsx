// Copyright 2024-2026 .chance (dotchance)
// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
import { useState } from "react";
import { Button } from "../ui/Button";
import { Field, SelectField } from "./editorKit";
import type { BuilderVisualCustomizeChainResult } from "./generated/builderApi";

export function CustomizeChainEditor({
  segmentId,
  rootRef,
  dependencyRefs,
  proposedRefs,
  onCustomize,
  onEdit,
  onClose,
}: {
  segmentId: string;
  rootRef: string;
  dependencyRefs: readonly string[];
  /** The documents the session already proposes; these open for editing. */
  proposedRefs: ReadonlySet<string>;
  /** Open the document the session proposes at `ref` in the component window. */
  onEdit: (ref: string) => void;
  onCustomize: (
    leafRef: string,
    targetLeafRef: string | null,
  ) => Promise<BuilderVisualCustomizeChainResult>;
  onClose: () => void;
}) {
  const refs = [...new Set([rootRef, ...dependencyRefs].filter(Boolean))];
  const [leafRef, setLeafRef] = useState(rootRef || refs[0] || "");
  const [targetLeafRef, setTargetLeafRef] = useState("");
  const [saving, setSaving] = useState(false);
  const [issues, setIssues] = useState<readonly string[]>([]);

  return (
    <div className="builder-inspector-stack" data-testid="customize-chain-editor">
      <div className="builder-site-derived">
        VS-API forks only the selected leaf and its minimal ancestor path, then
        rewrites this placed segment without changing its identity.
      </div>
      <SelectField
        stack
        label="component"
        ariaLabel="Component to customize"
        value={leafRef}
        onChange={setLeafRef}
        options={refs.map((ref) => ({ value: ref, label: ref }))}
      />
      <Field
        stack
        label="target leaf ref (optional)"
        value={targetLeafRef}
        placeholder="leave blank for a backend-allocated user: ref"
        onChange={setTargetLeafRef}
      />
      {issues.map((issue) => (
        <div className="builder-warning" key={issue}>
          {issue}
        </div>
      ))}
      {proposedRefs.has(leafRef) ? (
        <div className="builder-preset-row">
          <Button variant="primary" onClick={() => onEdit(leafRef)}>
            Edit
          </Button>
          <Button onClick={onClose}>Cancel</Button>
        </div>
      ) : (
      <div className="builder-preset-row">
        <Button
          variant="primary"
          disabled={saving || !leafRef}
          onClick={() => {
            setSaving(true);
            setIssues([]);
            void onCustomize(
              leafRef,
              targetLeafRef.trim() ? targetLeafRef.trim() : null,
            ).then(
              (result) => {
                setSaving(false);
                const forkedLeaf = result.forked_chain[result.forked_chain.length - 1]?.target_ref;
                // The fork is the session's own document now; it opens for editing.
                if (result.applied && forkedLeaf) onEdit(forkedLeaf);
                else if (result.applied) onClose();
                else setIssues((result.issues ?? []).map((issue) => issue.message));
              },
              (cause) => {
                setSaving(false);
                setIssues([cause instanceof Error ? cause.message : String(cause)]);
              },
            );
          }}
        >
          {saving ? "Customizing…" : "Create user forks"}
        </Button>
        <Button disabled={saving} onClick={onClose}>
          Cancel
        </Button>
      </div>
      )}
      <div className="builder-zone-empty">segment: {segmentId}</div>
    </div>
  );
}
