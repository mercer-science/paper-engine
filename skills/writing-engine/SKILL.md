---
name: writing-engine
description: Turn a project folder into a journal-formatted submission, and keep turning revisions of it - proposing or revising the paragraph outline, drafting sections from it, checking evidence and statistics blind, checking that every figure, panel, table and reference is called out and in order, holding the paper to the journal's word and float limits, assembling the .docx, ingesting coauthor and reviewer edits, and writing the response letter. Use when the user wants to draft, revise, assemble, format, or submit a paper, build or change plan/outline.md, check its citations, statistics, cross-references or length, drop uncited references, ingest tracked changes, respond to reviewers, prepare a submission package, or resume a run that was interrupted part-way. Runs after data collection and figure curation; the outline can be built here if there is not one.
---

# writing-engine

Runs after the data and the floats exist. Turns the plan folder into a
submission, and then keeps turning revisions of it.

`plan/outline.md` is the claims ledger everything downstream reads. It does
**not** have to exist first: if there is none, this skill offers to propose one
from what the project has, shows it to you, and writes it only once you agree.
An outline that no longer fits the data can be revised the same way.

Spec: `specs/writing-engine.md`. Read it before changing behaviour here — it
carries the reasoning, and the reasoning is what stops a settled choice being
re-litigated.

**Two things make this skill different from the other two, and everything
follows from them:**

1. **It is a pipeline of isolated agents, not one long conversation.** Seven
   modules run as their own sub-agent with an explicit input allow-list, and
   **you do not write their prompts** — `manuscript.py agent-brief` builds each
   one from the allow-list and you hand it over verbatim. That is partly token
   economy and mostly bias isolation: the module that checks whether the
   statistics are overstated must not know what the hypothesis is. A prompt you
   compose yourself is composed by something holding everything the denied list
   is about, which is how the hypothesis gets back in.
2. **It is modular and re-entrant.** Almost never does the whole pipeline need
   to run. The user picks the modules, and **you state the plan before executing
   it.**

## The two layers

| Engine | What it does |
|---|---|
| `<tools>/manuscript.py` | `init`, `status`, `outline`, `plan`, `run`, `round`, `assemble`, `bib`, `ris`, `ingest`, `response` — journal state, the claims ledger, run progress, round snapshots, the Word build, the bibliography, the edit ledger |
| `<tools>/prose.py` | `density`, `outline`, `flags`, `citekeys`, `captions`, `crossrefs`, `length`, `numbers` — every rule with a countable answer |
| `<tools>/pubmed.py` | `search`, `sections`, `pdf` — PubMed discovery, tiered reading, and the only route to full text |
| `<tools>/scholar.py` | `verify`, `check-refs`, `cite`, `search`, `related`, `cited-by`, `compound` — discovery, verification and citation formatting across PubMed + Crossref + OpenAlex + Europe PMC + arXiv, with Semantic Scholar behind `related`/`cited-by`. **Search and verify through this one**: a *Surface Science* paper has no PMID and PubMed alone reports it `not_found` |
| `<tools>/docx_edits.py` | `extract`, `plain` — tracked changes and comments out of a `.docx` (called by `ingest`) |

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
do not wait for a reply, and do not raise it again later in the session. `python` here means whatever interpreter is on PATH —
`python3` on Linux and macOS. Only `assemble` needs anything beyond a bare
Python install (pandoc); see `AGENTS.md`.

**Never do an engine's job by hand.** Do not build the `.docx` with your own
pandoc line — `assemble` carries four gates you would not reproduce. Do not
count numbers by eye. Do not hand-write `edits_status.md`; regenerating it is
what protects an applied edit, and a hand-written row loses its fingerprint.

**No module edits a `.docx`.** Word files are outputs. The only exception is
the inbox at `drafts/edits/`, where they are inputs and are read through
`docx_edits.py`.

## What to read, and when

**This file is the spine and it is the whole of what every invocation needs**:
how to start, how to state the plan, the module registry and the isolation
contract, clearing the context, and what is wrong to do here. The rest of the skill is in
`reference/`, one file per stage, and **you read the one the stage you are
entering needs and not the others**.

| stage | file | what is in it |
|---|---|---|
| outline | `reference/outline.md` | there is no outline; changing one that no longer fits; outline adherence; modules **0 outline**, **0b literature-landscape** |
| drafting | `reference/drafting.md` | telling the drafter what you want; the two drafting modes; hook the reader; the number-vomit rule; the retention invariant; modules **0c learn-from-edits**, **1 draft-sections** (and **1b abstract** inside it), **1c revise-prose**, **1d comprehension-check**, **1e quality-check** |
| submission | `reference/submission.md` | journal requirements; the length budget; captions and callouts; every reference cited; the author files; modules **2 evidence-check**, **3 stats-check**, **3r attribution-check**, **4 assemble**, **5 citation-check**, **6 reviewer-check**, **7 final-check**, **8 submission-package**, **8b ai-disclosure**, **8c submission-names**, **10 toc-graphic** |
| revision | `reference/revision.md` | ingesting edits; the FLAG convention; what this round is for; modules **0 ingest**, **9 respond-to-reviewers**. Read on a revision round only — `learn-from-edits` runs in every round and is in `drafting.md`, beside the brief it writes |
| the digest | `reference/prose-rules.md` | `writing_guides/writing_rules.md`, its two stages, and regenerating it |
| rounds | `reference/rounds.md` | opening and retiring a round, and what retires with it; the guard on a `manuscript_rN.docx` somebody has already edited; picking up an interrupted run. **Read it when `status` reports a built round or an open run** — the two fields that say so are four paragraphs down. *Clearing the context stayed in this file*: it fires during any run, and a context that has just been cleared is the one case that cannot rely on having read a stage file |
| review | `reference/review.md` | **only when `project.yml:paper_kind` reads `review`** — the corpus as the review's data, the tier rule at layer 0, what review mode releases and what it tightens instead, the section stems, the input ladder, the corpus commands |

A `§N` anywhere in this skill is a module number, and the table says which
file that module's section is in. The files sit beside this one, so they
resolve from this file's own directory whichever way `<tools>` resolved.

`manuscript.py handoff --stage <stage> --json` names the files and the
headings for a stage without your having to work it out, and it is what a
fresh context is handed when the round crosses a boundary - see **Clearing the
context mid-run** below.

**Why it is split at all.** This file was 219 KB in one piece, larger than the
whole engine payload of a full submission round, and a harness reads a skill's
body when the skill is INVOKED - so a cleared context re-paid for all of it
before it could be told which part it needed. There is no arrangement of
`handoff` that avoids that; the file has to be enterable in parts first
(specs/context-compaction.md 3).


## Start every invocation here

```bash
python <tools>/manuscript.py status "<project>" --json
python <tools>/scaffold.py   survey "<project>" --json   # once per project
```

The second one is cheap and is **asked once per project, not once per round**:
it finds folders outside the structure that could feed the paper, and an
answer recorded once never comes back. See "Directories the Tree Does Not
Explain" below; if `unexpected` is empty, say nothing about it.

`status` reports the journals already targeted, the round, which sections are still
stubs, **whether there is an outline and whether an earlier run was
interrupted**, the float count and whether any is mock, whether the stats
output exists, and how many journal requirements are still `unknown`. **Do not
ask for anything it already reports.**

Four of those fields change what you do next, before anything else:

- **`round` is 1 or more and a `manuscript_rN.docx` exists** — this project has
  been built before, so **you are not starting; you are continuing.** Read
  "A round that already exists" in `reference/rounds.md` *before* planning
  anything. Rebuilding
  overwrites that file.
- **`run.open` is true** — a previous run stopped part-way. Do not start over.
  Read "Picking up an interrupted run" in `reference/rounds.md` and resume
  from `run.next`.
- **`outline.state` is `missing` or `empty`** — there is no claims ledger.
  Read "When there is no outline" in `reference/outline.md`. Do not draft
  around the absence, and
  do not invent one silently. **Ask whether one exists outside the project
  before you propose one** — the folder cannot tell "never written" apart from
  "kept somewhere else", and only the user can.
- **`outline.proposed` is true** — the ledger in place is one *you* wrote and
  nobody has accepted. It is usable, and every verdict you report off it says
  so until the user runs `outline --accept`.

Then, if this is a new journal:

> What journal are you targeting? (Name or abbreviation — e.g. *IJROBP*,
> *JACS*, *Nature Communications*.)

Asked **once per project**. Changing journals later is an explicit
`--journal <name>` and is a real event with consequences (§4.3 of the spec).

```bash
python <tools>/manuscript.py init "<project>" --journal IJROBP
```

Pass **the journal's own abbreviation**, uppercase — `IJROBP`, `JACS`,
`Nat_Commun`. Abbreviating "Nature Communications" is knowledge, not string
manipulation, so the engine only cleans what you give it. Journal folders keep
their conventional capitalization on purpose; `drafts/ijrobp/` looks wrong to
every coauthor who opens the folder.

`init` also writes `project.yml:target_journal` and
`plan/theme/journal_target.yml`. **Say this out loud to the user:** picking a
journal changes the figure widths, DPI, and base font size, so
`plan/render_all.R` must be re-run before assembling. Offer to run it.

**When the journal is a SECOND one, `init` offers to retire the first.** It
appends an `offer` action naming the old folder and the command; it moves
nothing, because routing offers and never runs, and this one moves a directory
tree. Pass the offer on in one line and let the user decide:

```bash
python <tools>/manuscript.py retire-journal "<project>" --journal LANGMUIR --to JPCC --dry-run
```

`drafts/LANGMUIR/` goes to `obsolete/drafts/LANGMUIR/`, whole — nothing
renamed, nothing filtered, nothing deleted — so `drafts/` holds one live
journal folder, one `source_text_rN/`, `references.bib` and `log.md`. Run it
with `--dry-run` first and show the user the action list.

It **refuses** rather than burying anything that is still standing: an open
run, a coauthor's edit in `edits_status.md` that has not been applied (use
`--to <NEW>` and the ledger follows the paper, because a coauthor's edit
stands until the paper is SUBMITTED and a rejection is not a submission), a
per-journal `log.md` whose merge into `drafts/log.md` could not be verified,
and a file whose digest changed between the hash it took before the move and
the one it takes after. **The rounds are not renumbered**: r1–r7 belonged to
that journal and `project.yml:rounds` still says so, which is also what keeps
the next journal opening at r8 with the folder gone.

**`init` adapts to whatever the folder is, and creates what is missing.** A
bare directory gets `drafts/source_text_r1/`, `drafts/references.bib` and a
minimal `project.yml`; it does *not* get `plan/` or `data/`. So do not run
`setup-project-directory` first just to make `init` work — run it when the
user wants the float pipeline and the analysis side, which is a different
question. Read the `actions` array back: `create`, `keep` and `skip` each mean
something, and a `skip` says why.

Three things `init` refuses, and none is worked around — each is telling you
something about the folder:

- **a path inside another project** — it names the real root; use that.
- **a populated `source_text/` at the top level, outside `drafts/`** — the
  right layout in the wrong place, which is what an older hand-built run
  leaves. Creating the second one would leave the writing in the first and
  build the `.docx` from the second. **Ask the user before moving anything**,
  then move `source_text/` and `references.bib` into `drafts/` and re-run.
- **a path that is not a directory.**

### The source text folder carries the round it holds

It is `drafts/source_text_rN/`, never a bare `source_text/`, and N is
`project.yml:revision` — the same project-wide counter the manuscripts are
numbered from. **Read the folder name out of `status`
(`res["source_text"]`) rather than typing `source_text/`**; every path you
give the user has to be one they can open.

The counter runs across journals, so LANGMUIR can stop at r7 while a JPCC
opened beside it starts at r8. `manuscript_r7.docx` sitting next to
`source_text_r8/` is the whole point: it says, without opening either, that
what went to LANGMUIR is not what would build today.

`round` and `init` rename it forward, and nothing else does. If you see
`NOT BUILT FROM: source_text_r5/` in `status` or in an `init`/`round` action
list, an older folder still holds prose that no build reads — tell the user
which one, and ask before merging or deleting anything. Two labelled folders
are *not* refused: r8 is unambiguously later than r5, so keeping the earlier
one as a record is a legitimate thing to do, and the engine's job is only to
make sure nobody thinks it is being built from.

### Is this a review paper? Ask once, and record it

```yaml
# project.yml
paper_kind: review        # research | review.  Absent reads as `research`
review_kind: narrative    # narrative | systematic | scoping | tutorial
```

**Absent reads as `research`, and it is not `research_type`.** `research_type`
is what a paper is *about* — `surface_science`, `structural_bio`;
`paper_kind` is what it *is*. There are surface-science reviews and
surface-science research papers, and their prose is not the same prose.

```bash
python <tools>/manuscript.py config "<project>" --journal Langmuir \
    --paper-kind review --review-kind narrative
```

That is a real event and is reported as one: the section stems, the layer-0
rule, the module list, the density budget and the managed scopes all change
with it.

**The rest of review mode is in `reference/review.md`** — the corpus as the
review's data, the tier rule at layer 0, what review mode releases and what
it tightens instead, the section stems, the input ladder, and the corpus
commands. **Read it as soon as `paper_kind` reads `review`**, and not
otherwise: on a research paper none of it applies.

### Ask what the project actually holds

Some projects keep only the manuscript: the figures and the statistics live on
a shared drive, in another repository, or on a collaborator's machine. If
`status` shows no `plan/` or no `data/analysis/`, **ask once** rather than
reporting them as gaps forever:

> This project has no `plan/` or `data/`. Are the figures and statistics
> managed outside it, or should I scaffold those too?

If they are managed elsewhere, record it — the answer persists, so this is
asked once per project and never again:

```bash
python <tools>/manuscript.py config "<project>" --journal IJROBP --manages none
# or a subset:  --manages outline,floats
```

Anything not listed is reported **out of scope** instead of as a gap, and
stops appearing in the PAPER NOT COMPLETE block inside the `.docx`. What was
dropped still travels with every verdict — `completeness`'s summary, `plan`'s
approval line, and the block itself all name it — so **never present a clean
result on such a project as "the paper checks out"**. Say what was not
checked. `plan` hands you `out_of_scope`; use it.

Do not guess this from the folder. "No `plan/outline.md`" and "the outline
lives on the group drive" look identical on disk, and one of them is a broken
scaffold.

### Directories the Tree Does Not Explain — Ask Once

The structure is a contract, and a real project folder routinely holds things
outside it: a `notes/` folder of lab-notebook exports, a `protocols/` folder,
a `supporting/` folder somebody made for the last submission. **Some of that is
writable material and this engine would never otherwise see it.**

Run the survey once per project, at the start, in the same breath as `status`:

```bash
python <tools>/scaffold.py survey "<project>" --json
```

Each entry under `unexpected` carries what it is and what it could feed:

```json
{"path": "notes", "kind": "dir", "files": 14,
 "extensions": {".md": 14},
 "useful_for": ["drafting"],
 "why": "notes - they stay where they are, and writing-engine can be told to read them"}
```

**Ask about the ones whose `useful_for` is non-empty. Say nothing about the
rest.** A folder of `.zip` archives has an empty `useful_for`, and mentioning it
is noise — the survey exists to find the notes folder, not to audit the drive.

> There is a `notes/` folder here with 14 markdown files in it, outside the
> project structure. Want me to read it as background when drafting?

One question per entry, one line each, and **take no for an answer.** Then
record it, whichever way they answered:

```bash
python <tools>/scaffold.py survey "<project>" --use notes --as drafting --json
python <tools>/scaffold.py survey "<project>" --ignore old_submission --json
```

`--as` is one of `drafting`, `citations`, `data`, `floats`, `analysis`,
`methods`, `ignore`. The answer lands in `project.yml:extra_sources` and the
folder stops coming back as a question. **Recording the no matters as much as
recording the yes** — an unanswered folder is asked about on every round, and
a skill that asks the same question every round is a skill people stop reading.

#### What Each Role Lets the Source Do

| `use` | What it may feed | What it may never do |
|---|---|---|
| `drafting` | Background for the drafter, at the same tier as `plan/README.md` | It is **not** `drafts/rough_draft.md`. It does not become the highest source of truth for any section, and nothing in it is copied into the draft as prose |
| `citations` | Papers for the literature work and for `references.bib` | A citekey still has to verify through `scholar.py`. A PDF in a folder is not a verified reference |
| `data` | Named to the user and to the `analysis` skill | **No number in it reaches the paper.** Every number traces to `data/analysis/analysis.md`, and a spreadsheet in a side folder is not that file |
| `floats` | Images available as panel art for a figure folder | Nothing is moved or rendered automatically |
| `analysis` | Scripts the `analysis` skill may read | Same as `data`: nothing it prints is a number the paper may carry |
| `methods` | Facts for `data/methods_facts.yml`, copied by hand and confirmed | A protocol PDF is not a methods paragraph |
| `ignore` | Nothing | It is never mentioned again |

#### A `drafting` Source Carries `plan/README.md`'s Denial With It

This is the part that is easy to get wrong, and getting it wrong is silent.
`plan/README.md` is denied to `stats-check`, `abstract`, `revise-prose`,
`reviewer-check` and `attribution-check`, because those modules work blind and
a folder of the author's own thinking about what the result means is exactly
what would un-blind them.

**An `extra_sources` entry marked `drafting` is denied to the same five
modules, for the same reason.** Never quote one into a prompt for any of them
and never summarise one for them either. A `notes/` folder is more likely to
carry "the thermophile looks tighter, as expected" than `plan/README.md` is,
not less.

#### A Recorded Source That Has Gone

`survey` reports `exists: false` on any recorded entry whose folder has since
been renamed or deleted. Say so and offer to drop the entry; do not read
around it and do not treat the gap as content. A round that quietly drafts
without a source it was told to use has changed what the paper is written from
and reported nothing.

### `data/analysis/analysis.md` — the numbers, and the only place they come from

**It is generated.** `Rscript data/analysis/analysis.R` writes it, and it holds
numbers: descriptives, the test chosen and the reason it was chosen, p-values,
effect sizes with their intervals, n. It carries no takeaways and no
interpretation — that is this skill's job, not the file's.

It is a **primary** drafting source, and `can_draft` is true on a project whose
whole intellectual content is that one file. `stats-check` **is given it**:
denying a statistics checker the statistics would leave it nothing to check.

**The contract runs both ways, and only one direction is enforced:**

> Every number in the paper is in `analysis.md`. Not every number in
> `analysis.md` is in the paper.

So when a value is cut from the draft, it is cut from the draft *only*. It
stays in `analysis.md`, the drafter will not put it back — no paragraph calls
for it any more — and the user can still find it. **Never delete a value from
`analysis.md` for being unused, and never report one.** A line marked
`[must appear]` is the exception in the other direction: `completeness` reports
it whenever the draft does not carry it.

**Two regions.** `analysis.R` rewrites everything above the marker on every run
and never touches anything below it. Numbers this engine adds go **below** the
marker, with the round that added them. Nothing below it is ever deleted, by
anything.

**The denial moved; it did not go away.** `plan/README.md` is where "the
thermophile looks tighter, as expected" gets written down now, and it is the
load-bearing denial in the whole given/denied table. `stats-check`, `abstract`,
`revise-prose` and `reviewer-check` are all denied it. Never quote it into a
prompt for any of the four, and never summarise it for them either.

### When there is no `analysis.md` — warn, offer, and then do as you are told

A project with no `data/analysis/analysis.md`, or one with no generated region
in it, is a project whose analysis has not been run. **Say so before drafting,
in three parts, and then stop pushing:**

1. **Name it.** `data/analysis/analysis.md` does not exist. Every number in the
   paper has to trace to it, so with no analysis there is nothing to check any
   statistic against and the drafter may write no statistics at all.
2. **Offer the route.** The `analysis` skill works out which tests the design
   supports, fills in `data/analysis/analysis.R` with them, and runs it. Offer
   to hand off to it.
3. **Do not block.** If the user would rather keep going, keep going. Two ways,
   and both are supported:
   - **Create a stub** — the marker and an empty tail — so hand-entered numbers
     have somewhere legal to live and the evidence check has something to check
     against.
   - **Write without statistics.** A paper with no numbers in it is a real
     paper. Every statistical claim the drafter wants to make comes out as
     `**[FLAG: stats]**` instead of a number, and `completeness` carries the
     missing file.

This is a **gap, never a block**. Refusing to draft because a statistics file is
absent would refuse every review, every perspective and every methods paper the
group writes.

## Then state the plan and wait

```bash
python <tools>/manuscript.py plan "<project>" --journal IJROBP --preset coauthor
```

There are five presets — `draft`, `coauthor`, `submission`, `revision` and
`sections` — and **no table of them is written here.** `plan` prints the
module list and the agent-call count for the one it resolved, and a table
beside a list the engine generates is a second source of truth that has been
wrong twice. Read the modules and the count out of `plan`'s own output.

**`plan` also says which preset the round LOOKS like, and you pass that on.**
It reads the round number, whether any section has prose, and whether anything
is waiting in `edits/`:

| what the folder says | recommended | why |
|---|---|---|
| a file in `edits/` | `revision` | applying it is what this round is for, whatever the round number says |
| r1, or no section has prose yet | `coauthor` | every check is a first reading rather than a re-reading |
| a later round, prose written, no edits | `draft` | the prose loop, the build and the citation pass, without re-running four checking agents over a paper that has not materially changed |

**It recommends and never selects** — `--preset` has already won, and the
module list is the one that was asked for. When the recommendation agrees with
the preset, `plan` says nothing; when it differs, the line you are already
reading out carries one sentence naming the word that switches it. Read that
sentence with the rest of the line and let the user decide.

**Nothing is ever recommended as `submission`, on purpose.** A paper is ready
to send when a person says it is, and there is no count of finished sections
that may promote a round to the one that builds a package. The escalation is
named on every round instead, so the user never has to know the vocabulary:
say the paper is ready to submit and the round picks up toc-graphic, evidence,
stats, attribution and reviewer checks, final-check and the submission
package.

Print the line it returns and **wait for confirmation before running anything**:

> Running **coauthor** on IJROBP r1: outline → literature-landscape →
> learn-from-edits → draft-sections → revise-prose → evidence-check →
> stats-check → assemble → citation-check → reviewer-check (light).
> Est. 9 agent calls. Go?
>
> Drafting from:
>   `plan/outline.md`  21 paragraph lines · `plan/captions.md`  6 floats ·
>   `data/analysis/analysis.md`  1.2 kB ·
>   **`plan/README.md`  EMPTY — never filled in**
>
> `plan/README.md` is a primary source and has no content. The introduction
> will be built from the outline alone. Fill it, or say go and every claim
> that needed it comes out as a flag naming the file.

**Read the `Drafting from:` block out too, and read the gap sentence with
it.** `EMPTY` and `MISSING` are different words on purpose — one file needs
filling in, the other needs creating — and a round whose primary source is an
untouched scaffold stub has burned a round before: the introduction comes out
thin in a way that reads like the model being bad at introductions rather than
like a file nobody filled in.

If they want a source to count for more or less, that is
`config --weight <source>=primary|normal|low|ignore`. A weight is **instruction
text in your brief, not a coefficient**, and it never changes which files you
are given — except `ignore`, which removes the file and says so.

**Say the reviewer's level in that line and let them change it**, every run.
See module 6: light on a coauthor round, heavy before a PI or a journal sees
it, and the recorded dial is the default you offer rather than the answer.

`+reviewer-check` / `-stats-check` at the prompt is `--add` / `--drop`. Any
module also runs alone against the current source text — that is the point of
the isolation.

**Never offer citation-check as a choice (item 36).** It is in every preset
that writes prose and it runs itself. There is no judgment in it: every
in-text citekey resolves, every entry is cited, there are no duplicates, the
count is under the cap. Asking "shall I also run citation-check?" is a yes/no
question with one right answer, and it was put to the user once and correctly
called the wrong shape. Dispatch it to its own agent as usual and **report
only what needs them** — an unresolvable citekey, an uncited entry, a
duplicate, a count over the cap. A clean pass produces **no line in the run
summary at all**; do not tell them it ran. It is still droppable, but only by
`config --skip citation-check`, which is the user saying so once.

**If the user says they do not want a module *any* more, record it rather than
remembering it:**

```bash
python <tools>/manuscript.py config "<project>" --journal IJROBP --skip citation-check
```

`skip_modules` in `writing_config.yml` holds it, and `plan` applies it to every
later round on top of whatever preset is chosen. It is undone with
`--forget citation-check`, and `--add` beats it for a single round. Say which
part of the plan the config decided — the line `plan` prints already separates
"skipped by the preset" from "left out by your standing config", and the user
should not have to wonder which.

Do not offer to "just remember" a preference across rounds. A later invocation
is a different context window, and a preference that lives only in a transcript
is one the pipeline will quietly stop honouring.

Once the user says go, **open a run before executing anything**:

```bash
python <tools>/manuscript.py run "<project>" --journal IJROBP --start --preset coauthor
```

`plan` prints the dials and the user's standing instructions along with the
module list, so they are echoed before every run without your having to remember
to. On the first run, interview for the config — and explain
`outline_adherence`, because the user has been burned by it:

> **strict** — every outline line becomes a paragraph, in order, nothing added.
> Predictable, and in past attempts *painful to read*.
> **medium** (recommended) — the outline sets the argument and the order; the
> drafter may merge two lines or add a bridge, and must report every deviation.
> **loose** — the outline is a checklist of points that must appear somewhere.

### Scoping a round, and the second question it raises

Here rather than in `reference/outline.md`, where it used to sit, because a
scope governs the WHOLE round and not one module: every later module reads it
off `round_state.json` rather than being told again, and the precedence rule
below is one a plan is built on before any stage file is opened.

"Just the introduction and methods for now" is
`--sections introduction,methods` on `plan` and on `run --start`. "Do not
change the introduction or the methods, but do the rest" is its inverse,
`--except introduction,methods`. Both flags together are refused rather than
resolved — they are two complete statements about the same round.

The scope is recorded in `round_state.json`, named in `drafts/log.md`'s entry
for the round, and read from there by every later module rather than being
told again. **It is sticky**: a later `run --start` that names neither flag
keeps it, and `--sections all` is how it is cleared. The sections left out are
reported as *out of this round's scope*, never as gaps; they do not refuse the
build, and `completeness` returns them under `deferred` with the summary
saying what was not checked.

> **A scope the user gives outranks anything the outline, the plan folder or
> the directory suggests.** An outline line for a section outside the scope is
> not a work item this round — it is deferred. Say so; never treat the outline
> as a work order that overrides what the user asked for, and never quietly
> widen a round because there was more that could have been done.

> **It outranks the preset too.** This is the half that was missing, and it
> cost a whole round: `--sections introduction,methods` used to scope what got
> *drafted* and nothing else, so a request for two sections of prose still
> produced a re-source of the journal's requirements, a fetched stylesheet, a
> generated `reference.docx`, a built `manuscript_r1.docx` and a citation pass
> — none of it asked for, all of it now the user's to review or ignore.
> **Journal-requirement sourcing, CSL fetching, `reference-doc` and `assemble`
> are NOT implied by a request to draft sections.** A request for prose is not
> a request for a `.docx`.

**So a scoped round HOLDS the submission half and asks about it separately.**
`plan` takes the submission modules out of the round, returns them in
`held_for_decision`, and prints a `SECOND QUESTION` line. Put that question to
the user as its own question — the old line stated the modules and asked only
"Go?", which reads as one decision when it is two: *what to draft*, and
*whether to build a document out of it*. The answers:

| the user says | you pass |
| --- | --- |
| "just the prose" | nothing — the hold is already the default |
| "yes, build it too" | `--build` |
| "build it but skip the citation check" | `--add assemble` |
| "draft these and stop after the polish" | `--stop-after revise-prose` |
| "prose only, as a standing thing" | `--preset sections` |

If the user answers the second question *after* the run is already open — the
prose is written and they now want the document — do not abandon the round to
re-plan it. Record the module against the open run: `run --step assemble
--state done --wrote ...`. The ledger logs that it was added part-way and was
not in the plan, which is the true account of what happened and is exactly
what that mechanism is for.

`--preset sections` is the exact spelling of *"only do the introduction and
methods"*: the drafting modules plus the mechanical citation pass, and nothing
that builds or ships. `--stop-after <module>` ends any round where it says and
names what was not reached rather than dropping it. A `submission` preset is
never held — that preset **is** the request to build.

`--redraft <section>` is on `plan` and on `run --start` too — one invocation
only, never sticky (see the freeze, §0c).

### Where the Round Ends — Ask When the Request Does Not Say

`--sections` scopes **which sections** change. A request can instead scope
**what kind of change** is allowed — *"only address the flags and comments"*,
*"just fold in the edits"* — and that is not a statement about where the round
ends. **A limit on the edits is not a limit on the end state** (item 119).

Classify every request before `plan`, and say which it is in the plan line:

| the request | the round's end state |
| --- | --- |
| resolves flags, comments or edits, and says nothing about submitting | **resolve-only** |
| says submit, submission-ready, "getting ready for", names the journal's requirements, or asks for the package | **submission-ready** |
| does both — narrows the edits *and* names the journal, its requirements or submission | **ambiguous → ask** |

When it is ambiguous, ask **one** question before any module runs, and record
the answer with the round intent:

> Should this round only resolve the flags, comments and edits, or end with a
> submission-ready package for <JOURNAL>?

**A submission-ready round is not done until the package is.** It runs
`final-check` and `submission-package`, compares the paper against **every**
sourced row of `requirements.yml` — length, formatting, references built and
numbered, anonymization, each required statement in the file the journal wants
it in, figure files, the AI disclosure — and fixes what it can. It reports done
only when every row passes or names the single item only a human can supply.
The user's limit on edits still holds for the prose: fixing a requirement is
not licence to redraft a section nobody asked about, and a cut to fit a word
limit is still the user's call (`length_policy`).

## The modules

Each runs with an **explicit input allow-list**. Build that context from the
list, never by inheriting this session's — for `stats-check` that is not a
preference, it is the whole module. A `stats_report.md` written by an agent
that has read the hypothesis is worse than none, because it will be trusted.

### How a module is spawned

Seven of them run as their own agent. **Never hand-write the prompt** — ask
the engine for it:

```bash
python <tools>/manuscript.py agent-brief "<project>" --module stats-check --journal "<journal>" --json
```

That returns the resolved allow-list, what is missing from this project, and a
`prompt` field. Spawn with the **Agent** tool, pass `prompt` **verbatim** as
the agent's prompt, and use `subagent_type: "general-purpose"`.

> **Never `subagent_type: "fork"`.** A fork inherits this conversation's whole
> context by definition — every denial in the table below, undone, while the
> run still reports that a sub-agent ran. The engine names it as forbidden in
> the brief's `spawn.never` field for exactly this reason.

**In a harness with a different spawn tool**, follow the brief's `spawn` field,
which names each harness's tool and parameters. Where the tool takes a setting
for how much of this conversation the child inherits, **set it to none, by
name. Never rely on its default.** In at least one harness the default is the
whole conversation, which is a fork under another name.

Do not summarise the project for the agent, do not add "helpful context", and
do not answer its questions about anything on the denied list — a summary of a
denied file is the denied file. If it comes back saying it reasoned from
something outside its list, that is a finding about the pipeline and it goes
in the run summary.

**If the harness cannot spawn a sub-agent with a chosen context**, run the
module as a separate invocation whose prompt is that `prompt` field and
nothing else. **If that is not possible either**: for a `fresh` module, run it
in session and note in the report that it was not isolated; for a **blind**
module, skip it and say so. The brief's `fallback` field says which of the two
applies. This is the one failure in the pipeline that is invisible while it
happens — the report still looks fine, it is just worthless.

**Ask `agent-brief --list` how many modules there are.** It names every one,
which of them are spawned, which are blind, and why the rest run in session -
and no count is written here on purpose. A number typed in prose beside a
table the engine generates is a second source of truth, and this sentence
previously carried four of them, three of which disagreed with the engine and
with each other (item 91). The plan line you show the user before a round
names them too (`isolated_modules`, `blind_modules`), because whether a module
ran blind cannot be seen in its report afterwards.

| # | module | given | denied | writes |
|---|---|---|---|---|
| 0 | outline **[agent]** | `plan/outline.md` (both regions), plan/README, captions, stats output, refs, `methods_facts.yml` | — | `plan/outline.md` `# Structure`, under the PROPOSED banner |
| 0b | literature-landscape | the title, the outline, `scholar.py` (all six indexes) | this project's own narrative | `plan/literature_landscape.md` |
| 0c | learn-from-edits | the frozen sections and their snapshots, the registry, `requirements.yml` | — | the registry, the layer-1 brief, `reports/rN/learned.md` |
| 1 | draft-sections **[agent]** | **`plan/literature_landscape.md`**, outline, plan/README, captions, stats output, `methods_facts.yml`, **`instrument_metadata.json`**, `provenance.json`, refs, writing rules | the digest's **`## At revision`** half | `source_text/*.md`, **the sections in this round's scope**, body first and `title_abstract.md` last |
| 1b | abstract **[agent, blind]** | the finished `source_text/*.md`, captions, the journal's abstract type and cap | **README, outline, hypothesis, data/, this conversation** | `source_text/title_abstract.md`, the `## Take-home` / `## Supported by` headings of `reports/rN/prose_report.md` |
| 1c | revise-prose **[agent, blind]** | `source_text/*.md`, the digest's `## At revision` half, layers 0-3 of the brief, `writing_config.yml`, `voice --json` | **outline, README, hypothesis, `data/`, stats output, `references.bib`, captions, this conversation, every report** | `source_text/*.md`, `reports/rN/prose_report.md`, `reports/rN/learned_rules.md` |
| 1d | comprehension-check **[agent, blind]** | the manuscript IN READING ORDER, `live_captions.md`, `writing_config.yml:audience` | **outline, README, literature landscape, hypothesis, `data/`, `data/corpus/`, rough draft, every report, this conversation** | `reports/rN/comprehension_report.md` |
| 1e | quality-check **[agent, fresh — NOT blind]** | `source_text/*.md`, **`plan/outline.md`**, the claims ledger, `analysis.md`, `writing_config.yml`, `reports/rN/ai_voice.json`, the house digest | **the drafting conversation, every other report, rough draft** | `reports/rN/quality_report.md` |
| 2 | evidence-check | source text, refs.bib, stats output | plan/README narrative | `reports/rN/evidence_report.md` |
| 3 | stats-check **[agent, blind]** | source text, stats output, data contract | **hypothesis, README, outline, captions' claim lines** | `reports/rN/stats_report.md` |
| 3r | attribution-check **[agent, blind]** — *every paper kind, beside stats-check; Introduction and Discussion, and every body section a review declares* | one paragraph at a time, and the retrieved text of every source it cites, from `data/corpus/papers/` | **what the paper ARGUES: plan/README, outline, `title_abstract.md`, `protocol.md`, `synthesis.md`, the rest of the draft, every report, this conversation** | `reports/rN/attribution_report.md` — or one `DID NOT RUN` line when no pair has retrieved text |
| 4 | assemble | source text, requirements.yml, floats, refs | — | `{JOURNAL}/submission/manuscript_rN.docx`, and `supplementary_info_rN.docx` from `<source_text_rN>/supplementary.md` and the S-numbered captions, supplementary figures embedded |
| 5 | citation-check **[agent]** | refs.bib, in-text citations | everything else | `reports/rN/citation_report.md` |
| 6 | reviewer-check **[agent, blind]** | the built .docx, requirements.yml, the rendered floats | **hypothesis, README, outline, this conversation** | `reports/rN/reviewer_report.md` |
| 7 | final-check | writing_config, all reports, source text, edit ledger | — | resolution summary |
| 8 | submission-package | requirements.yml, manuscript, `plan/author_information/authors.md`, `plan/author_information/affiliations.md`, `<source_text_rN>/suggested_reviewers.md` | — | `{JOURNAL}/submission/*_rN.*` — cover letter, title page, statements, checklist, and suggested reviewers unless the journal wants those in the letter; on a `figures.placement: separate` journal, every figure, main and supplementary, into `{JOURNAL}/submission/figures/` |
| 8c | submission-names | this round's package | — | `{JOURNAL}/submission/upload/` — the same files under the name they are sent as, e.g. `EXAMPLE_STUDY_manuscript.docx`. **Run only when the user says it is time to submit** |
| 9 | respond-to-reviewers | `edits_status.md`, the revision, line numbers | — | `response_to_reviewers_rN.docx` |
| 10 | toc-graphic | requirements.yml, title/abstract, outline claims, captions | — | `plan/toc_graphic/` + the block in `source_text/toc_graphic.md` |

**[agent]** is spawned as its own sub-agent, prompt from `agent-brief`.
**blind** means the denials are load-bearing against bias: breaking one does
not fail visibly, it produces a confident wrong report that gets trusted
because it exists. The eight modules with no marker run in this session —
`agent-brief --list` says why each one does.

**Keep the thesis out of any file the harness auto-loads.** Most harnesses
inject a standing project instruction file — whatever they call it, sitting at
the project root or anywhere above it — into every sub-agent spawned from
there, without being asked and without it appearing on any READ list. The
engine cannot suppress an injection it does not perform: `agent-brief` names
the ones it can find on a blind module's denied list and requires every report
to open with an `Auto-loaded files:` line, and `run --state done` reports a
report that has none. What that cannot undo is a project instruction file that
restates what the paper argues, which un-blinds `attribution-check` and
`stats-check` at the moment they are spawned. So if the user keeps one, say
this once: **it may hold conventions and paths, and it may not hold the
hypothesis, the thesis or the result.**

Reports land in `drafts/{JOURNAL}/reports/rN/`. They are for the user and for
final-check, and are **not** fed back into the drafter automatically — the user
decides what gets applied.

**What `reports/` is for, and what must never go in it.** The manuscript
carries what is *wrong* with the paper; the reports carry what was *checked
and came back clean*, and the second cannot live in the first — a citation
check that finds nothing writes nothing in the `.docx`, which is
indistinguishable from a check that never ran. So a report holds negative
evidence, waivers, and what a pass deliberately left alone. It never holds a
second copy of something already in the manuscript: `flags.md` was exactly
that, a third copy of a list that is inline in the source text and again in
the PAPER NOT COMPLETE block, and it is not written any more (item 43).
`prose.py flags` reports them; nothing writes them to a file.

**Name the reports you wrote in the run summary**, with their paths, whenever
one holds something the user has not already been told. A folder nobody thinks
to look in is a folder that stops being read.

**Two numbering schemes meet here, so read the names and not the numbers.**
`specs/writing-engine-prose.md` calls the learning module `0b`; this file has
called literature-landscape `0b` since before it existed. The engine's module
list is the authority and it is ordered, not numbered:

    outline → literature-landscape → learn-from-edits → draft-sections
            → revise-prose → evidence-check → …

What is load-bearing is the two positions, not the labels. **learn-from-edits
runs before draft-sections**, so a section drafted this round is drafted *with*
the lesson in its brief rather than patched afterwards — and so a section
handed back by `--redraft` is learned from first and discarded second.
**revise-prose runs after drafting and before every checker**, so everything
downstream sees revised prose; a pass that rewrote prose after `assemble` would
invalidate every report generated from the text and orphan the built `.docx`.

## Every round, write the log

`drafts/log.md`, newest first, a short paragraph in plain prose. No diffs, no
file lists, no jargon. Someone who missed two rounds should catch up in a
minute. One file for the whole project, every journal in the same sequence —
`assemble` opens the round's heading and the prose under it is yours.

## Clearing the context mid-run

Same mechanism as **Picking up an interrupted run** above, used deliberately
rather than after an accident. **Every module's output to the next one in this
pipeline is a FILE** - `revise-prose --from-comprehension` is handed
`reports/rN/comprehension_report.md`, `final-check` reads back the reports the
isolated modules wrote, `respond-to-reviewers` writes from `edits_status.md`.
Nothing downstream of any module depends on a message. So the second half of a
round needs the source text, the journal's own rules and this journal's config,
and none of those is in the conversation.

**What IS only in the conversation is anything the user said that nobody wrote
down**, and that is the one thing a clear can lose. So:

**1. Capture first.** Before anything is cleared, put the session's own
knowledge on disk:

| what it is | where it goes |
|---|---|
| a standing preference | `config --instruct "<text>"` |
| a decision for this round only | `run --step <module> --note "<text>"`, or the round intent |
| an answer to a **[FLAG: ...]** | `flag-answers` |
| anything about the paper itself | `drafts/log.md` |

**If it cannot be written down, the boundary is not safe and the clear does
not happen.** The failure mode of a compaction is not a crash - it is a second
half of the round that quietly does not know something.

**2. Record the step that just finished, with `--wrote`.** Before the clear,
never after: a clear between a module finishing and its step being recorded
loses the freeze hash as well as the progress.

**3. Ask the engine what to hand across.**

```bash
python <tools>/manuscript.py handoff "<project>" --journal IJROBP --stage submission --json
```

It is built entirely from disk and reads no transcript. `resume_line`, `next`,
`done`, `remaining` and `stale_outputs` are the same fields `run --json` gives,
not a second copy. **It never says "safe to compact"** - it cannot see the
conversation, so a verdict about the conversation would be a confident wrong
answer. What it prints instead is `not_recorded`: the CLASSES of state it
cannot see, so you know what you are being asked to check by hand.

**4. On the other side**, read `SKILL.md` and the files in `read` - and not
the rest - and say the `resume_line` out loud before doing anything, exactly
as the resume protocol already requires.

### When it happens: the `compaction` dial

`plan` marks every boundary this round crosses, in the line the user approves,
so the shape of the round and its clear points arrive in the same sentence.
`writing_config.yml:compaction` says what to do at one:

| value | behaviour |
|---|---|
| `auto` | **the default.** Capture, record the step, call `handoff`, clear. **Announced in one line, not asked** |
| `ask` | the same, with a yes/no at each boundary |
| `off` | no boundary is crossed; `handoff` still works when called by hand |

At `auto` the announcement is one sentence naming the boundary, and then you
go:

> Drafting is done and recorded. The submission half needs the source text and
> the journal's rules and nothing that is in this conversation, so I am
> starting fresh from here - say so if you want to keep talking about the
> draft first.

**A question at every boundary is the failure mode this whole idea has**, and
it is the same reasoning that stopped `citation-check` being offered as a
yes/no with one right answer. `auto` is a deliberate reversal of this
toolkit's usual *offer, never run*: everything else writes to the project
folder and **a compaction writes nothing**. Its whole content is forgetting,
the forgetting is bounded by the capture above, and the run ledger means the
worst case is re-reading a file.

**Three things override the dial and are not the user's to set**, because each
one is state that is not on disk:

- **a module that has not finished.** The boundary is INSIDE a module. Compact
  at module boundaries, resume inside them, and never confuse the two: what a
  half-finished module knows is exactly the part that has not been written
  down.
- **`stale_outputs` is non-empty.** A file recorded as written that has since
  changed is a question for the user, and the question needs the context that
  noticed it.
- **a question standing to the user unanswered.** Clear after the answer is
  recorded, never between.

And two more places it is never safe, which no dial can see: **in the middle
of `flag-resolver`**, which walks flags with the user one at a time - a clear
between the asking and the recording makes the user answer the same question
twice, which is the most annoying possible failure of this idea - and
**between a module and the `run --step` that records it**, which is rule 2
above said from the other side.

## When the Engine Itself Is Wrong

**At any stage of a round, not only at the end.** If the engine does something
it cannot justify — refuses valid input, reports a number it cannot have
measured, exits 0 on a check that never ran — file it before the session ends:

```bash
python <tools>/manuscript.py log-issue "<project>" --stage writing \
    --journal <JOURNAL> --round <N> \
    --code a_short_slug --detail "what happened, GENERICALLY" \
    --example "what happened on THIS project" \
    --title "one line" --wanted "what it should do" \
    --verify "how the next run answers 'is it still there?'"
```

`--detail` is generic and `--example` is the one place a project specific may
live — the file is read by everyone who has the toolkit. `--verify` is
required. **`reference/submission.md` has the full rules**, including `--fixed`
and the lint; this is here rather than only there because a defect found while
drafting is lost if the sentence permitting it lives in the stage-8 file.

## Titles, Headings and Labels Are Title Case

A standing preference of this group's, not a choice about one paper: **every
title, heading, section header, figure or table title, box or node label,
button and short caption is Title Case.** It is in the spine because the spine
is read on every invocation and because this skill writes headings at four
different points - the outline's section names, the drafted section headings,
a float's caption line, and the submission package's own documents.

Title Case here is the ordinary English convention: capitalise the first and
last word and everything between except articles (*a, an, the*), coordinating
conjunctions (*and, but, or, nor, for, so, yet*) and prepositions of four
letters or fewer (*of, in, on, at, to, for, with, from, into, over*) - and
capitalise those anyway when they are first or last.

    Growth Temperature and Array Order      not  Growth temperature and array order
    Effects of Annealing on Film Symmetry   not  Effects Of Annealing On Film Symmetry

**What keeps its own casing, always:** gene and protein symbols, species
names, units and quantity symbols, chemical formulae, file paths, command
lines, code identifiers, and any product or proper noun with an established
spelling (`pandoc`, pH, *E. coli*, AlphaFold). Retitling one of those is a
factual error, not a style choice.

**What is not a title and is left alone:** running prose, body text, bullet
sentences, a caption's explanatory sentences under its bold claim line, and
anything quoted - a coauthor's own words, a reviewer's point, a source's
title as the journal printed it.

**A journal's own style wins where it conflicts**, and it is a
`requirements.yml` fact like any other: sourced with a URL or `unknown`. Some
journals mandate sentence case for headings. Where `requirements.yml` says so,
follow it and say once that you are.


## Things that are wrong to do here

- **Letting stats-check see the hypothesis.** Nothing visibly fails, and the
  module is then worthless. Summarising the project for it, letting it inherit
  this conversation, and quoting your own reasoning at it are all the same
  mistake.
- **Letting reviewer-check see anything but the manuscript and the journal's
  rules.** An adversarial read by someone who already knows the answer is a
  reassurance, not a review.
- **Passing `--force` to get past the overwrite refusal.** The engine stops a
  rebuild over a manuscript that has been edited since it wrote it. `--force`
  discards those edits. The route is `edits/` and `ingest`.
- **Redrafting a section no edit asked about.** The edits are the work order.
- **Running again on a built project without knowing why.** Ask.
- **Writing a methods number that is not in `methods_facts.yml`.** The worst
  output this pipeline could produce.
- **Working around the mock-data gate**, or renaming a `_MOCK` file.
- **Hand-editing a citation number**, or hand-writing `edits_status.md`.
- **Silently trimming a caption's claim to hit a word cap.**
- **Running the whole pipeline every round.** Print the module list first.
- **Guessing a journal requirement** because the value is formulaic.
- **Skipping a round's float snapshot because nothing changed.**
- **Re-asking what `manuscript.py status` already reports.**
- **Starting a run over because the last one was interrupted.** `status`
  reports where it stopped.
- **Writing an outline the user has not seen**, or revising one without
  showing the diff first.
- **Writing prose into `plan/outline.md`.** One line is the idea of a
  paragraph; the detail belongs in the paragraph or in the notes block.
- **Proposing an outline when `can_draft` is false.** It will read as a good
  outline and nothing in the project stands behind it.
- **Redrafting or revising a frozen section.** If the user deleted a method on
  purpose, putting it back is the behaviour that makes a tool untrustworthy.
  `--redraft` is the only way, it is the user's to type, and it says what it
  will discard.
- **Learning a rule from one observation.** A registry that learns from n=1
  fails invisibly: the wrongness arrives as slightly worse first drafts on an
  unrelated paper months later. One observation is a candidate and is applied
  to nothing.
- **Learning a prose rule from a `factual` edit.** `400 °C` → `450 °C` means
  the recorded source is wrong. It is a data discrepancy, never a style
  lesson.
- **Recording a layer-0 absolute as a learned budget.** A fraction cap looks
  exactly like a budget, and it is the one place the loop would quietly eat an
  invariant. `learn.py` refuses; do not work around it.
- **Learning from a round that was over the journal's word cap**, or one with
  an unresolved `**[FLAG]**` in that section. A section cut to fit looks
  exactly like a preference for shorter prose. Apply it, do not induce from
  it.
- **Letting `revise-prose` see the outline, the stats output or the
  bibliography.** Given the outline it re-argues the science; given the stats
  output it adds numbers; given the README it restores the paper we meant to
  write.
- **Treating a `voice` or `metaprose` finding as a gate.** Every check outside
  layer 0 reports and exits 0. A gate on a prose measurement recreates the
  original failure one layer up.
- **Handing the drafter the digest's `## At revision` half.** Its rules are
  editing rules; there is nothing to cut at draft time, so the drafter
  compresses instead — which is the 40-word citation-stuffed sentence this
  staging exists to stop.
- **Waiving a learned rule without a reason, or applying every rule in scope
  and waiving none.** The first is unauditable; the second is a checklist
  wearing a judgment's clothes.
- **Proposing an outline before asking whether one already exists elsewhere.**
  An empty `plan/` and an outline in the user's notes folder look identical on
  disk, and only one of them wants a proposal.
- **Writing a proposed outline without `--proposed`.** An outline you guessed
  that does not say so is indistinguishable from one the user authored, and
  every check run against it inherits an authority it never had.
- **Running `--accept` yourself.** Accepting is the user saying the lines are
  theirs. Showing them the lines is not the same as being told.
- **Refusing to write an outline because a line ran long.** Length is advice.
- **Deleting a bibliography entry the user has not seen listed**, or deleting
  one that is cited from a planning note or a caption.
- **Cutting the paper to fit a word limit on your own initiative.** Which way
  to go is the user's call, and carrying the overage is a legitimate answer.
- **Reordering the argument to fix float numbering.** Renumber the floats; the
  prose is the paper.
- **Judging the callout form against a `citation_style` nobody sourced.**
- **Promising to remember a standing preference instead of recording it.**
  `config --skip` and `--instruct` are where a preference survives the next
  context window.
- **Running a module the user has skipped without saying you are.** `plan`
  reports the standing skips separately; print that, and if you `--add` one
  back, say why.
