---
name: flag-resolver
description: Walk the outstanding **[FLAG - ...]** markers in a paper one at a time with the user, explain what each one is asking and why it is blocking, offer concrete options, and record what they decide so the next writing-engine round applies it. Use when a build reports flags remaining, when submission-package says NOT SENDABLE, when the user wants to answer the questions the drafter raised, or when they ask what is still blocking the paper. Runs standalone on any project at any time; it writes no prose.
---

# flag-resolver

`**[FLAG: ...]**` is the pipeline's way of saying *I did not make this up*.
Flags are raised freely while drafting, a self-resolution pass answers every
one that does not need a fact only a human has, and what survives blocks
`submission-package`. That machinery is built and correct.

**What has never existed is the other end of it.** A surviving flag is a
question addressed to a person, and the pipeline had no way to hear the answer.
The route without this skill is: read the `.docx`, find the bold brackets,
remember what each one wanted, edit the source text by hand, and hope the next
round does not walk over it. Every one of those steps is a place to lose an
answer.

**One sentence for what this does:** it walks the outstanding flags one at a
time, gives each one its context and its options, records what the user
decides, and stops. **It writes no prose.**

Spec: `specs/flag-resolver.md`. Read it before changing behaviour here.

## Invocation

Standalone, on any project, at any time — the only input for the reading half
is the source text.

```
flag-resolver                          # the project in the working directory
flag-resolver "Projects/surface_example"
flag-resolver --type author            # one type at a time
flag-resolver --all                    # include deferred flags
flag-resolver --list                   # print them and stop; no conversation
```

It does **not** need a round open, a run in progress, or a journal folder to
*read*. It needs one to *record*, and a project without one gets the whole
walk-through plus a clear statement that there is nowhere to record answers
yet — **never an invented folder**. The engine says which of the two it hit.

**Offered, never auto-run, at the end of a writing-engine run.** One line in
the run summary:

```
9 flags remain (4 author, 3 data, 2 decision) and the package is NOT SENDABLE.
Run `flag-resolver` to answer them; they will be applied on the next round.
```

An offer and not a hand-off, because this is a conversation that can run to
twenty questions and it would otherwise arrive unannounced at the end of a run
the user started for something else. **It is not in any preset** and cannot be
added to one: a module list with a twenty-question conversation in the middle
of it makes the agent-call estimate a fiction.

## Reading the flags

Resolve `<tools>` in this order, and stop at the first hit:

1. `${CLAUDE_PLUGIN_ROOT}/tools/`, if the harness expands that variable
2. `../../tools/` relative to this skill's own directory (the skill is
   authored inside the toolkit at `skills/<name>/`, so this wins when it has
   not been synced out)
3. `tools/` under the toolkit root, if the pointer file that led you here named
   that root

If none of them resolves, say so and stop.

## First, Say Whether This Copy Is Behind

As soon as `<tools>` resolves, before any other command:

```bash
python <tools>/remote.py refresh
```

**Print whatever it prints, verbatim, then carry on in the same breath.**
Most of the time it prints nothing at all, and nothing is the intended
outcome. When it does print, the line says that the toolkit or the lab pack
is behind the repository it came from, or that whether it is behind is
UNKNOWN and why.

**This is the only command in the toolkit that reaches a network, and this is
the only moment it may run.** It asks GitHub at most once a day per
repository and gives up after six seconds; every other invocation reads an
answer already on disk. Nothing later in the session — no stage, no engine,
no draft — makes a network call for this, so a member working offline gets
the same run with one silent failure recorded at the start.

**It reports; it never updates, never blocks, never gates a stage and never
changes what you do next.** Do not offer to run `/plugin update` for them,
do not wait for a reply, and do not raise it again later in the session. Do not read the flags out of the
`.docx` by hand — the ids below are what make an answer survive to the next
round, and they come from the engine.

```bash
python <tools>/prose.py flags "<project>" --json
```

Every flag comes back with a stable `id` — hashed from the section, the type
and the normalized body, and **never from the location**. That is what lets an
answer recorded in r4 be matched to its flag in r5 after two paragraphs have
been merged and `results ¶3` has become `results ¶4`.

**There is no `flags.md` and there is not going to be one.** It was removed on
purpose (`system-changes.md` item 43) because it was a third copy of a list
that is already inline in the source text and again in the PAPER NOT COMPLETE
block. The live source text is not a workaround for it — it is the better
source: a snapshot re-asks flags the paper no longer has and is blind to flags
added since it was written, and *a ledger believed over the folder is worse
than no ledger*.

A **reworded** flag is a different flag and gets a different id. That is
correct rather than unfortunate: *"no thickness recorded"* and *"thickness for
the annealed series is not in the stats output"* are not certainly the same
question, and an answer carried silently across them is a lookup that was
almost right.

## The conversation

One flag at a time, in this order: `author` → `data` → `decision` → `conflict`
→ `stats` → `citation` → `journal`. Cheapest to answer first — a person
answers an `author` flag in ten seconds, and a walk-through that opens with
three of them earns their attention for the `decision` flag on question four.

Present five things per flag, and **the fourth is the one that is easy to leave
out and must not be**:

1. **The flag verbatim**, the sentence it sits in, and the paragraph around it.
   A flag read without its paragraph gets answered wrong.
2. **Which module raised it, and in which round.** A `stats` flag from r2 still
   standing in r5 is a different conversation from one raised yesterday.
3. **What is blocked by it.** Every flag blocks `submission-package`; some also
   block a specific check. Say which.
4. **What the self-resolution pass already tried**, from
   `reports/rN/prose_report.md` under `## Flags that survived the pass`. The
   user must not be asked to do work the agent already did — *"I searched
   Crossref and PubMed for this claim and found nothing"* changes the question
   from *find a citation* to *is this yours, or should it come out*. Where that
   record is absent — a resolver run whose last writer round has retired — say
   **no attempt recorded** rather than implying nothing was tried.
5. **Options** — concrete, drawn from the type, never more than four. They are
   proposals rather than a closed menu: free text is always accepted and is
   recorded verbatim.

```
Flag 3 of 9 — data — results ¶3

  "The coating was about 40 nm thick **[FLAG: data — 40 nm is your figure
  from drafts/rough_draft.md; it is not in analysis.md]**,
  which places it above the percolation threshold."

  Raised by draft-sections in r4. Blocks submission.
  The pass looked in analysis.md and methods_facts.yml and
  found no thickness for any condition.

  a  Give me the number and where it is recorded — it goes into
     methods_facts.yml and the sentence keeps it
  b  It was measured but is not in the analysis — I will record it as an
     author-supplied value, cited to where you say it lives
  c  It was never measured — the sentence becomes a limitation, in the
     limitations paragraph, and the number comes out
  d  Skip for now — stays flagged, and I will not re-ask unless you say --all

  Or tell me in your own words.
```

**Three options exist for every flag whatever its type**, and they are the
skill's whole shape: *answer it*, *it is a limitation*, or *defer with a
reason*.

### Options by type

| type | offer | never do |
|---|---|---|
| `author` | the fact, and where it is recorded so `methods_facts.yml` can hold it | accept "I'll add it later" as an answer — that is `defer`, and it is recorded as one |
| `data` | the value with a source; or a limitation; or the number comes out | write a number into the text yourself |
| `decision` | the two framings as the drafter saw them, and the consequence of each | rank them silently; if you recommend one, say why |
| `conflict` | what each source asked for, side by side, and who to ask | resolve it by picking the more recent edit |
| `stats` | the test the text implies vs. what the output holds; re-run, restate, or remove | propose a wording that makes an unsupported claim sound weaker but still unsupported |
| `citation` | what was searched and found nothing; a citation from the user; make it a claim of this work; or cut | accept a DOI without verifying it through `scholar.py` |
| `journal` | the requirement, its source URL and its date; ask, look, or assume-and-record | let an assumption go in unmarked |
| `citation-claim` | the sentence, the citekey, and the line of the source attribution-check quoted — then fix the sentence, change the citation, or keep both and say why the source does support it | decide from the title or the abstract which of the two is wrong; the quoted line is the evidence, and if it is not enough, retrieve more of the source |
| `edit-override` | the coauthor and their sentence, the sentence the engine wants instead, the rule behind it, and the context — then keep theirs, take the engine's, or write a third | present it as a defect in the coauthor's writing, or apply the engine's version because it is the one with a reason attached |
| `edit-intent` | the inserted text as the coauthor typed it, and the two readings: a sentence of the paper, or an instruction to rewrite that span | pick one because it is more likely |

**A `citation-claim` flag is never there by accident.** It exists only because `attribution-check` returned `contradicts` on a (sentence, source) pair AND the user said yes to writing it in — the module reports and never rewrites, so nothing else can put one there. Read the quoted source line out before the sentence: the question is not whether the sentence is *true*, it is whether **that source** says it, and the two ordinary answers are *the sentence overstates it* and *the citation is the wrong paper*.

**The `edit-override` row is the one this pipeline was missing.** A coauthor's
edit **stands** — it came from somebody who is not in this loop and will not
see the paper again for weeks — so nothing overrides it silently. But it is
not permanent either: the user may override it, and the flag exists so that
they are the one who does. Read the five parts out as the engine wrote them
and say whose they are by name, because *"JD put this edit: x, but we are now
thinking y will sound better"* is a decision somebody can actually make.

**When the answer keeps the coauthor's version, waive the rule that proposed
the change:**

```bash
python <tools>/learn.py waive <rule-id> --because "<the user's reason>" \
  --project "<project>" --round rN --section <section>
```

**Three independent refusals demote it** — counted as evidence is counted, so
three in one section of one round is one. A rule that keeps proposing to undo
colleagues' work is a rule this paper does not want.

**The `citation` row is the one with teeth.** A DOI the user types goes through
`scholar.py verify` before the answer is recorded, and a DOI that does not
resolve is not an answer. The pipeline applies that rule to its own searches
and the user is not exempt from it: a mistyped DOI in a reference list is a
retraction-grade error and it arrives most often by hand.

## Recording the answers

```bash
python <tools>/manuscript.py flag-answers "<project>" --journal X \
  --answer "7c1a4e=data — coating thickness is 40 nm, SEM 2026-03-14, notebook p.72" \
  --source RW
```

They land in `edits/edits_status.md` as rows with type `flag`, state `pending`.
That is the correct file rather than a convenient one, and every property the
job needs is one it already has:

- **§10.3 defines it as the single inbox for every external change request,
  whatever its origin.** An answered flag is a change request from a person,
  arriving by a different door.
- ***"applied on the next iteration of the writer"* is what `pending` already
  means** — every pending item is automatically in scope for the next round and
  the drafter is handed the pending list as a work order.
- ***"automatically check for the ones resolved on the next run"* is
  `final-check`'s existing duty**, and stronger than what was asked: once an
  item is `applied` it is protected, and every later round re-verifies that its
  substance is still there.
- **It does not retire.** `reports/rN/` does, in the same gesture that opens
  the round meant to consume the answers — which is exactly why the answers do
  not go there.

An answer whose flag id is no longer in the prose is reported **orphaned**. It
is not dropped and it is never guessed onto the nearest flag; it blocks
nothing. Name the answer, the flag it was written for, and the flags that now
exist, and ask.

## The refusal that makes the rest of it safe

**Do not edit the manuscript. Not one sentence, not even the flag you just
answered.** The temptation is obvious — the answer is right there and the fix
is one string replacement — and it is wrong three ways:

- **The source text has one writer.** `draft-sections` and `revise-prose`
  operate under the freeze, the preservation invariant and, from r2, the
  retention invariant. A second writer editing outside all three is precisely
  the hole those were built to close, and an especially cruel one because it
  edits the sections the user has just been thinking hardest about.
- **An answer is not prose.** *"It was 40 nm, from the March SEM"* has to become
  a sentence, and which sentence depends on the paragraph, the density budget,
  the voice and the ladder — which is `draft-sections`' job, with a brief this
  skill does not have.
- **The user asked for it this way**: the flags are resolved at the next
  iteration of the writer.

Close with what is true, including the part nobody wants to hear:

```
6 flags answered, 2 deferred, 1 declined.

Recorded in drafts/edits/edits_status.md as RW-F1..RW-F9, state
`pending`. Nothing in the manuscript has changed yet.

They are applied on the next writing-engine round — the "resolve the flags"
intent runs exactly this work and nothing else:

    writing-engine            (then choose: resolve the flags)

Until then the package stays NOT SENDABLE: an answered flag is still a flag.
```

**An answered flag still blocks submission and the closing block says so
plainly**, because the alternative is a user who believes they finished. The
gate is on the manuscript's text and the text still holds the flag. It comes
off when the writer applies the answer and the flag is gone from the prose —
the only observation that means the work was actually done.

## Re-running it

Expected to be run repeatedly, and a walk-through that starts from question one
every time is one nobody finishes.

- **Answered and not yet applied** — one summary line at the top, not re-asked.
- **Deferred** — skipped and counted in that line. `--all` brings them back,
  carrying the reason, so the second pass opens with *"you skipped this in r4
  because the notebook was at the lab"* rather than cold.
- **New since last time** — asked normally.
- **Gone since last time** — reported, never silently forgotten. A flag that
  vanished with no answer means somebody edited the text by hand, which is
  allowed and is worth one line.

## Things that are wrong to do here

- **Never invent a fact.** A `data` flag whose answer the user does not have
  stays a flag. *An outline proposed from nothing is a fabricated paper, and it
  reads exactly like a good one* — the same rule at the granularity of one
  number.
- **Never send anything anywhere.** Flag bodies quote unpublished prose.
- **Never write to `rules.yml` or the learned registry.** An answer to a flag is
  a fact about this paper, not a lesson about the user's style. Answering nine
  flags must teach the engine nothing.
- **Never write a `flags.md`.**
- **Never edit prose.**

## Isolated context

This skill needs none, and that is deliberate rather than an oversight. It is a
conversation with the user about their own paper: the context it needs is the
one the user is in, and it reads the same source text they are looking at.
Nothing here is a check whose verdict could be biased by knowing the
hypothesis — it never produces a verdict at all.

## When the Engine Itself Is Wrong

**File it. From this stage, not only from the writing one.** If the engine does
something during walking the outstanding flags that it cannot justify — refuses a valid input, reports
a number it cannot have measured, writes a file in the wrong place, exits 0 on
a check that never ran — that is a defect in the toolkit, and it is lost with
this session unless you record it:

```bash
python <tools>/manuscript.py log-issue "<project>" --stage flags     --code a_short_slug --detail "what happened, GENERICALLY"     --example "what happened on THIS project"     --title "one line" --wanted "what it should do"     --verify "how the next run answers 'is it still there?'"
```

Three things about this command, and each of them has cost something already:

- **`--stage flags`.** Unstated, the stage is inferred, and the inference is
  built around the writing stage's journal and round — both of which are empty
  here. The record marks an inferred stage as inferred, so a wrong guess is
  visible rather than silent, but saying it is better than being guessed at.
- **`--detail` is generic; `--example` is the one place a project specific may
  live.** `system-changes.md` is read by everyone who has the toolkit, so the
  description has to be true of every project this could happen on.
  Write *"the engine dropped a row from the table"*, not *"it dropped sample
  ZQx7"*. A sample id or a composition in `--detail`, `--title`, `--wanted` or
  `--verify` is **refused** by name; `--example` is never linted.
- **`--verify` is required and is not a formality.** An item the next run
  cannot check is an item nobody can ever close.

The project argument is positional and defaults to the current directory, so
this works before a project exists. `--list-codes` prints the codes the engine already files by itself —
reuse one if it fits. If you fixed it in the same session, add `--fixed`, and
`--detail` then says what you changed.

**Only engine misbehaviour goes here.** A missing file, an empty template, a
question the user has not answered yet are the project's business and are
already reported where they belong.
