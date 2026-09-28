# system-changes.md

Running list of defects and wanted changes in the writing-engine toolkit,
gathered from real runs against real projects. It stays on this computer:
it is gitignored, because its items describe real, often unpublished work.

## If you were handed this file to work from

Read this before touching an item.

**Fixing an item does not clear it.** The instinct on finishing a fix is to
tick the item off; do not. Move it to `FIX ATTEMPTED`, record what you changed
and when, and leave it in the open list. Only a completed engine run that
fails to reproduce the issue may move it to `VERIFIED FIXED`, and only then
does it go to Closed. You are almost never the one who closes an item — the
next run is.

**Since 2026-09-06 the run does that itself, for the defects it can detect.**
A build that succeeds closes every item whose `Code:` the engine actively
looks for and did not find — `promote_verified()` in `tools/manuscript.py`.
That is a narrow set on purpose: the codes in `ENGINE_ISSUES`, and nothing
else. For every other item the engine detects nothing, so "this run did not
encounter it" would be vacuously true and one build would close thirty items
having checked none of them. Those still need a person to run their **verify
by** line. A run reports what it closed *and* names what it could not judge,
so "nothing was promoted" never has to be read as "nothing was promotable".

**Only engine misbehavior belongs in this file.** A problem with a
*manuscript* — bad phrasing, a wrong claim, a badly organized section, a
missing float — is feedback for that draft, and it already has somewhere to
live: `completeness`, the reports, and the PAPER NOT COMPLETE block. A
problem with the *engine* is a section silently dropped, a heading not
emitted, an author name inverted, an exit code of 0 over a broken build. That
distinction is the thing most likely to blur when someone reads this file
cold, and blurring it fills the list with things no code change can fix.

**Adding an item.** Match the shape of the existing ones: status, first seen
(date, project, journal), where in the source, what happens, wanted behavior,
and a **verify by** line. The verify-by is not optional — without it, "did
this run see the issue?" is answerable only by whoever originally found it,
and the status convention stops working.

## How this file works

**Status values.** Every item carries exactly one.

| Status | Meaning |
| --- | --- |
| `OPEN` | Seen on a run. Nothing attempted. |
| `FIX ATTEMPTED` | A change was made that is meant to fix it. **Not verified.** |
| `VERIFIED FIXED` | A full engine run has completed since the fix without the issue reappearing. Only then may the item be moved to the Closed section. Written by the run itself where the engine detects the defect; by hand, after running the item's **verify by**, where it does not. |

**The rule that matters:** an item is **never cleared on the strength of the
fix alone**. Editing the engine moves an item to `FIX ATTEMPTED` and records
what was changed and when. It stays in the open list, visible, until an engine
run of the kind that originally exposed it finishes *without* the issue
occurring — at which point it becomes `VERIFIED FIXED` and moves down to
Closed. A fix that was attempted and then seen to fail goes back to `OPEN`
with the failed attempt kept in its history, so the same wrong fix is not
tried twice.

Each item records **how to verify** it, because "did this run see the issue?"
has to be answerable by whoever is running the engine next, not just by
whoever found it.

---

## Open items

---

## Closed items
*(An item arrives here only after a run has completed without seeing it —
`VERIFIED FIXED`, never `FIX ATTEMPTED`. Only its code, status and last
sighting are kept; the detail is cleared on closing.)*

## Run history
Which runs have exercised the engine, so `VERIFIED FIXED` can be judged
against something concrete.

| Date | Project | Journal | Preset | Result |
| --- | --- | --- | --- | --- |
