# Builder Interaction Rules

The session builder grew one editor at a time, and for a while each editor
invented its own behavior. The result was a UI where creating a
constellation behaved differently from creating a site, where an editor
remembered which cards the previous object had open, and where roughly the
same task needed different gestures on different screens. Users noticed
immediately.

These rules fix the class of problem, not individual screens. Where a rule
says "enforced by", that component or test makes the
violation impossible or fails the build. A rule marked "review only" has no
mechanical enforcement yet; treat that as debt.

These rules cover how a person builds a session in the UI. They are separate
from the session grammar, which covers what a session file may contain. The
one connection between the two: everything the builder does must serialize
to a valid session-grammar production. The builder never has a private
dialect.

## Creating things

**Created objects open where they can be edited.** This applies to every way
of creating something: new, use, fork, mint, connect. We shipped the
constellation flow without this once; the new segment appeared as an
unremarkable row in the tree and users had to go find it. Enforced by the
create handlers in `BuilderView` and by `interactionGrammar.test.tsx`.

**Create gestures focus the seeded name.** The editor that opens on create
has the name field focused, with the seeded name selected. Typing renames;
clicking elsewhere keeps the seed. Enforced by `EditorName` in
`editorKit.tsx`, which takes an `autoFocus` flag and focuses and selects the
seeded name when it is set — the create gesture raises the flag for the
object it just made — and by the conformance tests.

**Create gestures never dead-end.** If a create or use gesture needs
something that doesn't exist yet (a workspace, a ground segment to hold a
site), the gesture creates it. If it can't, the error appears at the point
of the gesture and says what is missing. Enforced by the self-ensuring
mutations in `useWorkspace`.

## Editing things

**Editor state belongs to the object being edited.** Card open/closed state,
picker state, and scroll position belong to the object being edited, so
switching objects gives you the same canonical layout every time. We had
editors inheriting the previous object's open cards, which meant the same
editor looked different depending on what you did earlier. Enforced by
keying every editor instance on the object id (React remounts on switch) and
by the remount test in `interactionGrammar.test.tsx`.

**Editors are built from the same parts.** Name field first, then cards with
a title and a summary line (a closed card should read like a spec sheet
entry). Text fields, number fields, and selects come from `editorKit.tsx`.
Writing a raw `<input>`, `<select>`, or `<textarea>` in an editor fails the
static scan in `interactionGrammar.test.tsx`. File pickers are the one
exception, since they aren't editing controls.

**Verbs are consistent across families.** The shared verb set is use, edit,
inspect, export, delete. A family may support only some of these, but a verb
always looks the same and sits in the same place. Don't invent a second
style of edit button. Review only.

**Known values are filled in and shown.** If the system already knows a
value, fill it in and show it; don't make the user type it. The connect flow
is the main case: when two
segments are linked, the role, medium, elevation mask, and topology are
computed from the terminal inventories in the resolved world
(`linkPhysics.ts`), because the resolver already knows what each side can
form. Options that neither side can physically form are shown disabled with
the reason. Ground stamps, derived addressing, and scheduling presets follow
the same rule. Any value seeded this way becomes the user's the moment they
edit it.

**Destructive actions stay out of the primary flow.** Delete and other
destructive actions go at the end of an editor or as the remove control on a
row. Keep them away from primary actions. Review only.

## State and honesty

**Every gesture has a visible consequence.** Consequences always show up in
the same place: the resolve status, the completeness rail, or an inline
message on the owning object. Resolver errors are shown verbatim. This is
the project's customer-trust rule applied to the UI; the builder must not
look like it did something it didn't do, or hide something it did. Review
only.

**Derived values are recomputed when their inputs change.** The user is told
when a derived value changes. The concrete case: re-pointing a link rule's
endpoint changes what the rule can physically be, so the physics re-derive
and a notice states the new values (`rederiveRule` in `linkPhysics.ts`). The
old behavior, silently keeping the stale values, produced rules that asked
ground stations for crosslink optics.

**Undo and autosave cover every workspace mutation.** Undo (Ctrl/Cmd+Z)
covers every workspace mutation. Autosave runs continuously and offers a
restore after a reload. Neither depends on which surface made the change;
both hang off the single mutation path in `useWorkspace`.

**Editor windows commit through Apply or OK.** An editor window edits a
working copy; the session changes only on Apply (or OK, which applies and
closes). Every editor window ends in the
same commit row — Apply, OK, Defaults, Cancel — with a state label that says
"applied" or "unapplied changes", so the answer to "did my typing take?" is
on screen, never inferred. Closing a window and cancelling it are the same
action, and both discard. Defaults returns the working copy to its baseline —
the values at window open, advanced to the applied draft on each Apply. The
canvas previews the working copy while a window is dirty —
drag an orbit slider and the satellites move — with a status chip saying so;
the preview is the resolver's expansion of the edited draft, never a
builder-local calculation. Saving writes the applied session only, and the
save control says what is being left out when windows are dirty. Before this
rule, edits were live and closing was ambiguous:
the same gesture meant both "done" and "never mind", and the user couldn't
tell which one they had performed. Enforced by the window buffers in
`useEditorWindows`, `EditorApplyRow` in `editorKit.tsx`, and the conformance
tests.

**The builder always shows the session anatomy.** The anatomy panel shows
what a session is made of, what this one has, and why each missing part
matters, without imposing an order. The rows are permanent: each one carries
a structural state (present or not, never a health claim — the resolve
status stays the only green), a why written for both kinds of user, and an
action that creates or opens the thing. A user can build in any order they
please and still always has an answer to "what could I do next, and why
would I". This exists because watching recorded builds showed the opposite:
between steps the screen offered no reason to click anything in particular.
Enforced by `BuildGuide` and its conformance tests.

**Closed vocabularies have one owner.** A closed vocabulary the grammar
defines — mount roles, link media — is declared once in the builder's
grammar twin and imported everywhere it is offered. No surface re-lists it.
The rule editor once
hand-listed roles and silently lost `backbone` while the node editor kept
it: hardware you could mount but never select with a rule. Each vocabulary
entry carries a plain-language description written for both kinds of user,
shown wherever the token is offered. Enforced two ways: a backend contract
test pins the twin to the Python literals (a grammar change breaks the
build until the twin follows), and a conformance scan fails any builder
file that re-lists the roles or the media as an array literal. Preference
tables over a vocabulary are typed exhaustively (`Record<..., number>`), so
a new entry fails to compile where it would otherwise be silently skipped.

**Session verbs live on the toolbar.** New, open, save, deploy, restore, and
library live on the builder toolbar with standard icons, the way every
desktop application arranges file verbs. The world rail carries only the
session's content: anatomy, drafts, links, routing, the resolved tree. The
library is one surface, its own window, opened from the toolbar; no second
library affordance competes elsewhere. And saving an asset to the library is
never silent: the library opens at that asset's family with the row visible
and highlighted. The reveal is wired at the one save path every family
shares, so a new family inherits it without new wiring. This exists because
session buttons scattered down the rail read as unrelated one-offs, and
because a saved asset that just vanished into a long list convinced its
author the save failed. Enforced by the toolbar/rail source-slice scan and
the save-reveal wiring tests in `interactionGrammar.test.tsx`.

## Getting around

**Selection is shared across surfaces.** Picking an object in the tree, on
the canvas, in the rail, or in an editor is the same act, and every view
agrees about what is selected. Review only.

**Visible objects are close to editing.** Anything you can see, you can edit
within two gestures. Verified by the recorded UI drives; review only beyond
that.

## Working with these rules

Build new surfaces out of the kit and the rules come along for free; spend
design effort on what fields an editor needs, not on how fields behave. In
review, cite the relevant rule by name instead of arguing taste. The
conformance tests run with the frontend suite. Recorded end-to-end build
scenarios are the regression benchmark for friction; if a change makes the
same session take more gestures, that shows up as a measurement, not an
opinion.
