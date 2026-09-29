# The Revision Stage

Reading what came back, and deciding what the round is for.

Part of the `writing-engine` skill. **`SKILL.md` is the spine and is read on
every invocation; this file is read when the round reaches this stage**, and
`manuscript.py handoff --stage revision` is what names it. Everything the spine
says still applies here - the `<tools>` resolution, the module isolation
contract, and **Things that are wrong to do here** are stated there once and
are not repeated in this file.

A `§N` in this file is a module number. `SKILL.md`'s **What to read, and
when** table says which file each module's section is in; the others beside
this one are `drafting.md`, `outline.md`, `prose-rules.md`, `submission.md`.

---

## 9. respond-to-reviewers

```bash
python <tools>/manuscript.py response "<project>" --journal IJROBP
```

It writes nothing new — it *renders* `edits_status.md`. Three rules keep it
honest, and the engine enforces all three:

- **Every reviewer point gets a response, including declined ones.** A declined
  point's recorded reason is the argument. It refuses to render while any point
  is still `pending` or `conflict`.
- **A point cannot be answered as done unless its item is `applied`.** Both the
  letter and the manuscript derive from one record, so the letter cannot claim a
  change the manuscript does not contain.
- **Line numbers come from the built revision**, resolved after the final
  assemble of that round, never before.

The submission file carries no line numbering unless the journal asks for it,
so build the line-numbered copy for yourself when you need to cite into it:

```bash
python <tools>/manuscript.py reference-doc "<project>" --journal IJROBP --line-numbers
python <tools>/manuscript.py assemble "<project>" --journal IJROBP
```

Then put the reference doc back (`reference-doc` with no flag) before the
submission build, or the file you upload differs from the one the journal
shows its authors. The rendered letter leaves a `**[FLAG: author]**` where the
revised text and its line number go; fill those from the line-numbered
`.docx`, then convert.


## Ingesting edits

`drafts/edits/` is one inbox for **every external change request**: coauthor
returns (`manuscript_r5_JV.docx`), reviewer and editor reports
(`reviewer_report_r5.pdf`), and the user's own marked-up file.

It sits beside `source_text/` and belongs to the **paper**, not to the journal:
a coauthor returns a manuscript to the folder they were pointed at last time,
and nobody outside the project knows which journal this round is aimed at. A
project that still has `drafts/<JOURNAL>/edits/` keeps using it — nothing moves
a user's inbox for them — so read the path the engine reports rather than
assuming either one.

```bash
python <tools>/manuscript.py ingest "<project>" --journal IJROBP
```

It reads documents through `docx_edits.py` and PDFs through `pypdf` — **never by
eye** — excludes OneDrive sync conflict copies (`*-DESKTOP-XXXX.docx`, which
would otherwise read one coauthor's edits twice), splits reviewer reports into
numbered points per reviewer, and writes `edits_status.md`.

**Attribution comes from the OOXML, not the filename.** A forwarded file named
for one coauthor routinely carries another's revisions, and the engine warns
when it does. An initial that is not in `authors.md` is asked about, never
guessed.

Disposition:

- **Tracked changes are applied almost always** — they are the collaborator's
  explicit intent. Exceptions: the change introduces a factual or statistical
  error, or two authors edited the same span differently.
- **Comments are instructions to address**, usually broader than a text swap.
  Each gets a disposition and a one-line response. None may be silently dropped.
- **Conflicts stop and ask.** Present what the source asked for, what the new
  pass wants to write, why, and a recommendation. Never resolve silently in
  either direction. Two reviewers asking for opposite changes is normal and gets
  the same treatment.

**The state machine is the part that matters.** `pending` → automatically in
scope for the next round; the drafter is handed the pending list as a work
order. `applied` → in the manuscript and now **protected**, re-verified by
final-check every subsequent round. `conflict` → needs the user, never
auto-resolved. `declined` → requires a recorded reason.

The `state` and `note` columns are the user's to edit; everything else is
rebuilt each round and matched on a fingerprint, so **a hand-marked state
survives regeneration and a row whose source file left the inbox is retained**.
A coauthor's or reviewer's change is never undone by a later automated pass.

### The two classes, and why the word "edits" needed splitting

`authority` is a column in the ledger and it is set from **where the file came
from**, never inferred later from what the text says.

| Class | Set when | Lifetime |
|---|---|---|
| `own` | the change came out of `submission/manuscript_rN.docx` — the user's own markup of what this round built | **this round only.** Applied, then spent. "By the time we get to r5, there is no history of r2" |
| `coauthor` | the file came out of `edits/` — a colleague's return, or a reviewer report | **standing**, until the paper is submitted |

Both were called "edits" and they have opposite lifetimes, so from the moment
they landed in one ledger nothing downstream could tell a spent markup from a
colleague's judgment. Now `ingest` reads the user's own tracked changes too —
they used to be counted for the round menu and recorded nowhere — and an `own`
row from an earlier round is **dropped rather than carried forward**, with the
count said out loud. A `coauthor` row never falls off.

**A reviewer report is `coauthor`.** The class is about standing, not about
friendliness.

**What is dropped is the prose of a spent round, never the lesson.** r5 owes
nothing to r2's wording; *"he cuts this hedge every time"* is a rule about the
user and `learn.py`'s layers keep it. The text is transient, the lesson is not.

### A replacement is kept; a request is carried out

- a **tracked** change is the words the coauthor wants — **text to keep**,
  applied verbatim, and guarded from then on;
- a **comment** is an instruction to rewrite — *"make this paragraph flow
  better"* — and it is a **licence** for exactly the pass the round would
  otherwise leave off, **scoped to what it is anchored to and nothing else**.
  Without that boundary "make this flow better" becomes a general warrant to
  restyle the section.

**When it cannot tell, it asks.** A tracked insertion whose text reads as an
instruction rather than as prose — *"reword this"* typed into the body — comes
back from `ingest` as a warning naming the row, and it is a
`**[FLAG: edit-intent]**` rather than a guess. Applied as written it becomes a
sentence of the paper; read as an instruction it licenses a rewrite, and the
two failures are both bad in different directions.

### Overriding a coauthor's edit — ask, never silently

A `coauthor` item is not immutable; it is **un-overridable without a
question**, which is a different and better thing. It keeps the engine from
quietly undoing a colleague's work, and it keeps the user from having to fight
the engine when the colleague was wrong.

The three passes that can reach a coauthor's sentence — `revise-prose`, the
comprehension fix and the quality fix — are each **handed the standing items
in their own brief**, with the author, the sentence, the location and the
round. A finding that lands on one of them stops before writing and raises a
`**[FLAG: edit-override]**` carrying five parts:

> **JD (Jane Doe)** put this edit: *"…"* — but we are now thinking *"…"* will
> read better, because *(the rule)*. It arrived in r3, and JD has three other
> items in the discussion.

**The engine's proposal does not land while the flag is open.** The coauthor's
text stays. `flag-resolver` walks it with the user and `submission-package`
refuses to ship a paper still carrying one, so nothing is lost by waiting.
When the user keeps the coauthor's version, record it against the rule that
proposed the change — `learn.py waive <rule> --because "…" --section <s>` —
because **three independent refusals demote it**: a rule that keeps proposing
to undo colleagues' work is a rule this paper does not want.

### A colleague's requests come before the engine's own goals

A PI's or coauthor's explicit request — *call it X, explain Y, move this* — is
a **required change**, not one input among the readability passes. Every pass
brief says so, and every request ends the round with one of three outcomes in
the pass report's `## Coauthor Requests`: **applied**, **declined with a
reason**, or **flagged** to the author. `completeness` names each unanswered
row by id, and a row set `declined` with no reason in its note is **blocking**:
for a colleague the reason is the answer.

**A request to move a passage is a move, and no prose pass can make it.**
*"This belongs in Results"* takes text out of one section and puts it in
another; every pass is checked per section, so a passage leaving Methods drops
Methods' numbers and the pass is restored. The pass lists it under
`## Moves Requested`, and you carry it out before the next pass:

```bash
python <tools>/manuscript.py move-passage "<project>" --text "<the passage, as it stands>" --to results --edit KW-04 --journal X
```

It moves the words verbatim, numbers and citekeys with them, refuses a passage
it cannot find exactly once, puts it at the end of the section or `--under` a
subheading or `--after` a paragraph, and marks the ledger row applied.

**A coauthor's inserted sentence is frozen.** Transitions and framing sentences
are what a coauthor adds to make the argument flow, and they are exactly what
`open on the point` and *cut the lead-in* would delete. They stay verbatim and
in place, including ahead of the claim they set up, and a pass lists any it
touched under `## Coauthor Text Touched`. `completeness` **blocks** when an
applied coauthor insertion is gone from the source text with no decision
recorded.

**Author decisions bind every pass.** An entry in `custom_instructions` is a
decision about the paper, and a pass may not rewrite toward the alternative it
rules out — not even as the first reading of a correct sentence. Each pass
report carries `## Decisions Checked`; `assemble` warns when one does not.

### Importing a manuscript from a `.docx` brings its comments with it

Prose imported from a `.docx` arrives with whatever the people who marked it
up asked for. Import it with the file named:

```bash
python <tools>/manuscript.py import-prose "<project>" --journal X --from-docx "<the file>.docx"
```

It copies the file into the inbox as that author's return and runs `ingest`,
so every **comment** becomes a pending coauthor request with its anchor, and
every **tracked change** — already accepted into the imported text — becomes
an applied coauthor row, which puts it under the coauthor guard every pass
reads. A long insertion that is *not* in the imported text is named, not
assumed. Whose file it is comes from the revision authors resolved against
`authors.md`; when none resolves, it refuses and asks for `--as <initials>`.
Converting a `.docx` to `source_text/` without this drops every comment in it
silently.

## The `**[FLAG: ...]**` convention

Anything a module cannot resolve is written **into the text at the point of the
problem**, in bold:

```markdown
The thermophilic arrays were more ordered than their mesophilic counterparts
**[FLAG: stats — text says "more ordered", analysis.md shows
p = 0.31 for this comparison]**, consistent with the lattice model.
```

Types: `citation`, `stats`, `data`, `decision`, `author`, `conflict`, `journal`.

**Raise flags freely while drafting, then run the self-resolution pass** before
anything reaches the user: re-search PubMed for the missing citation, re-read
the stats output, re-check the requirements page.

### The one test every flag has to pass

**Does resolving this require a fact only a human has?**

If no, resolve it yourself and write the prose. That is the whole rule, and it
replaces the old one — "the residue is typically `author`, `decision`,
`conflict`, and `data` where the experiment was not run" — which was the wrong
test and let this through:

> **[FLAG: data — the low-salt condition is the exception: only one
> preparation was obtained, after several failed attempts at a replicate, so
> this condition has n = 1. See the Results.]**

Read it as a question: it does not ask one. It states the condition, the
history, the resulting n, and where the evidence lives. Nothing about it is
outstanding. It is a **limitation, fully known**, wearing the marker that
means *a human must answer this* — and it got there because n = 1 after
failed replicates is literally "the experiment was not run", so the old rule
read it as unresolvable and stopped.

**That example is deliberately fictional, and the reason is worth one line.**
The version that stood here quoted a real sample id and a real failure count
out of one of this group's own projects, and quoting a project's facts in the
toolkit's documentation is a contamination route into every future run of that
project: the orchestrator reads this file every round, so the number lands in
its context and can be passed into a drafter's brief as though it had been
recorded. It had not been. **Examples in this file name nothing real.**

The question is not *was the experiment run*. It is *does anyone need to tell
me anything*. Here nobody does, and the fix is a sentence of prose — which is
what a reviewer expects to read anyway. **A limitation stated in the text is a
paper being honest; the same words in bold brackets are a paper that is not
finished.**

So, at the self-resolution pass:

- **A known limitation becomes prose.** n = 1, a failed replicate, an
  instrument that was down, a condition that could not be run — write the
  sentence.
- **A knowable requirement becomes a lookup.** Re-read the guidelines page.
- **An acquisition value becomes a harvest.** Run
  `scaffold.py harvest "<project>"` before any `[FLAG]` naming an instrument
  setting is allowed to survive. Most instruments write their own settings
  into every file they save, and the round that this rule came from shipped
  three author flags for values that were sitting in 158 image files — one of
  which had *two* answers. If the harvest reports it, the flag becomes prose;
  if the harvest reports two values, the prose names both with their counts.
- **A missing citation becomes a search.** `scholar.py`, verified, as always.
- **`data` is not a type that survives by default.** Split it three ways: a
  number nothing produced and nobody can produce is prose about a limitation;
  a number that exists somewhere you have not looked is a lookup; only a
  number **the user must supply** stays a flag.
- **A flag whose own body states its answer is a defect** — no "unknown", no
  "not recorded", no "which?" — and the pass should catch it on its own text
  before anyone else does.

**A surviving flag is written as a question addressed to a person**, so the
test is visible in the output:
`**[FLAG: author — which of the two Cu loadings should Table 2 report?]**`,
not a statement of something you already know.

**Resolved limitations go to one place.** Where a flag becomes a limitation,
it goes in the limitations paragraph (or the one place the journal's structure
puts them), so the paper's limitations read together rather than scattered
through the sections as former flags.

Every flag is a claim on the user's attention and a block on
`submission-package`. A flag that needs nothing devalues the ones that do, and
58 outstanding items is a number nobody reads to the end of.

```bash
python <tools>/prose.py flags "<project>" --json
```

It writes nothing. The flags are already in two places anybody reads — inline
in the source text, and again in the PAPER NOT COMPLETE block of the built
`.docx` — and a `reports/rN/flags.md` was a third copy to keep in step
(item 43).

Bold so they survive into the `.docx` and cannot be missed by a coauthor
skimming it — a PI can often answer an `author` flag in ten seconds. `assemble`
builds with flags present and says how many; `submission-package` writes the
package anyway and reports it **NOT SENDABLE** while any remain.

**Offer `flag-resolver` at the end of a run that leaves flags standing** - one
line in the summary, and an offer rather than a hand-off, because it is a
conversation that can run to twenty questions and would otherwise arrive
unannounced at the end of a run the user started for something else:

```
9 flags remain (4 author, 3 data, 2 decision) and the package is NOT SENDABLE.
Run `flag-resolver` to answer them; they will be applied on the next round.
```

It is **not in any preset** and cannot be added to one with `+flag-resolver`:
a module list with a twenty-question conversation in the middle of it makes
the agent-call estimate a fiction.

**Answers already recorded show up from this side too.** `plan` and
`completeness` report *N flag answers recorded and not yet applied*, so a user
who answered nine flags and then did not run the writer for two weeks is told
by the next thing they run. Applying them is the `resolve the flags` intent,
which runs exactly that work and nothing else - and an answered flag is still
a flag until the writer applies it and the marker is gone from the prose.

## What is this round for? — ask it on every rerun

**Asked when `project.yml:revision` >= 2, and never at r1.** There is no *why
are you running this again* on the round that has nothing to run again, and
the r1 block is unchanged.

Without it, the shape of a rerun is assembled from four remembered values and
one typed word - the preset, the sticky scope, the standing adherence, the
source weights - and every one of them is a good answer to a question nobody
asked. The thing the user actually has in their head - *the PI sent back a
marked-up docx and I want those changes in* - was never asked for, never
recorded, and is not derivable from any of the five.

```
This is r4. What is this round for?
  1  apply the changes         - 12 items pending in edits_status.md, plus 9
                                 tracked changes in submission/manuscript_r3.docx
  2  finish citations          - 6 citekeys unresolved, 2 [FLAG: citation]
  3  resolve the flags         - 4 author, 3 data, 2 decision
  4  substantial rearrangement - an offer, not a count; the answer that
                                 REDRAFTS paragraphs already written, from
                                 the outline
  5  polish the prose that is  - 5 sections with prose to read better:
     already there               title_abstract, introduction, methods,
                                 results, discussion
  6  keep drafting             - 1 section still empty (discussion)
  7  something else            - say it in your own words
```

**Every count is derived**, from things that already compute them, and nothing
is stored: a project that gains a flag moves one number and not the other.

**An option whose count is zero is listed LAST, marked `(nothing found)`, and
is never dropped.** That is worth the extra line: a user who came to apply a
coauthor's edits and is shown a menu with no "apply the changes" on it
concludes the engine found them and folded them into something else. Hiding it
makes *"there is nothing to apply"* indistinguishable from *"this engine does
not do that"*, and the first is a fact about their project they need. Picking
it anyway is not refused - it runs and reports having found nothing, because
the count measures one place and *"the edits are in the file I just saved"* has
been true before.

### And what each step might override

The block carries a second half, because from r2 on the prose has the user's
and their coauthors' work in it and a module list does not say what is at
risk:

```
These steps would run. Each says what it may rewrite:
  revise-prose           rewrites sentences for the house rules
                         may overwrite: engine prose only - methods is yours
                         and is skipped. Never a coauthor edit without
                         asking (JV, 4 items)
  comprehension-check    reads the paper cold and reports where it lost the
                         thread
                         may overwrite: nothing - it reports
  revise-prose --from-comprehension
                         rewrites only the sentences that check named
                         may overwrite: engine prose …
                         OFFERED at r2+, not automatic
```

**The override line names the actual collision** — the initials and the count
read off this project's own ledger, the frozen sections read off the
provenance — not *"may rewrite prose"* in the abstract. The user is deciding
about their own paper, not reading a manual. A step that would rewrite nothing
stays listed and says so, for the same reason a zero-count option does.

**Ask it before the plan block, because it decides the plan block.** The
sequence is intent → the plan block the intent produced → *Go?*. Still one
confirmation; what is being confirmed is now a pipeline derived from a stated
purpose.

| intent | what it sets | must not |
|---|---|---|
| `apply` | the `revision` preset minus respond-to-reviewers unless there are reviewer or editor rows; scope from the pending rows | recompose a section no edit asked it to touch |
| `citations` | citation-check → the resolution pass → assemble | change a sentence that is not a citation or its flag |
| `flags` | the resolution pass over recorded answers → draft-sections in edit mode → assemble | touch an unanswered flag |
| `rearrange` | outline → draft-sections → the full check set, adherence **asked and generative** | rewrite a section it did not name |
| `polish` | the standing preset, unchanged; every written section becomes `polish` mode instead of `edit`, so first-pass revise-prose reads it | redraft a section from the outline, or change what a sentence claims |
| `keep drafting` | the standing preset, scoped to the empty sections | touch a section that already has prose |
| `something else` | the standing preset, unchanged; the sentence recorded verbatim | — |

**The `must not` column reaches the agent, not only the terminal.** It is in
the brief of every module the round runs that writes prose, in those words,
with the line that it is what the user approved. A promise rendered to the
user and enforced nowhere is worse than no promise, because the user stops
checking.

**An `apply` round MAY change prose**, and the constraint says what it may not
touch rather than forbidding the work. A coauthor's comment asking a paragraph
to flow better *is* a request to recompose, and a round forbidden to recompose
cannot honour the edits it exists to apply — *"it CAN make changes, but it
should prioritize the user edits over anything else."* What it must not do is
recompose a section no edit asked about.

**`citations` NARROWS the round; it does not add a sentence to a brief.**
"Finish the citations" handed to a drafting agent with the whole section in
front of it produces a section that is better in four other ways nobody asked
for, and the diff is then unreviewable.

**No intent but `rearrange` may REDRAFT a section that has prose**, at any
adherence level, and `rearrange` still names its sections through `--redraft`.

**`polish` is the other door and it is not that one.** A project that imports
a manuscript — a member's own earlier draft, a `.docx` from another engine —
has prose in every section, so every section reads `edit` and first-pass
`revise-prose` skips all of them. Before `polish` existed the only neighbouring
answer was `rearrange`, which means *redraft from the plan*: the wrong gesture
entirely for prose somebody has already written and wants **read better**. So a
readability-and-structure round over an imported paper had no first pass at
all, and only the two bounded passes could reach it — which is how the gap
stayed invisible, because something ran and the round looked done.

Answer `polish` and every written section becomes `polish` mode: `revise-prose`
runs over it, the brief says beside each section name that **a polish section
is not a redraft**, and the paragraphs, their order and what each one claims
stay the author's. A frozen section is still skipped, and the preservation
invariant — the numeric and citekey multisets, the verbatim flags, the
snapshot restore — is unchanged. When the manuscript came out of a `.docx`,
import it with `--from-docx` (above) so its comments and tracked changes come
with it.

**Prose that came from outside this pipeline is recorded, not faked.** Drop the
sections into `source_text/` and run

```bash
python <tools>/manuscript.py import-prose "<project>" --journal X
```

which takes the hash of record now and writes `written_by: import`. The
sections then read **engine-owned**, so a polish round may reach them, and the
ledger says honestly that no module composed them. The gesture it replaces is
writing `written_by: draft-sections` into the provenance by hand, which is a
claim the next reader cannot check. A section edited by hand afterwards reads
**frozen** again, exactly as engine prose does — which is the point of
recording a hash rather than a claim.
More than one intent is fine; the modules run in **pipeline order, never the
order typed** - citations verified against prose a tracked change is about to
replace is work thrown away. `rearrange` plus anything else is accepted and
**warned once** on the overlap.

```bash
python <tools>/manuscript.py plan "<project>" --journal X --intent apply --intent citations
```

The answer goes to `round_state.json` and **opens the `drafts/log.md` entry** -
that first clause is the line every good log entry has and no generated entry
could produce, because the file recorded what was done and never why.

### And Where the Paper Now Differs From the Plan — Ask, Never Apply

**The planning files are checked on r1 only.** `plan/outline.md`,
`plan/captions.md` and `data/analysis/` are what the first draft is written
*from*, so on r1 a gap between them and the prose is work. From r2 on the
paper has been read and edited, and the same comparison is a list of places
where it has moved away from the plan — usually on purpose. Treating that list
as work is how a round undid its author's edits: a paragraph the author cut
still had an outline line, so the drafter put it back.

So on r2+ `completeness` does not list those findings as outstanding. Each one
becomes a **proposal with an id**, and you put them to the user after the
intent question and before the plan block:

```bash
python <tools>/manuscript.py source-review "<project>" --journal X --json
```

Ask with a **multi-select** — one option per proposal, its detail and file as
the description, nothing pre-selected. More than four proposals: group them
by file (outline / captions / analysis) and ask per group, then per item only
inside a group the user wants. Record the answer:

```bash
python <tools>/manuscript.py source-review "<project>" --journal X --accept sr-1a2b3c4d,sr-5e6f7a8b --decline all
```

`--decline all` after an `--accept` declines everything not accepted. An id
the round did not produce is refused by name. Answers are **per round** — r4
asks again about whatever still differs.

**Only accepted proposals reach the drafter.** The `draft-sections` brief on
r2+ says the planning files are not re-applied to EDIT sections, drops the r1
permission to *add a paragraph for an uncovered outline line*, and lists the
accepted ids as the round's only work from the plan. An empty list is not an
error: "nothing is taken from the plan this round" is a normal answer.

**`rearrange` is the exception, and it is the user's.** Answering it means
*redraft these sections from the outline*, which is the plan applied on
purpose; it still names its sections through `--redraft`, and nothing else
about it changes.

### Offer the round bump here, and let `round` do it

If `submission/manuscript_rN.docx` exists for the open round, this round is
finished and the next one has not been opened. **`plan` detects and offers; it
moves nothing** - the counter has exactly one mover and it is `manuscript.py
round`. Asking *what is this round for* about a round that shipped last week is
the same defect wearing a friendlier face, and without the offer the engine
drafts into `source_text_rN/`, overwrites `reports/rN/`, and is only stopped at
`assemble` by the overwrite guard - after the prose has been edited.

Declining is real and is the `--same-round` case: r3 was built but never sent.
Say what is being kept, and let the overwrite guard catch it if that turns out
to be wrong.

**"Start over" is deliberately not an option.** Wiping is `--redraft all` or
emptying the folder, both of which carry their own confirmation and their own
learning pass. A menu entry is a much lighter gesture than what it would do.
