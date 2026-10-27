/** Backend-owned graphical and YAML catalog component editing. */

import { useEffect, useRef, useState } from "react";
import { Button } from "../ui/Button";
import {
  EditorCard,
  Field,
  PasteArea,
  SelectField,
} from "./editorKit";
import {
  addCatalogDraftNodeEthernet,
  addCatalogDraftNodeTerminal,
  addCatalogDraftSiteNode,
  applyCatalogDraftYaml,
  compileCatalogDraft,
  getCatalogDependents,
  mutateCatalogDraftControls,
  saveCatalogDraft,
} from "./builderApiClient";
import {
  GraphicalControlTreeEditor,
  type BuilderControlMutation,
} from "./GraphicalControlTreeEditor";
import { useBuilderCatalog } from "./useBuilderWorld";
import type {
  BuilderVisualAuthoringFacts,
  CatalogComponentDraftEnvelope,
  CatalogDependencyImpact,
  CatalogDraftCompileResult,
  CatalogDraftIssue,
  CatalogDraftSaveResult,
  JsonValue,
} from "./generated/builderApi";

type JsonObject = { [key: string]: JsonValue };

function isObject(value: unknown): value is JsonObject {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function jsonEqual(left: JsonValue | undefined, right: JsonValue | undefined): boolean {
  if (Object.is(left, right)) return true;
  if (Array.isArray(left) || Array.isArray(right)) {
    return (
      Array.isArray(left) &&
      Array.isArray(right) &&
      left.length === right.length &&
      left.every((value, index) => jsonEqual(value, right[index]))
    );
  }
  if (isObject(left) || isObject(right)) {
    if (!isObject(left) || !isObject(right)) return false;
    const leftKeys = Object.keys(left);
    const rightKeys = Object.keys(right);
    return (
      leftKeys.length === rightKeys.length &&
      leftKeys.every((key) => jsonEqual(left[key], right[key]))
    );
  }
  return false;
}

interface NodeGesturesProps {
  authoring: BuilderVisualAuthoringFacts;
  disabled: boolean;
  addTerminalMount: (
    role: BuilderVisualAuthoringFacts["default_mount_role"],
    terminalRef: string,
  ) => Promise<boolean>;
  addEthernetPort: () => Promise<boolean>;
}

/** Node gestures whose shape the backend derives from the referenced terminal. */
function NodeGestures({ authoring, disabled, addTerminalMount, addEthernetPort }: NodeGesturesProps) {
  const terminals = useBuilderCatalog("terminals");
  const [role, setRole] = useState(authoring.default_mount_role);
  const [terminal, setTerminal] = useState("");
  return (
    <EditorCard title="Add to this node" open>
      <SelectField
        label="new mount role"
        value={role}
        onChange={(value) => setRole(value as BuilderVisualAuthoringFacts["default_mount_role"])}
        options={authoring.mount_roles.map((choice) => ({ value: choice.id, label: choice.label }))}
      />
      <SelectField
        stack
        label="new mount terminal"
        value={terminal}
        onChange={setTerminal}
        options={[
          { value: "", label: "select terminal", disabled: true },
          ...terminals.entries.map((entry) => ({
            value: entry.ref,
            label: `${entry.namespace === "user" ? "★ " : ""}${entry.display_name}`,
          })),
        ]}
      />
      <div className="builder-preset-row">
        <Button disabled={disabled || !terminal} onClick={() => void addTerminalMount(role, terminal)}>
          + terminal mount
        </Button>
        <Button disabled={disabled} onClick={() => void addEthernetPort()}>
          + LAN port
        </Button>
      </div>
    </EditorCard>
  );
}

interface SiteGesturesProps {
  disabled: boolean;
  addNode: (nodeId: string, nodeRef: string) => Promise<boolean>;
}

/** Site gesture whose installed shape the backend derives from the node model. */
function SiteGestures({ disabled, addNode }: SiteGesturesProps) {
  const nodes = useBuilderCatalog("nodes");
  const [nodeId, setNodeId] = useState("");
  const [nodeRef, setNodeRef] = useState("");
  return (
    <EditorCard title="Add installed node" open>
      <Field label="node id" value={nodeId} placeholder="for example, gw1" onChange={setNodeId} />
      <SelectField
        stack
        label="node model"
        ariaLabel="New site node model"
        value={nodeRef}
        onChange={setNodeRef}
        options={[
          { value: "", label: "select a node model" },
          ...nodes.entries.map((entry) => ({
            value: entry.ref,
            label: entry.display_name ?? entry.ref,
          })),
        ]}
      />
      <Button
        disabled={disabled || !nodeId.trim() || !nodeRef}
        onClick={() => {
          void addNode(nodeId.trim(), nodeRef).then((added) => {
            if (added) {
              setNodeId("");
              setNodeRef("");
            }
          });
        }}
      >
        + add node
      </Button>
    </EditorCard>
  );
}

function impactText(impact: CatalogDependencyImpact): string {
  const direct = impact.direct_dependents.length;
  const transitive = impact.transitive_dependents.length;
  if (direct === 0 && transitive === 0) return "No saved sessions or components depend on this object.";
  return `Overwrite affects ${direct} direct and ${transitive} transitive dependent${transitive === 1 ? "" : "s"}.`;
}

export function CatalogDraftEditorWindow({
  initialDraft,
  onSaved,
  onClose,
  onDiscard,
  onDirtyChange,
  authoring,
}: {
  initialDraft: CatalogComponentDraftEnvelope;
  onSaved: (result: CatalogDraftSaveResult) => void | Promise<void>;
  onClose: () => void;
  onDiscard?: () => void;
  /** Told whether the window holds edits not yet saved to the library. */
  onDirtyChange?: (dirty: boolean) => void;
  authoring: BuilderVisualAuthoringFacts;
}) {
  const [draft, setDraft] = useState(initialDraft);
  const draftRef = useRef(initialDraft);
  const [baselineDocument, setBaselineDocument] = useState(initialDraft.state.document);
  const [compileResult, setCompileResult] = useState<CatalogDraftCompileResult | null>(null);
  const [yamlText, setYamlText] = useState(initialDraft.projected_yaml);
  const [appliedYamlText, setAppliedYamlText] = useState(initialDraft.projected_yaml);
  const [yamlIssues, setYamlIssues] = useState<ReadonlyArray<CatalogDraftIssue>>([]);
  const [canonicalizationRequired, setCanonicalizationRequired] = useState(false);
  const [canonicalizationAccepted, setCanonicalizationAccepted] = useState(false);
  const [yamlSync, setYamlSync] = useState<"idle" | "syncing" | "failed">("idle");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"idle" | "validating" | "saving">("idle");
  const [impact, setImpact] = useState<CatalogDependencyImpact | null>(null);
  const [impactState, setImpactState] = useState<"not-required" | "loading" | "loaded" | "failed">(
    initialDraft.state.expected_target_revision ? "loading" : "not-required",
  );
  const [impactAttempt, setImpactAttempt] = useState(0);
  const yamlBufferGeneration = useRef(0);
  const yamlApplyQueue = useRef<Promise<void>>(Promise.resolve());
  const yamlPendingApply = useRef<{
    generation: number;
    promise: Promise<CatalogComponentDraftEnvelope | null>;
  } | null>(null);
  const yamlTextDirty = yamlText !== appliedYamlText;
  const semanticDirty = !jsonEqual(baselineDocument, draft.state.document);
  const dirty = semanticDirty || yamlTextDirty || canonicalizationRequired
    || canonicalizationAccepted;
  const graphicalEditingBlocked = yamlTextDirty || canonicalizationRequired;
  const issues = yamlIssues.length > 0
    ? yamlIssues
    : compileResult?.issues ?? draft.issues;

  useEffect(() => {
    onDirtyChange?.(dirty);
  }, [dirty, onDirtyChange]);

  useEffect(() => {
    if (!draft.state.expected_target_revision) {
      setImpact(null);
      setImpactState("not-required");
      return;
    }
    setImpact(null);
    setImpactState("loading");
    void getCatalogDependents({ ref: draft.state.target_ref }).then(
      (result) => {
        setImpact(result);
        setImpactState("loaded");
      },
      (cause) => {
        setImpactState("failed");
        setError(cause instanceof Error ? cause.message : String(cause));
      },
    );
  }, [draft.state.expected_target_revision, draft.state.target_ref, impactAttempt]);

  const adoptGraphicalDraft = (updated: CatalogComponentDraftEnvelope) => {
    yamlBufferGeneration.current += 1;
    draftRef.current = updated;
    setDraft(updated);
    setYamlText(updated.projected_yaml);
    setAppliedYamlText(updated.projected_yaml);
    setYamlIssues([]);
    setCanonicalizationRequired(false);
    setCanonicalizationAccepted(false);
    setCompileResult(null);
  };

  const applyYamlBuffer = (
    buffer: string,
    generation: number,
  ): Promise<CatalogComponentDraftEnvelope | null> => {
    if (yamlPendingApply.current?.generation === generation) {
      return yamlPendingApply.current.promise;
    }
    const run = async (): Promise<CatalogComponentDraftEnvelope | null> => {
      if (yamlBufferGeneration.current === generation) setYamlSync("syncing");
      try {
        const baseDraft = draftRef.current;
        const result = await applyCatalogDraftYaml({
          draft: baseDraft.state,
          expected_draft_revision: baseDraft.state.draft_revision,
          yaml_text: buffer,
        });
        if (yamlBufferGeneration.current !== generation) return null;
        setYamlIssues(result.issues);
        setYamlSync("idle");
        if (!result.applied) return null;
        draftRef.current = result.draft;
        setDraft(result.draft);
        setYamlText(result.yaml_text);
        setAppliedYamlText(result.yaml_text);
        setCanonicalizationRequired(result.canonicalization_required);
        setCanonicalizationAccepted(false);
        setCompileResult(null);
        return result.draft;
      } catch (cause) {
        if (yamlBufferGeneration.current === generation) {
          setYamlSync("failed");
          setError(cause instanceof Error ? cause.message : String(cause));
        }
        return null;
      }
    };
    const operation = yamlApplyQueue.current.then(run, run);
    yamlApplyQueue.current = operation.then(() => undefined, () => undefined);
    yamlPendingApply.current = { generation, promise: operation };
    void operation.finally(() => {
      if (yamlPendingApply.current?.promise === operation) yamlPendingApply.current = null;
    });
    return operation;
  };

  const applyYaml = (): Promise<CatalogComponentDraftEnvelope | null> => {
    if (!yamlTextDirty) return Promise.resolve(draftRef.current);
    return applyYamlBuffer(yamlText, yamlBufferGeneration.current);
  };

  useEffect(() => {
    if (!yamlTextDirty || busy !== "idle") return;
    const generation = yamlBufferGeneration.current;
    const buffer = yamlText;
    const timer = window.setTimeout(() => {
      void applyYamlBuffer(buffer, generation);
    }, 400);
    return () => window.clearTimeout(timer);
  }, [busy, yamlText, yamlTextDirty]);

  const addSiteNode = async (nodeId: string, nodeRef: string): Promise<boolean> => {
    setBusy("validating");
    setError(null);
    try {
      const base = draftRef.current;
      const updated = await addCatalogDraftSiteNode({
        draft: base.state,
        expected_draft_revision: base.state.draft_revision,
        node_id: nodeId,
        node_ref: nodeRef,
      });
      adoptGraphicalDraft(updated);
      return true;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      return false;
    } finally {
      setBusy("idle");
    }
  };

  const addNodeTerminalMount = async (
    role: BuilderVisualAuthoringFacts["default_mount_role"],
    terminalRef: string,
  ): Promise<boolean> => {
    setBusy("validating");
    setError(null);
    try {
      const base = draftRef.current;
      const updated = await addCatalogDraftNodeTerminal({
        draft: base.state,
        expected_draft_revision: base.state.draft_revision,
        terminal_ref: terminalRef,
        role,
      });
      adoptGraphicalDraft(updated);
      return true;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      return false;
    } finally {
      setBusy("idle");
    }
  };

  const addNodeEthernetPort = async (): Promise<boolean> => {
    setBusy("validating");
    setError(null);
    try {
      const base = draftRef.current;
      const updated = await addCatalogDraftNodeEthernet({
        draft: base.state,
        expected_draft_revision: base.state.draft_revision,
      });
      adoptGraphicalDraft(updated);
      return true;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      return false;
    } finally {
      setBusy("idle");
    }
  };

  const mutateControls = async (
    commands: ReadonlyArray<BuilderControlMutation>,
  ): Promise<void> => {
    if (graphicalEditingBlocked) {
      throw new Error("Apply or revert the component YAML before changing this field.");
    }
    setBusy("validating");
    setError(null);
    try {
      const baseDraft = draftRef.current;
      const updated = await mutateCatalogDraftControls({
        draft: baseDraft.state,
        expected_draft_revision: baseDraft.state.draft_revision,
        commands,
      });
      adoptGraphicalDraft(updated);
    } finally {
      setBusy("idle");
    }
  };

  const validate = async () => {
    setBusy("validating");
    setError(null);
    try {
      const patched = yamlTextDirty ? await applyYaml() : draftRef.current;
      if (!patched) return;
      const compiled = await compileCatalogDraft({
        draft: patched.state,
        expected_draft_revision: patched.state.draft_revision,
      });
      draftRef.current = compiled.draft;
      setDraft(compiled.draft);
      setYamlIssues([]);
      setCompileResult(compiled);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("idle");
    }
  };

  const save = async () => {
    if (draft.state.expected_target_revision && impactState !== "loaded") {
      setError("Dependency impact must load before this user component can be overwritten.");
      return;
    }
    if (canonicalizationRequired) {
      setError("Acknowledge canonical YAML formatting before saving this component.");
      return;
    }
    setBusy("saving");
    setError(null);
    try {
      const patched = yamlTextDirty ? await applyYaml() : draftRef.current;
      if (!patched) return;
      if (yamlTextDirty && yamlText !== patched.projected_yaml) {
        setError("Review and acknowledge canonical YAML formatting before saving.");
        return;
      }
      const compiled = await compileCatalogDraft({
        draft: patched.state,
        expected_draft_revision: patched.state.draft_revision,
      });
      draftRef.current = compiled.draft;
      setDraft(compiled.draft);
      setYamlIssues([]);
      setCompileResult(compiled);
      if (!compiled.save_allowed) return;
      const saved = await saveCatalogDraft({
        draft: compiled.draft.state,
        expected_draft_revision: compiled.draft.state.draft_revision,
      });
      yamlBufferGeneration.current += 1;
      draftRef.current = saved.draft;
      setDraft(saved.draft);
      setBaselineDocument(saved.draft.state.document);
      setYamlText(saved.draft.projected_yaml);
      setAppliedYamlText(saved.draft.projected_yaml);
      setYamlIssues([]);
      setCanonicalizationRequired(false);
      setCanonicalizationAccepted(false);
      setCompileResult(saved.compile_result);
      await onSaved(saved);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("idle");
    }
  };

  const gesturesDisabled = busy !== "idle" || graphicalEditingBlocked;
  const gestures = draft.state.family === "nodes"
    ? (
      <NodeGestures
        authoring={authoring}
        disabled={gesturesDisabled}
        addTerminalMount={addNodeTerminalMount}
        addEthernetPort={addNodeEthernetPort}
      />
    )
    : draft.state.family === "sites"
      ? <SiteGestures disabled={gesturesDisabled} addNode={addSiteNode} />
      : null;

  return (
    <div className="builder-inspector-stack" data-testid="catalog-draft-editor">
      <div className="builder-site-derived">
        {draft.state.source_ref && draft.state.source_ref !== draft.state.target_ref
          ? `customizing ${draft.state.source_ref} as ${draft.state.target_ref}`
          : `editing ${draft.state.target_ref}`}
      </div>
      <div className="catalog-draft-surfaces">
        <div className="builder-inspector-stack" data-testid="catalog-graphical-editor">
          <div className="builder-zone-title">Graphical editor</div>
          <GraphicalControlTreeEditor
            tree={draft.control_tree}
            disabled={busy !== "idle" || graphicalEditingBlocked}
            onMutate={mutateControls}
          />
          {gestures}
          {yamlTextDirty && (
            <div className="builder-warning" data-testid="catalog-yaml-stale-marker">
              Showing applied revision {draft.state.draft_revision}; the YAML buffer has unapplied edits.
            </div>
          )}
          {canonicalizationRequired && (
            <div className="builder-warning" data-testid="catalog-canonicalization-warning">
              The applied YAML contains formatting or comments that graphical edits and saves
              cannot preserve. Review the backend projection before continuing.
              <div className="builder-preset-row">
                <Button
                  disabled={busy !== "idle"}
                  onClick={() => {
                    yamlBufferGeneration.current += 1;
                    setYamlText(draft.projected_yaml);
                    setAppliedYamlText(draft.projected_yaml);
                    setYamlIssues(draft.issues);
                    setCanonicalizationRequired(false);
                    setCanonicalizationAccepted(true);
                    setError(null);
                  }}
                >
                  Use canonical YAML
                </Button>
              </div>
            </div>
          )}
        </div>
        <div className="builder-inspector-stack" data-testid="catalog-yaml-editor">
          <div className="builder-zone-title">Component YAML</div>
          <div className="builder-site-derived">
            VS-API parses this exact buffer through the canonical catalog loader. The graphical
            view changes only after a valid buffer is applied.
          </div>
          <PasteArea
            ariaLabel="Component YAML"
            className="catalog-draft-yaml"
            rows={28}
            value={yamlText}
            disabled={busy !== "idle"}
            onChange={(value) => {
              yamlBufferGeneration.current += 1;
              setYamlText(value);
              setYamlSync("idle");
              setYamlIssues([]);
              setError(null);
            }}
            placeholder="catalog component YAML"
          />
          <div className="builder-preset-row">
            <Button
              disabled={busy !== "idle" || !yamlTextDirty}
              onClick={() => {
                setBusy("validating");
                void applyYaml().finally(() => setBusy("idle"));
              }}
            >
              Apply YAML
            </Button>
            <Button
              disabled={busy !== "idle" || !yamlTextDirty}
              onClick={() => {
                yamlBufferGeneration.current += 1;
                setYamlText(appliedYamlText);
                setYamlSync("idle");
                setYamlIssues([]);
                setError(null);
              }}
            >
              Revert buffer
            </Button>
          </div>
          {yamlSync === "syncing" && (
            <div className="builder-site-derived">validating YAML and updating the graphical view…</div>
          )}
          {yamlSync === "failed" && (
            <div className="builder-warning">YAML validation could not reach VS-API.</div>
          )}
        </div>
      </div>
      {issues.map((issue) => (
        <div className="builder-warning" key={`${issue.code}:${issue.pointer}`}>
          {issue.message}{" "}
          <span className="builder-site-derived">
            {issue.pointer}
            {issue.source_line ? ` · line ${issue.source_line}:${issue.source_column ?? 1}` : ""}
          </span>
        </div>
      ))}
      {impact && (
        <div className="builder-library-note" data-testid="catalog-dependency-impact">
          {impactText(impact)}
          {[...new Map(
            [...impact.direct_dependents, ...impact.transitive_dependents]
              .map((dependent) => [dependent.ref, dependent]),
          ).values()].map((dependent) => (
            <div className="builder-site-derived" key={dependent.ref}>
              {dependent.family} · {dependent.ref}
            </div>
          ))}
        </div>
      )}
      {impactState === "loading" && (
        <div className="builder-site-derived">checking saved dependents before overwrite…</div>
      )}
      {impactState === "failed" && draft.state.expected_target_revision && (
        <Button
          onClick={() => {
            setError(null);
            setImpactAttempt((attempt) => attempt + 1);
          }}
        >
          retry dependency check
        </Button>
      )}
      {compileResult?.save_allowed && !compileResult.runtime_supported && (
        <div className="builder-warning">
          Structurally valid and savable; current runtime support blocks deployment.
        </div>
      )}
      {error && <div className="builder-warning">{error}</div>}
      <div className="builder-preset-row">
        <Button disabled={busy !== "idle"} onClick={() => void validate()}>
          {busy === "validating" ? "Validating…" : dirty ? "Validate edits" : "Validate"}
        </Button>
        <Button
          variant="primary"
          disabled={
            busy !== "idle"
            || canonicalizationRequired
            || impactState === "loading"
            || impactState === "failed"
          }
          onClick={() => void save()}
        >
          {busy === "saving" ? "Saving…" : "Save to library"}
        </Button>
        {dirty && onDiscard && (
          <Button disabled={busy !== "idle"} onClick={onDiscard}>Discard edits</Button>
        )}
        <Button disabled={busy !== "idle"} onClick={onClose}>Close</Button>
      </div>
    </div>
  );
}
