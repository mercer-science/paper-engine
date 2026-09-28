---
name: analysis
description: Work out which statistical tests a project's data actually supports, fill in data/analysis/analysis.R with the user, run it, and read back the numbers it produced into data/analysis/analysis.md. Use when a project has data but no analysis, when the writing engine reports analysis.md missing, when the user asks what tests to run, which comparisons the design supports, how to set up their statistics, or wants to mark a value as one the paper must carry. Runs standalone on any project at any time; it writes numbers and R, never prose and never interpretation.
---

# analysis

Everything downstream of the data — every p-value on every figure, every number
in the results — is gated behind one thing: somebody knowing which of their
columns are outcomes and which are groups, and writing that into an R file.

`data/analysis/analysis.R` ships with sections 1 and 2 as `TODO`s and two empty
character vectors. Sections 3 and 4 are the whole battery and are already
written. **That gap is the narrowest point in the toolkit**, and until this
skill it had no aid standing at it.

**One sentence for what this does:** it works out with the user which analyses
their design actually supports, fills in the two sections that need filling in,
runs the script, and reads back what came out. **It writes no prose and no
interpretation.**

Spec: `specs/analysis-and-front-matter.md` §2. Read it before changing
behaviour here.

## What it writes, and the one thing it must not

| Writes | Never writes |
|---|---|
| `data/analysis/analysis.R` sections 1 and 2 | sections 3 and 4 — the battery is provided |
| `data/analysis/analysis.md`, by running the script | anything into the paper |
| `[must appear]` markers, when the user asks for one | what the numbers mean |

**The boundary is the same one `flag-resolver` holds.** A takeaway this skill
notices goes to the user in conversation. It reaches a file only if the user
says so, and then it goes to `plan/README.md` or the `# Notes` region of
`plan/outline.md` — never into `analysis.md`.

The reason is not tidiness. `analysis.md` is the one file every downstream
module is allowed to trust as fact, and `stats-check` is given it precisely
*because* it is numbers. An interpretation written there arrives in a blind
module's context wearing the authority of a measurement.

## Resolving `<tools>`

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
do not wait for a reply, and do not raise it again later in the session.

## Invocation

Standalone, on any project, at any time.

```
analysis                               # the project in the working directory
analysis "Projects/surface_example"
analysis --run                          # skip the conversation, just re-run it
analysis --status                       # what is declared and what came out
```

It does **not** need a round open, a journal folder, or an outline. It needs
data, or a data contract describing data that does not exist yet — a project
with mock data is a project this skill can be useful on, and saying so early is
better than discovering it at the end.

## What to read first

Read all of these before asking anything. The questions worth asking are the
ones the project has not already answered.

| File | What you are looking for |
|---|---|
| `data/data_contract.md` | what each column is, its units, and what it can legitimately be compared across |
| `data/raw/*.csv` | the columns that actually exist, their types, how many rows, how many levels per candidate grouping |
| `data/mock_data/*_mock.csv` | the same, when there is no real data yet — say which one you read |
| `plan/outline.md` | the claims. A claim with `[stats: ...]` evidence is a test the paper is already promising |
| `plan/captions.md` | a caption's claim subtitle is a finding stated as a sentence, and usually names a comparison |
| `plan/README.md` | the hypothesis, which is what makes one comparison the point and another one a control |
| `data/analysis/analysis.R` | what is already declared — this may not be a fresh project |
| `plan/lab_pack_brief.md` | **only if it exists.** The lab's own route, step by step, quoted from the resource pack, with the decisions and open questions `idea-generation` recorded against each step. It is where "how were these samples actually made" is answered without asking |

```bash
python <tools>/manuscript.py completeness "<project>" --json
python <tools>/scaffold.py check "<project>" --json
```

**`plan/lab_pack_brief.md` is a source and never a result.** Every number in
it was true on the pack's curation date, for the standard case — it makes a
question specific and never answers one, and nothing in it may become a value
in `analysis.R` or a number in `analysis.md`. If the user contradicts a line
in it, the user is right. This skill reads it and never writes it;
`labpack.py brief` is the only writer, and the `## Your Notes` section at the
end is the user's.

## The conversation

Staged, in this order, the way `idea-generation` is staged. Do not open with a
form. **Read first, then ask only what the reading did not answer.**

### Stage 1 — say what you found

Before any question, state what the data looks like: the file, the row count,
the columns, and which of them are numeric and which are categorical with how
many levels. This is the stage that catches the two failures nobody reports —
a column that is text where it should be numeric, and a grouping variable with
one level.

Say explicitly whether you are looking at real data or mock data. If it is
mock, everything from here is a rehearsal, and the user should know that before
they spend twenty minutes on it.

### Stage 2 — the outcomes

*What did you measure?* The outcomes are the numeric columns the paper makes
claims about. Propose them from the data and the outline rather than asking
cold, and let the user correct the list.

Watch for a column that is an outcome in one comparison and a grouping in
another — `temperature` as a measured response and `temperature_class` as a
factor are two columns, and a project with only the first usually needs the
second.

### Stage 3 — what splits them

*What do you want to compare across?* Grouping variables, with their levels
named back to the user and the n in each. Two levels and three-plus levels are
different tests, and a group with n = 2 is a number this skill should say out
loud rather than quietly run a t-test on.

### Stage 4 — what the design will and will not support

**This is the stage that earns the skill.** Say plainly what the data cannot
answer, and say it now rather than after the paper is drafted:

- **Repeated measures and nesting.** The provided battery treats every row as
  independent. Three grids from one sample are not three independent
  observations, and if the data is nested, the battery will report a p-value
  that is too small. Say so. This is not a reason to avoid running it; it is a
  reason to say what the number means.
- **A claim with no comparison behind it.** An outline line claiming a
  difference between conditions the data does not contain is a gap in the
  project, not a test to invent.
- **n too small for the effect size to mean anything.** The battery reports a
  95% interval, and an interval spanning zero and both directions is worth
  seeing before it is written about.
- **A grouping variable with one level after filtering.** Silently produces
  nothing at all, and the empty output looks like a script that did not run.

Where the design cannot support a claim the outline makes, **say which claim**.
That is a finding the user needs, and it belongs in the conversation and in
`plan/README.md` — not in a file this skill writes.

### Stage 5 — write it, run it, read it back

Fill in sections 1 and 2 only:

```r
# 1. LOAD
d <- load_data("data/raw/measurements.csv")

# 2. DECLARE
outcomes <- c("yield_pct", "turnover_number")
groups   <- c("catalyst", "temperature_class")
```

Every outcome is tested against every grouping, so an outcome and a grouping
that make no sense together still produce a row. That is intended — the file is
a record of what was measured, and not everything in it belongs in the paper.

Then run it and read the result:

```bash
Rscript data/analysis/analysis.R
```

Read `data/analysis/analysis.md` back and tell the user what came out: which
test was chosen for each comparison and **why** (the reason is recorded with
every row), the n, and the effect sizes with their intervals. Report what is
ambiguous as ambiguous.

If `Rscript` is not available, say so, leave sections 1 and 2 written, and
stop. Do not compute statistics by hand and do not write numbers into
`analysis.md` yourself — a number in that file that no script produced is
exactly what the whole checking apparatus exists to catch.

### Stage 6 — `[must appear]`, only if asked

Offer it once. A value marked `[must appear]` is one `completeness` will report
whenever the draft does not carry it:

```markdown
| A vs B | Welch t-test | t = 4.11 | p = 0.002 | g = 1.8 |  [must appear]
```

**Nothing is marked automatically.** A value nobody chose is a requirement
nobody agreed to. Most projects mark nothing, and that is the normal case.

A marker written **above** the marker line is overwritten on the next run —
that is the region contract, not a bug. To make one survive, declare it in
section 2 of `analysis.R`, or put the marked line below the marker.

## The two regions of `analysis.md`

`analysis.R` rewrites everything **above** the marker on every run and never
touches anything **below** it.

```markdown
<!-- ------------------------------------------------------------------
     GENERATED ABOVE, KEPT BELOW.
     ...
     --------------------------------------------------------------- -->
```

Three rules follow from that, and all three matter here:

1. **Nothing below the line is ever deleted** — not by the script, not by the
   writing engine, not by `manuscript.py round`. A value cut from the paper
   stays in this file. The paper gets shorter; this file never does.
2. **A file with no marker is kept whole, below the line.** That is a
   hand-written `analysis.md` from before this file was generated, and the
   first run preserves every word of it. If you find one, say so before you run
   anything, and confirm the user is happy for the file to become generated.
3. **Every number in the paper is in this file. Not every number in this file
   is in the paper.** A value nothing points at is reported by nothing and is a
   supported state. Never delete one for being unused.

## The contract this skill hands to the writing engine

> Every number in the paper traces to `analysis.md`.

`prose.py numbers` enforces it, and `manuscript.py completeness` reports what
fails. So the honest end of this session is not "the analysis is done" — it is
**which claims in the outline now have a number behind them, and which do not**.
Say both.

## What this skill does not do

- **It does not add tests to the battery.** Sections 3 and 4 are provided and
  are the same in every project, which is what makes the asterisk on a chart
  and the number in a paragraph agree. If a project genuinely needs a test the
  battery does not have, that is a change to the toolkit with its own
  reasoning — say so, and do not fork the script into the project.
- **It does not touch the figures.** Figure scripts plot from `data/raw/`
  directly and do not read the analysis at all, so re-running this script
  changes no figure. If a panel prints a p-value, that number is typed from
  `analysis.md` and somebody has to retype it when the analysis changes -
  say so when you have just changed one.
- **It does not write the results section.** That is `writing-engine`, and it
  will read `analysis.md` when it gets there.
- **It does not decide anything is significant.** It reports what the test
  returned and what the interval was.

## When the Engine Itself Is Wrong

**File it. From this stage, not only from the writing one.** If the engine does
something during working out or running the analysis that it cannot justify — refuses a valid input, reports
a number it cannot have measured, writes a file in the wrong place, exits 0 on
a check that never ran — that is a defect in the toolkit, and it is lost with
this session unless you record it:

```bash
python <tools>/manuscript.py log-issue "<project>" --stage analysis     --code a_short_slug --detail "what happened, GENERICALLY"     --example "what happened on THIS project"     --title "one line" --wanted "what it should do"     --verify "how the next run answers 'is it still there?'"
```

Three things about this command, and each of them has cost something already:

- **`--stage analysis`.** Unstated, the stage is inferred, and the inference is
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
