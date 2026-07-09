// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** The Library "New terminal / New site / New node" editor window body (P9d
 *  leaf). The library-draft state and the save hooks live in BuilderView and
 *  arrive as props; the functional setLibraryEditor wrappers (N56) are
 *  preserved verbatim so a concurrent edit during an async write survives.
 */
import type { Dispatch, SetStateAction } from "react";
import { Button } from "../ui/Button";
import { NodeEditor } from "./NodeEditor";
import { SiteEditor } from "./SiteEditor";
import { TerminalEditor } from "./TerminalEditor";
import {
  nodeObjectFromDraft,
  type DraftNode,
  type DraftSiteObject,
  type DraftTerminal,
} from "./workspace";
import type { BuilderCatalogEntry } from "./builderTypes";
import type { LibrarySave } from "./useBuilderWorld";

/** The Library editor's draft, discriminated by object kind. */
export type LibraryEditorState =
  | { kind: "terminal"; draft: DraftTerminal }
  | { kind: "node"; draft: DraftNode }
  | { kind: "site"; draft: DraftSiteObject };

interface LibraryEditorWindowProps {
  editor: LibraryEditorState;
  setLibraryEditor: Dispatch<SetStateAction<LibraryEditorState | null>>;
  terminalEntries: BuilderCatalogEntry[];
  refreshTerminals: () => Promise<void>;
  closeWindow: (key: string) => void;
  nodeSave: LibrarySave;
}

export function LibraryEditorWindow({
  editor,
  setLibraryEditor,
  terminalEntries,
  refreshTerminals,
  closeWindow,
  nodeSave,
}: LibraryEditorWindowProps) {
  if (editor.kind === "terminal") {
    return (
      <TerminalEditor
        draft={editor.draft}
        onChange={(update) =>
          setLibraryEditor((prev) =>
            prev?.kind === "terminal" ? { kind: "terminal", draft: update(prev.draft) } : prev,
          )
        }
        catalog={terminalEntries}
        onSaved={() => {
          setLibraryEditor(null);
          closeWindow("library");
          void refreshTerminals();
        }}
        onCancel={() => {
          setLibraryEditor(null);
          closeWindow("library");
        }}
      />
    );
  }
  if (editor.kind === "site") {
    return (
      <SiteEditor
        key="library-site"
        autoFocusName
        site={editor.draft}
        onUpdate={(update) =>
          setLibraryEditor((prev) =>
            prev?.kind === "site" ? { kind: "site", draft: update(prev.draft) } : prev,
          )
        }
        onClose={() => {
          setLibraryEditor(null);
          closeWindow("library");
        }}
      />
    );
  }
  return (
    <div className="builder-inspector-stack">
      <NodeEditor
        key="library-node"
        autoFocusName
        draft={editor.draft}
        onChange={(update) =>
          setLibraryEditor((prev) =>
            prev?.kind === "node" ? { kind: "node", draft: update(prev.draft) } : prev,
          )
        }
      />
      <div className="builder-preset-row">
        <Button
          variant="primary"
          disabled={nodeSave.saving}
          onClick={() =>
            void nodeSave.save({ node: nodeObjectFromDraft(editor.draft) }, () => {
              setLibraryEditor(null);
              closeWindow("library");
            })
          }
        >
          {nodeSave.label("Save node to library")}
        </Button>
        <Button
          onClick={() => {
            setLibraryEditor(null);
            closeWindow("library");
          }}
        >
          Cancel
        </Button>
      </div>
      {nodeSave.state.kind === "failed" && (
        <div className="builder-warning">{nodeSave.state.message}</div>
      )}
    </div>
  );
}
