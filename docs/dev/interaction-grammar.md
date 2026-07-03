# Builder Interaction Grammar

Status: ACTIVE — authority for every session-builder surface, current and
future. Authority order: this file > builder UI code. A surface that
violates a clause is buggy, not stylistically different.

This grammar governs how a human COMPOSES the session artifact; the session
grammar (BNF) governs what the ARTIFACT may say. The two meet in one
standing law: every builder gesture serializes to a session-grammar
production — the builder is a conforming emitter and never a dialect.
Nothing in this file changes the session grammar.

Every clause carries an enforcement pointer: a component that makes
violation inexpressible, or a test that fails on violation. A clause whose
pointer is marked ADVISORY is enforceable only by review and is debt.

## Creation

- **IG-1 Create opens what it creates.** Any create gesture (new, use,
  fork, mint, connect) ends with the created object's editor open and the
  object selected — never a silent row added somewhere to hunt for.
  Enforcement: every create handler in `BuilderView` opens the editor;
  `interactionGrammar.test.tsx`.
- **IG-2 Naming is the first act.** The editor opened by a create gesture
  has the name field focused and selected; typing renames immediately;
  leaving it keeps the seeded name. Enforcement: `EditorName`
  (`editorKit.tsx`) with `autoFocus` wired to the fresh-object id;
  `interactionGrammar.test.tsx`.
- **IG-3 Creation never dead-ends.** A create or use gesture missing a
  prerequisite either self-ensures it or states exactly what is missing,
  at the gesture. Enforcement: self-ensuring mutations in `useWorkspace`;
  resolver walls render at the gesture site.

## Editing

- **IG-4 An editor is a view of an object, not a widget with a history.**
  Switching objects yields the canonical presentation; open-card state and
  picker state are keyed by object identity and never bleed between
  objects. Enforcement: `key={object id}` on every editor instance;
  `interactionGrammar.test.tsx` remount-reset test.
- **IG-5 One editing anatomy.** Name first; cards with title plus
  spec-sheet summary (a closed card reads as the object's spec); fields,
  numbers, and selects come from the editor kit; no raw
  `<input>`/`<select>`/`<textarea>` in editor surfaces (file pickers
  excepted). Enforcement: `editorKit.tsx` is the only source of editing
  controls; static conformance scan in `interactionGrammar.test.tsx`.
- **IG-6 Same intent, same gesture.** The verb set — use, edit, inspect,
  export, delete — has one look and one placement per context; families
  differ only in WHICH verbs apply, never in how a verb looks or where it
  sits. Enforcement: `IconButton` row-action idiom; kit components.
- **IG-7 Derive, don't ask.** Values the system can compute from what it
  already knows (terminal faceplates, geometry, placement) are seeded and
  SHOWN, and become the user's the moment they touch them. Asking for a
  value the system knows is a defect. Enforcement: `linkPhysics.ts` —
  `connectSegments` derives role/medium/mask/topology from the resolved
  world's terminal inventories at the connect gesture (both endpoints
  known BEFORE the rule exists); unformable role/medium options render
  disabled with the reason; masks come from the ground side's own
  access-terminal limits. Also: ground stamps, derived addressing,
  scheduling presets.
- **IG-8 Dangerous acts live apart.** Destructive and identity-changing
  actions sit at the editor's end or as the row remove — never adjacent to
  primary actions. ADVISORY (review clause).

## State and honesty

- **IG-9 Every consequence is visible, in one voice.** Every gesture lands
  in the artifact; its consequence (resolve status, completeness rail,
  inline wall) renders in the same place with the same tone every time. No
  silent success, no silent failure. This is the customer-trust invariant
  applied to interaction. Enforcement: the single resolve pipeline, status
  bar, and rail; resolver messages verbatim.
- **IG-10 Stale derivations re-derive loudly.** When an upstream edit
  invalidates a derived value (a re-pointed endpoint, a swapped model),
  dependents re-derive with a visible notice — they never silently keep
  stale physics. Enforcement: `rederiveRule` (`linkPhysics.ts`) fires on
  every endpoint re-point and its notice renders at the rule.
- **IG-11 Trust mechanics are unconditional.** Undo covers every workspace
  mutation; autosave is continuous; both work identically on every
  surface. Enforcement: single mutation path in `useWorkspace` (bounded
  history + debounced autosave).

## Navigation

- **IG-12 One selection.** Selecting an object in any representation
  (tree, canvas, rail chip, editor) IS the selection; all representations
  agree at all times. Enforcement: single editing-target state in
  `BuilderView`.
- **IG-13 Two gestures to edit.** Any visible object is editable within
  two gestures. ADVISORY (verified by recorded UI drives).

## Usage

- New surfaces compose the kit and inherit the grammar; interaction design
  effort goes into content (which cards, which fields), not behavior.
- Reviews cite clause numbers, not taste.
- The conformance tests run with the frontend suite; recorded end-to-end
  build scenarios serve as the friction regression benchmark.
