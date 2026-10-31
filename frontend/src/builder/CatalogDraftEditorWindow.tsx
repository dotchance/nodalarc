/** Backend-owned graphical and YAML catalog component editing. */

import { useCallback, useEffect, useRef, useState } from "react";
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
import {
  adoptedYamlBuffer,
  answeredYamlBuffer,
  useYamlAutoApply,
  useYamlBuffer,
  yamlBufferBlocksGraphicalEditing,
} from "./useYamlBuffer";
import type {
  BuilderVisualAuthoringFacts,
  CatalogComponentDraftEnvelope,
  CatalogDependencyImpact,
  CatalogDraftCompileResult,
  CatalogDraftSaveResult,
  CatalogDraftSessionContext,
  NadirBoresight,
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
    boresight: NadirBoresight | null,
  ) => Promise<boolean>;
  addEthernetPort: () => Promise<boolean>;
}

/** Node gestures whose shape the backend derives from the referenced terminal. */
function NodeGestures({ authoring, disabled, addTerminalMount, addEthernetPort }: NodeGesturesProps) {
  const terminals = useBuilderCatalog("terminals");
  const [role, setRole] = useState(authoring.default_mount_role);
  const [terminal, setTerminal] = useState("");
  // A space node's access mount points at nadir; a ground node's access mount
  // carries no boresight, because its site installation states one.
  const [pointing, setPointing] = useState<"nadir" | "none">("nadir");
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
      {role === "access" && (
        <SelectField
          label="new mount boresight"
          value={pointing}
          onChange={(value) => setPointing(value as "nadir" | "none")}
          options={[
            { value: "nadir", label: "nadir (a space node)" },
            { value: "none", label: "none (its site states one)" },
          ]}
        />
      )}
      <div className="builder-preset-row">
        <Button
          disabled={disabled || !terminal}
          onClick={() =>
            void addTerminalMount(
              role,
              terminal,
              role === "access" && pointing === "nadir" ? { mode: "nadir" } : null,
            )
          }
        >
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

/** A window over a document the session proposes. Its gestures read the
 *  session's proposals, and OK holds the document as the session's pending edit;
 *  nothing is saved to the library. */
export interface ProposalSession {
  readonly context: () => CatalogDraftSessionContext;
  readonly onStage: (working: CatalogComponentDraftEnvelope["state"]["document"]) => void;
}

export function CatalogDraftEditorWindow({
  initialDraft,
  onSaved,
  onClose,
  onDiscard,
  onDirtyChange,
  authoring,
  session,
}: {
  initialDraft: CatalogComponentDraftEnvelope;
  onSaved: (result: CatalogDraftSaveResult) => void | Promise<void>;
  onClose: () => void;
  onDiscard?: () => void;
  /** Told whether the window holds edits not yet saved to the library. */
  onDirtyChange?: (dirty: boolean) => void;
  authoring: BuilderVisualAuthoringFacts;
  /** Set when the window edits a document the session proposes. */
  session?: ProposalSession;
}) {
  // A proposed document's gestures read the session's proposals.
  const sessionOf = () => (session ? { session: session.context() } : {});
  const [draft, setDraft] = useState(initialDraft);
  const draftRef = useRef(initialDraft);
  const [baselineDocument, setBaselineDocument] = useState(initialDraft.state.document);
  const [compileResult, setCompileResult] = useState<CatalogDraftCompileResult | null>(null);
  const yaml = useYamlBuffer(adoptedYamlBuffer(initialDraft.projected_yaml, 0));
  const yamlBuffer = yaml.state;
  const [yamlSyncing, setYamlSyncing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"idle" | "validating" | "saving">("idle");
  const [impact, setImpact] = useState<CatalogDependencyImpact | null>(null);
  const [impactState, setImpactState] = useState<"not-required" | "loading" | "loaded" | "failed">(
    initialDraft.state.expected_target_revision ? "loading" : "not-required",
  );
  const [impactAttempt, setImpactAttempt] = useState(0);
  const yamlApplyQueue = useRef<Promise<void>>(Promise.resolve());
  const yamlPendingApply = useRef<{
    generation: number;
    promise: Promise<CatalogComponentDraftEnvelope | null>;
  } | null>(null);
  const yamlTextDirty = yamlBuffer.dirty;
  const canonicalizationRequired = yamlBuffer.canonicalizationRequired;
  const semanticDirty = !jsonEqual(baselineDocument, draft.state.document);
  const dirty = semanticDirty || yamlTextDirty || canonicalizationRequired;
  const graphicalEditingBlocked = yamlBufferBlocksGraphicalEditing(yamlBuffer);
  const issues = yamlBuffer.issues.length > 0
    ? yamlBuffer.issues
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
    draftRef.current = updated;
    setDraft(updated);
    yaml.set(adoptedYamlBuffer(updated.projected_yaml, yaml.ref.current.generation + 1));
    setCompileResult(null);
  };

  /** Send buffer generation `generation` to the backend, one apply at a time. */
  const applyYamlBuffer = (generation: number): Promise<CatalogComponentDraftEnvelope | null> => {
    if (yamlPendingApply.current?.generation === generation) {
      return yamlPendingApply.current.promise;
    }
    yaml.markSent(generation);
    const run = async (): Promise<CatalogComponentDraftEnvelope | null> => {
      const buffer = yaml.ref.current;
      if (buffer.generation !== generation) return null;
      setYamlSyncing(true);
      try {
        const baseDraft = draftRef.current;
        const result = await applyCatalogDraftYaml({
          ...sessionOf(),
          draft: baseDraft.state,
          expected_draft_revision: baseDraft.state.draft_revision,
          yaml_text: buffer.text,
        });
        if (yaml.ref.current.generation !== generation) return null;
        yaml.set(answeredYamlBuffer(yaml.ref.current, generation, result));
        if (!result.applied) return null;
        draftRef.current = result.draft;
        setDraft(result.draft);
        setCompileResult(null);
        return result.draft;
      } catch (cause) {
        yaml.holdFailure(generation, cause);
        return null;
      } finally {
        setYamlSyncing(false);
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
    const buffer = yaml.ref.current;
    if (!buffer.dirty) return Promise.resolve(draftRef.current);
    return applyYamlBuffer(buffer.generation);
  };

  const applyYamlBufferRef = useRef(applyYamlBuffer);
  applyYamlBufferRef.current = applyYamlBuffer;
  const autoApplyYaml = useCallback((generation: number) => {
    void applyYamlBufferRef.current(generation);
  }, []);
  useYamlAutoApply(yamlBuffer, busy === "idle", autoApplyYaml);

  const addSiteNode = async (nodeId: string, nodeRef: string): Promise<boolean> => {
    setBusy("validating");
    setError(null);
    try {
      const base = draftRef.current;
      const updated = await addCatalogDraftSiteNode({
          ...sessionOf(),
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
    boresight: NadirBoresight | null,
  ): Promise<boolean> => {
    setBusy("validating");
    setError(null);
    try {
      const base = draftRef.current;
      const updated = await addCatalogDraftNodeTerminal({
          ...sessionOf(),
        draft: base.state,
        expected_draft_revision: base.state.draft_revision,
        terminal_ref: terminalRef,
        role,
        boresight,
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
          ...sessionOf(),
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
          ...sessionOf(),
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
      yaml.set({ ...yaml.ref.current, issues: [] });
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
      if (yaml.ref.current.canonicalizationRequired) {
        setError("Review and acknowledge canonical YAML formatting before saving.");
        return;
      }
      const compiled = await compileCatalogDraft({
        draft: patched.state,
        expected_draft_revision: patched.state.draft_revision,
      });
      draftRef.current = compiled.draft;
      setDraft(compiled.draft);
      yaml.set({ ...yaml.ref.current, issues: [] });
      setCompileResult(compiled);
      if (!compiled.save_allowed) return;
      const saved = await saveCatalogDraft({
        draft: compiled.draft.state,
        expected_draft_revision: compiled.draft.state.draft_revision,
      });
      draftRef.current = saved.draft;
      setDraft(saved.draft);
      setBaselineDocument(saved.draft.state.document);
      yaml.set(adoptedYamlBuffer(saved.draft.projected_yaml, yaml.ref.current.generation + 1));
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
                    setError(null);
                    void applyYamlBuffer(yaml.canonicalize(draft.projected_yaml));
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
            value={yamlBuffer.text}
            disabled={busy !== "idle"}
            onChange={(value) => {
              yaml.edit(value);
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
                yaml.edit(yaml.ref.current.appliedText);
                setError(null);
              }}
            >
              Revert buffer
            </Button>
          </div>
          {yamlSyncing && (
            <div className="builder-site-derived">validating YAML and updating the graphical view…</div>
          )}
          {yamlBuffer.applyFailure && (
            <div className="builder-warning">
              The YAML was not applied: {yamlBuffer.applyFailure}
            </div>
          )}
        </div>
      </div>
      {issues.map((issue) => (
        <div className="builder-warning" key={`${issue.code}:${issue.json_pointer ?? ""}`}>
          {issue.message}{" "}
          <span className="builder-site-derived">
            {issue.json_pointer}
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
      {compileResult && (
        <div className="builder-site-derived">
          This verdict covers this component alone. A session that places it decides the
          gates that depend on placement and profile.
        </div>
      )}
      {error && <div className="builder-warning">{error}</div>}
      {session ? (
        <div className="builder-preset-row">
          <Button
            variant="primary"
            disabled={busy !== "idle" || graphicalEditingBlocked}
            onClick={() => {
              session.onStage(draftRef.current.state.document);
              onClose();
            }}
          >
            OK
          </Button>
          <Button disabled={busy !== "idle"} onClick={onClose}>Cancel</Button>
        </div>
      ) : (
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
      )}
    </div>
  );
}
