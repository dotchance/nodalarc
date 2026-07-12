import { useState } from "react";
import { Button } from "../ui/Button";
import { Field, SelectField } from "./editorKit";
import type { BuilderVisualCustomizeChainResult } from "./generated/builderApi";

export function CustomizeChainEditor({
  segmentId,
  rootRef,
  dependencyRefs,
  onCustomize,
  onClose,
}: {
  segmentId: string;
  rootRef: string;
  dependencyRefs: readonly string[];
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
                if (result.applied) onClose();
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
      <div className="builder-zone-empty">segment: {segmentId}</div>
    </div>
  );
}
