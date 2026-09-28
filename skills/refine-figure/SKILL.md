---
name: refine-figure
description: Refine the R scripts that build a figure or table - plan/figures/FigNN/figure.R, panels.R, plan/tables/TableNN/table.R, and the mock-data generator beside them. Use when a figure needs to look different, when a plot does not show the claim its caption asserts, when a rendered float is wrong or ugly or unreadable, when the axes or panels or colours need changing, when a table needs different columns, or when the mock data does not exercise the hypothesis. Also use to lint float scripts for the traps that render successfully and produce the wrong figure.
---

# refine-figure

The scaffold writes a runnable float script before any data exists — a slot per
float behind `BUILT <- FALSE`, a typed data contract, and hypothesis-shaped mock
rows — so the common operation is not *"write a float script from nothing"*,
which happens once. It is **"edit a script that already runs, look at what came
out, and edit it again"**, which happens ten or twenty times per float.

That loop is where the whole value of mock data gets collected: a figure built
on hypothesis-shaped rows is a test of the figure **and of the claim**, before
any sample is prepped.

Spec: `specs/setup-project-directory.md` §5.7. Read it before changing
behaviour here.

## The engine does the countable half

```bash
python <tools>/scaffold.py float list  "<project>" --json
python <tools>/scaffold.py float lint  "<project>" [--float "Figure 2"] --json
python <tools>/graphic_figure.py status "<project>" --json
```

Resolve `<tools>` in this order, and stop at the first hit:

1. `${CLAUDE_PLUGIN_ROOT}/tools/scaffold.py`, if the harness expands that
   variable
2. `../../tools/scaffold.py` relative to this skill's own directory (the skill
   is authored inside the toolkit at `skills/<name>/`, so this wins when it
   has not been synced out)
3. `tools/scaffold.py` under the toolkit root, if the pointer file that led you
   here named that root

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

**This skill needs no sub-agent and no isolated context.** Everything it reads
is the user's own project, and there is nothing here a blind module exists to be
protected from — the claim in `plan/captions.md` is *supposed* to be the thing
you are working towards. Run it in the conversation.

**`float lint` reports and never edits, and it is never a gate.** Every finding
is fixed by editing a script the user owns. Do not treat a finding as a reason
to refuse a render — a lint that gates teaches people to skip the render, which
is the one thing this loop cannot survive.

## The loop, and the order matters

The first two steps are what stop the refinement being aimless.

**1. Read the claim.** `plan/captions.md`'s block for this float carries a bold
claim sentence, written at idea time before any plotting code existed. **That
sentence is the specification.** A session that has not read it is decorating.
`float lint` prints it, and says so when there is not one.

**2. Read the contract.** `data/data_contract.md` gives each column its kind,
units, expected range and permitted values. **The kind decides the geom and the
test**, so a figure argued from the contract is already most of the way to the
right form.

**3. Read what the script does now**, and which data it reads — real, mock, or
both. `plan/floats/float_provenance.json` records which script and which data
made each float, and whether that data was mock.

**4. Edit — one change at a time, and re-render between changes.** A script that
breaks after six edits costs more to diagnose than six renders cost to run.

**5. Render, and LOOK at the image.** Not at the exit code:

```bash
Rscript plan/render_all.R
```

then open `plan/preview.html`. **Every trap below renders successfully.** R is
installed on this machine and is *not* on `PATH` — look under
`C:\Program Files\R\*\bin\Rscript.exe` before reporting R missing.

**6. Hold the image against the claim.** Does a reader see the claim without
being told? If not, say which of the two is wrong — the figure or the claim.
**The honest outcome is sometimes "the claim is weaker than it sounded"**, and
learning that in an afternoon on mock data is the entire point of having mock
data.

**One claim per figure** is the standing constraint. The most common finding in
this loop is that a panel is carrying a second claim that wants its own float.

## Mock data: the rule that keeps the script honest

A script tuned until it looks right on mock data can be a script tuned to the
mock generator. So:

- **The script must not know it is reading mock data.** No `if (MOCK)` branch,
  no mock-specific axis limits, no hard-coded group names only the generator
  produces. It reads a path. When real data lands, the script runs unchanged —
  or the refinement was wasted.
- **Fix the generator, not the plot.** If the mock rows do not show the claim,
  the interesting possibility is that the *hypothesis as encoded* does not imply
  the claim, which is a finding about the paper. The generator's own comment
  block is where the hypothesis is written down. Widening an axis until the
  difference looks bigger is the wrong repair, and it survives into the
  real-data render.
- **Never touch the three mock-data safeguards.** Not the `_mock` filename
  suffix, not the watermark, not `create_floats.R`'s refusal to build the
  coauthor `.docx` from mock rows. A session that removes a watermark to see the
  figure cleanly has removed the thing standing between mock data and a
  coauthor's document. `--force` exists for a preview; the safeguards stay.

## Eight traps. Every one renders successfully

Three of these `float lint` catches. The other five are properties of an image
or are enforced elsewhere, and they are here because you have to **look** for
them.

| # | Trap | What it looks like | Lint? |
|---|---|---|---|
| 1 | **`figure.R` runs LAST in its folder** — sorted order alone puts it first | the figure is composed from the *previous* run's panel art, and looks entirely fine | no — `render_all.R` enforces it |
| 2 | **`save_float()`, never a bare `ggsave()`** | no `.pdf`, wrong size, no provenance — so the mock watermark never fires | **yes** |
| 3 | **State `height =` as soon as the layout has more than one row** | `(A \| B) / C` renders one row tall with the graphic panels shrunk to nothing | no — look at it |
| 4 | **`panel_header()` / `panel_body()` for aspect-locked art** | two graphic panels side by side rendered titled `Reaction sccAhpepmaeratus` | no — look at it |
| 5 | **R is installed and not on `PATH`** | `which Rscript` finds nothing | no — every engine here already looks under Program Files |
| 6 | **Resolve floats by stem, never filename; key by label** | every captioned table rendered under its caption *and* listed again as an orphan | no — `read_captions.R` enforces it |
| 7 | **Clear `BUILT <- FALSE` when the slot becomes real** | renders clean and reports itself unbuilt forever, so a finished figure is reported missing by `completeness` and inside the `.docx` | **yes** |
| 8 | **Check every column against `data/data_contract.md`** | a renamed column fails at `read_csv`, which is the *good* case; a column that exists with a different meaning does not fail at all | **yes**, by name only |

Trap 8's lint compares **names and nothing else**. It cannot check meaning and
does not claim to — that check is you, reading the contract row.

## Every Label Is Title Case

Axis titles, legend titles, facet strip labels, panel titles, annotation text
and factor level labels are **Title Case**, without being asked: `Growth
Temperature (°C)`, not `growth temperature (°C)`; `Symmetry Index`, not
`symmetry index`. Capitalise the first and last word and everything between
except articles and short prepositions (*a, an, the, and, or, of, in, on, at,
to, for, with*).

**What keeps its own casing:** gene and protein symbols, species names, units,
chemical formulae, and anything quoted — a unit inside a Title Case axis title
stays a unit (`Dose (mGy)`). **What this rule is not about:** the caption
sentence in `plan/captions.md` and any running prose — those are sentences.

A raw column name is not a label. `aes(x = temp_c)` is fine; `labs(x =
"temp_c")` is not, and a plot that falls back to the column name has no axis
title yet. This is a standing preference that used to live only in the
maintainer's own notes, which no skill and no project loads, so every rendered
figure shipped lower case (item 83).

## Where this sits relative to the other skills

- **`create-graphic-figure`** owns the drawn panels: the `.pptx` round trip and
  the render backends. This skill owns the script that *composes* them. They
  meet at `panels.R`, which `graphic_figure.py add` generates and this skill
  then edits. Neither hand-writes a `.pptx` or a render backend.
  **Never draw a schematic in `annotate()` calls.** A rectangle-and-arrow
  diagram built from `annotate()` renders perfectly and is then editable only
  by somebody who will edit R, which is not the person who asked for it. If the
  panel is art, hand it to that skill; `float lint` reports a float marked
  drawn that holds no PowerPoint source for the same reason (item 82).
- **`writing-engine`** owns the caption text, the callout checks and the
  cross-reference order. Read `plan/captions.md`; do not write it. A claim
  sentence is the paper's, and `prose.py captions` is what holds the two in
  agreement.
- **`idea-generation`** wrote the claim and the data contract in the first
  place. When a session concludes the claim is wrong, **that is an idea-stage
  finding** and it goes back to `plan/README.md` — not into the plot.
- **`setup-project-directory`** owns `plan/figures/_template/`. It is
  `_`-prefixed to keep it out of `render_all.R`'s discovery, and editing it
  changes every future slot. Refine the slot, never the template.

## Things that are wrong to do here

- **Refining without reading the claim.** Then it is decoration and the loop has
  no stopping condition.
- **Tuning the plot to the mock generator**, or widening an axis to make a
  difference look bigger.
- **Removing a mock-data safeguard** to get a clean preview.
- **Fixing a claim problem in the figure.** The plot is not where a weak claim
  gets repaired.
- **Rewriting `_template/` in place.**
- **Editing several things between renders**, then diagnosing a script that no
  longer runs.
- **Reading the exit code instead of the image.** All eight traps render
  successfully.
- **Turning the lint into a gate**, or reporting its findings as reasons the
  figure cannot be built.
- **Hand-writing a caption, a `.pptx`, or `float_provenance.json`.** Each has an
  engine, and a hand-made one is wrong in ways nothing reports.

## When the Engine Itself Is Wrong

**File it. From this stage, not only from the writing one.** If the engine does
something during refining a float that it cannot justify — refuses a valid input, reports
a number it cannot have measured, writes a file in the wrong place, exits 0 on
a check that never ran — that is a defect in the toolkit, and it is lost with
this session unless you record it:

```bash
python <tools>/manuscript.py log-issue "<project>" --stage figures     --code a_short_slug --detail "what happened, GENERICALLY"     --example "what happened on THIS project"     --title "one line" --wanted "what it should do"     --verify "how the next run answers 'is it still there?'"
```

Three things about this command, and each of them has cost something already:

- **`--stage figures`.** Unstated, the stage is inferred, and the inference is
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
