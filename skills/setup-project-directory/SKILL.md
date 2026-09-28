---
name: setup-project-directory
description: Scaffold a new scientific-paper project into the universal structure - plan/, data/, drafts/, obsolete/, the R float pipeline, and project.yml - so every later skill can assume where things live. Use when the user wants to set up, start, or scaffold a paper/manuscript/research project, or asks to fix up an existing project folder that is missing pieces. Also use when another skill needs a project directory to write into.
---

# setup-project-directory

Creates the project structure every other skill in this toolkit reads. The
layout is a contract, not a preference: `plan/captions.md` is always the caption
source of truth, `data/analysis/analysis.md` always holds the numbers,
`plan/outline.md` is always one line per paragraph. Skills downstream depend on
that being true without checking.

Spec: `specs/setup-project-directory.md`. Read it before changing behaviour here.

## The engine does the work

**Never create the tree by hand, and never write a template file yourself.** The
manifest lives in `tools/scaffold.py` and that file is the contract; a
hand-made folder will be subtly wrong in a way nothing reports until a later
skill fails on it.

```bash
python <tools>/scaffold.py scaffold "<path>" --json [--title ...] [--field ...] \
                                             [--journal ...] [--pi ...] [--dry-run]
python <tools>/scaffold.py check "<path>" --json     # what is present / missing
python <tools>/scaffold.py tree  "<path>"            # print the annotated tree
python <tools>/scaffold.py github "<path>" --json [--owner ORG] [--url URL]

python <tools>/scaffold.py adopt "<path>" --json [--expects-data ...] [--apply]
python <tools>/scaffold.py survey "<path>" --json     # what the tree does not explain

python <tools>/scaffold.py mock-floats   "<path>" --bundle mock.json --json
python <tools>/scaffold.py image-floats  "<path>" --bundle panels.json --json
python <tools>/idea.py     mock          "<path>" --bundle mock.json --json
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
do not wait for a reply, and do not raise it again later in the session. Do not fall back to writing files
yourself.

The engine is safe to run against anything: **it never overwrites a file that
exists.** A re-run on a finished project is a no-op; a re-run on a damaged one
restores only what went missing.

## Entry modes

| Situation | What to do |
|---|---|
| "set up a project for X" | Ask for the name and parent directory, then scaffold |
| Invoked with a path to an empty directory | Scaffold in place, no questions beyond the metadata |
| Invoked with a path that already has files | **Use `adopt`, not `scaffold`.** It does everything `scaffold` does and then says what to do with the files that were already there. See "Adopting a folder that already exists" |
| "fix up / reorganise this folder", "my folder is a mess", "files everywhere" | **`reorganize-directory`**, the skill for a folder that already has months of work in it. It calls the same `adopt` and then does the three things this skill does not: depth, the sweep into `sandbox/unsorted/`, and the figure-order read |
| Invoked by `idea-generation` | The path is already decided. Skip the ask, scaffold, connect it to GitHub, hand straight back |
| "put this project on GitHub", "sync this between my computers", or an existing project with no `.git` | **`github` alone** — see "GitHub". It adds the sync to a project scaffolded before it existed and never touches the user's files |

## Adopting a Folder That Already Exists

The common case, not the exception. Somebody has been collecting data for four
months and now wants the pipeline around it; the folder is not *wrong*, it is
*unfiled*. `adopt` is `scaffold` plus a proposal for everything the structure
does not explain.

```bash
python <tools>/scaffold.py adopt "<path>" --json [--expects-data ...] [--field ...]
```

It scaffolds first — so a proposal can name a destination that exists — then
classifies every top-level entry that is not part of the tree. **It moves
nothing.** Read `extras` and work through it with the user.

| `action` | What it means | What you do |
|---|---|---|
| `move` | The rules are certain. A `.csv` is data; a `.dm4` is an instrument image | Show them, then offer `--apply` |
| `ask` | A judgement call. A `.pdf` is a paper you are reading or a figure you exported; `old_figs/` is retired work but `obsolete/` has four bins | Ask, one line each. Then move by hand |
| `conflict` | The destination already holds that name | Never resolved automatically. Two files with one name, and only the user knows whether they are the same file |
| `keep` | Fine where it is — talks, grant admin | Say nothing unless asked |

Then, and only if the user says yes:

```bash
python <tools>/scaffold.py adopt "<path>" --apply --json
```

`--apply` moves the `move` rows and nothing else. **It never deletes and never
overwrites**, with or without the flag.

### How to Run the Conversation

1. **Report the structure first.** "52 pieces added, nothing overwritten" is
   the reassuring half and it is true; lead with it.
2. **Then the certain moves, as a list, and ask once for all of them.** Not one
   question per file. Four CSVs into `data/raw/` is one decision.
3. **Then the judgement calls, one line each**, with the engine's `why`. These
   are short questions with real answers — "is `figures/` your old renders, or
   source images?" — and the answer changes where things land.
4. **Do not move the ask rows on their behalf, even when it seems obvious.**
   This skill adds; it does not reorganise somebody's work on a guess.

**A `.bib` file is never merged automatically.** It lands as an `ask` with the
destination `drafts/references.bib`, because merging two bibliographies means
resolving duplicate citekeys and that is a decision, not a copy.

**A folder holding old drafts is the one worth slowing down on.** `obsolete/`
has `figures/`, `tables/`, `analysis/`, `notes/` and `drafts/`, and which bin a
folder called `old/` belongs in is usually several answers rather than one.

### When the Folder Contradicts the Declaration

A `.csv` in a project that declared `expects_data: none` comes back as an
`ask` saying so on its face:

> a table of measurements — but this project declares `expects_data: none`, so
> `data/raw` does not exist. Either this is not what it looks like, or the
> declaration is wrong.

**Surface that, do not resolve it.** One of the two is wrong and the user knows
which. The fix for the second is to edit `project.yml` and re-run `adopt`.

### What `adopt` Does Not Do

- **It does not go deeper than the top level, here.** `--depth` exists and
  `reorganize-directory` is the skill that uses it; from this skill the walk
  is the top level, because a folder somebody is *starting* a project in has
  no four months of `misc/` in it.
- **It does not rename anything.** Only moves, and only whole entries.
- **It does not touch anything already in the manifest.** A `README.md` at the
  root is the scaffold's own; it is never classified — with one exception the
  engine now reports rather than hiding: a file whose name differs from a
  manifest entry only in **case** satisfies neither. It is reported as a
  `NAME COLLISION`, the template is not written over it, and `check` counts
  the entry as missing (item 113).

### The Folder With Four Months of Work In It Is a Different Skill

`adopt` is two thirds of it and it stays here, unchanged and still called by
this skill. What **`reorganize-directory`** adds is the part this one has no
business doing on a fresh project: `--depth`, `--sweep-unknown` into
`sandbox/unsorted/`, `float-order`, and the end-of-run report of what the
folder can and cannot now do. Route there when the sentence is *"my folder is
a mess"* rather than *"set up a project"*.

## What to ask

Keep this short — six questions, and every one of them has a working default.
Nothing here is load-bearing enough to block on, because **every value is
recorded in `project.yml` and can be edited there afterwards.** If the user is
unsure, take the default, scaffold, and say which default you took.

| Ask | Default | Why it matters |
|---|---|---|
| Project name | — | Becomes the folder name |
| Parent directory | The nearest `Projects/` above the working directory | Where it lands |
| Field: chemistry / biochemistry / other | `chemistry` | Selects the `data/methods_facts.yml` template — a cryo-EM block or an instrumentation block |
| Target journal | blank → `generic` geometry | Selects figure column widths, dpi, and base font in `theme_journal.R` |
| What will this hold: measurements / images / neither? | `measurements` | The adaptivity axis. It decides whether `data/` is scaffolded at all, and which of the two head-start offers below is even on the table |
| Mock data? | no | Synthetic rows shaped like the hypothesis, so figures can be built and criticised before any sample exists. **Offered only for `measurements`** |
| Mock figures and tables? | no | Fills the pre-made slots, so `render_all.R` produces a real preview on day one. **Offered only for `measurements`** |
| Image placeholder figures? | no | The `images` counterpart: figure slots laid out as grey panels saying what image is expected in each. **Offered only for `images`** |
| GitHub: whose account? | the user's own, **private** | Where its repository is created. Ask once, with the default stated; an organisation needs `--owner`. "No GitHub" is a real answer: skip the step and say the project is on this computer only |

Also pass `--title` and `--pi` when the user has already said them. A working
title is fine; it is not a commitment.

The last two are unlike the others: they add files rather than record a
value, so they default to **no** and are covered in "Mock data and mock
floats" below. Ask them once, together, after the tree is reported — a user
who wanted a bare folder should not have to decline twice.

### What the Project Will Hold — Ask It, Do Not Infer It

`--expects-data` is the third axis, and it is not either of the other two.
`--paper-kind` is what the document **is**; `--field` is what it is **about**;
this is what the project will **hold**.

| | What it means | What the tree does |
|---|---|---|
| `measurements` | Rows in a table. The default | Everything, unchanged |
| `images` | Micrographs, maps, gels, spectra read as pictures | Keeps `data/raw_images/` and the analysis pipeline; **drops `data/mock_data/`** |
| `none` | A perspective, a theory paper, a methods commentary | **No `data/` at all**, and no `obsolete/analysis/` |

Ask it in one line, with the examples, because the words on their own do not
land:

> What will this project hold — measurements you can put in a table, images
> (cryo-EM, SEM, gels, micrographs), or neither, because it argues from the
> literature?

**It is declared, never sniffed.** An empty `data/` and "this project will
never have data" look identical on disk, and one of them is a scaffold nobody
has filled in yet. This is the same rule `manages` follows and for the same
reason.

**Read `offers` out of the JSON rather than re-deriving the rule.** The
scaffold result carries it:

```json
"expects_data": "images",
"offers": {"mock_data": false, "mock_floats": false, "image_placeholders": true}
```

A `false` there means **do not ask**. An imaging project that gets asked
"want me to generate mock data?" has been told the tool does not understand
what it is looking at — and a cryo-EM dataset is exactly the case where
synthesising the evidence would be the worst thing this engine could do.

An answer given here is written into `project.yml` and never asked again. If
it turns out to be wrong, the fix is to edit `project.yml` and re-run —
re-running adds what the new answer needs and **never deletes what the old one
made**, so a project that switches from `none` to `measurements` gains `data/`
and a project that switches the other way keeps it. Say so; deleting is the
user's call and this skill does not delete.

**`none` does not mean no figures.** Four figure slots and two table slots are
still created. A theory paper's figures are drawn art, and
`create-graphic-figure` is how they get made.

**Naming.** Recommend `snake_case` with no spaces for the project folder, and
say why in one clause — unquoted paths with spaces break `Rscript` and `pandoc`
invocations silently. Recommend, do not insist: the parent folders here already
contain spaces and the scaffold handles them.

**If there is no `Projects/` directory above the working directory**, propose
creating one inside the nearest `<PI> - <Field>` folder and confirm before
creating it. Do not invent a location silently.

## Running it

Call with `--json` and read the result rather than parsing the printed text:

- `created` — every path written, `/`-suffixed for directories
- `skipped` — every path that already existed and was left alone
- `already_set_up` — true when nothing needed doing
- `was_populated` — the directory had files in it before this run
- `ignored_flags` — flags that contradicted an existing `project.yml`

`ignored_flags` is not an error and not something to retry. `project.yml` is
never overwritten, so what it already records wins over a flag. **Surface the
contradiction and tell the user to edit `project.yml` directly** — do not
re-run with different arguments, and do not edit the file on their behalf
unless they ask.

Use `--dry-run` first only when the target already has files in it and the user
seems uneasy about it. Otherwise it is a wasted round-trip; the engine cannot
overwrite anything.

## GitHub

**Every new project gets its own private repository by default**, straight
after the scaffold and before the report, so the first commit is the
scaffold itself:

```bash
python <tools>/scaffold.py github "<path>" --json [--owner ORG]   # proposes a name
python <tools>/scaffold.py github "<path>" --json [--owner ORG] --repo NAME
```

**Confirm the repository name with the user before anything is created.**
Run `github` without `--repo` first: when it would create a repository it
creates nothing, returns `needs_confirmation` and a `proposed_repo` slug of
the folder name, and stops. Ask:

> I'll put this on GitHub as a **private** repository named
> **`<owner>/<proposed_repo>`**. OK, or would you like a different name?

Re-run with `--repo <the name they confirmed>` only after they answer. Never
pass `--repo` with a name the user has not seen — renaming a repository later
breaks every clone of it.

It then makes the folder a repository, creates the private GitHub repository
with `gh`, and runs the first sync. From then on the project keeps itself in step:
the hooks in `.claude/settings.local.json` run `.claude/hooks/sync.sh` when a
session opens (commit what the last one left, pull, push) and when it ends
(`/exit`, `/clear`). Say that in one sentence; the user does not need the
mechanism.

**Large data never reaches GitHub, and say so the first time.** Raw cryo-EM
formats (`.eer`, `.dm4`, `.mrcs`, `.st`, `.ali`) are ignored by the template,
and before every commit the sync adds any new file of 50 MB or more, or new
folder of 500 MB or more, to `.gitignore`. It stays on the computer that made
it. The next session opens with a `Project sync:` line listing what was kept
off. **Relay it and ask whether that is right**; if the user wants one on
GitHub, delete its line from `.gitignore`. Analyses that read raw data run on
the machine that holds it, which is usually the lab computer.

Read `problems` in the result; each is a sentence the user can act on:

| Problem | What to do |
|---|---|
| `gh` not installed | Offer to install it — `sudo apt install gh` on Linux, `winget install GitHub.cli` on Windows — then `gh auth login` (HTTPS, log in with a browser), then re-run. Or the user creates an **empty** private repository at github.com/new and you re-run with `--url` |
| `gh` not logged in | `gh auth login`, then re-run |
| git has no name/email | Offer the two `git config --global` lines with their name and email filled in, then re-run |
| settings file is not valid JSON | Show the user the error; do not rewrite their file |

`github` is safe to re-run: it only adds what is missing, keeps an existing
remote, and brings the sync script up to date. **Never put a project that is
a git repository inside a OneDrive- or Dropbox-synced folder** — two sync
tools moving the same files corrupt `.git`. If `<path>` is inside one, say so
before connecting and suggest an ordinary folder.

### In a Cloud Session

Idea generation needs nothing opened on the user's machine, so it runs well in
a cloud session, and the project can be born there. Three differences:

- **The session cannot create a repository and has no `gh`.** Ask the user to
  create an **empty private** repository at github.com/new (no README, no
  `.gitignore`, no licence), suggesting the name you would have proposed,
  and tell you the name they chose. Attach it to the session,
  clone it, and scaffold **into the clone** — the clone already has its
  remote, so `github "<clone>"` skips creation and just installs the sync and
  pushes.
- **Push to `main`** — the project's only branch — not a session branch.
- **The literature engines need network access** the environment may not
  allow. If `scholar.py` or `pubmed.py` cannot connect, name the hosts it
  could not reach and tell the user to add them under the environment's
  network settings. Do not substitute search results from memory.

Then tell the user the one step left on their own computer: `git clone` the
repository into their projects folder and open a session there. The sync
takes over from that point.

## Reporting back

Print, in this order:

1. **The tree** — `scaffold.py tree "<path>"`, which annotates the load-bearing
   entries (`.here`, `plan/captions.md`, `plan/outline.md`, `project.yml`)
2. **What was skipped**, if the directory already had files, with "nothing was
   overwritten" stated explicitly
3. **What to do next**, in this order and no more than these five — the sixth
   exists only on a machine that has a lab pack, and is the one line the user
   did not ask for and will use most:
   - `plan/README.md` — background, question, hypothesis, design. The living document
   - `data/data_contract.md` — name every column and its **kind** before any data lands
   - `plan/outline.md` — one line per paragraph, each with its evidence in brackets
   - `plan/author_information/authors.md` — who is on this and in what order.
     Two minutes now, while the answers are obvious; see below for why it is on
     this list
   - `Rscript plan/render_all.R` — renders nothing yet, and should still run clean
   - `plan/lab_pack_brief.md` — **only if this lab has a resource pack**, and
     only once it has been written: the lab's own steps, quoted and dated,
     with `## Your Notes` at the end for what you learn at the bench

Then offer the two optional steps below — mock data, and mock figures and
tables from it — in one question, not two rounds.

Say the thing that is easy to miss: `plan/README.md` is the workhorse and the
root `README.md` is only orientation for someone opening the project folder.
They are not the same document and should not be merged.

**Offer `plan/author_information/authors.md` and `plan/author_information/affiliations.md`
as a fifth thing to do, and say why now rather than later.** They are scaffolded blank, and blank is
where a project starts rather than where it should sit. The author list is
usually the most settled thing about a project on day one, so filling it in
costs two minutes at the moment the answers are obvious. The alternative is the
writing engine stopping mid-round to ask for a coauthor's ORCID.

Offer it; do not insist. A project whose author list genuinely is not settled
is a real project, and the engine will ask when it gets there. What the two
files hold: the author list, the contribution matrix, the addresses and the
grants. They live in `plan/` rather than in the source text because the
source-text folder is renamed forward every round and an author list is not a
per-round thing.

They sit in `plan/author_information/`, with `conflict_statements/` beside
them — an inbox for the **signed competing-interest forms**, one file per
author, named with that author's initials. Its `README.md` says so; mention it
only if they ask what the folder is for, or when a submission is close. Most
journals will not send a paper out for review until every form is in, and
`manuscript.py submission-package` counts them and says who is outstanding. It
reads the file names and never opens a form.

`data/analysis/analysis.md` is **not** scaffolded, and that is deliberate — it
is generated by `Rscript data/analysis/analysis.R`. A project that has not run
its analysis honestly has no analysis file, and the `analysis` skill is what
fills in the two sections that script needs.

### Offer the Facility Prefill, If Anything Holds One

The project now has a `data/methods_facts.yml` with nothing in it. Somebody has
usually written down what this lab's instruments are and what settings they run
at — in the lab's **resource pack**, or in a **drop-zone** folder on this
machine — and re-typing that per paper is how a manufacturer's city ends up
missing from a methods section.

Ask what is available before offering anything, because the answer is often
"nothing", and an offer of a list that turns out to be empty is worse than no
offer:

```bash
python <tools>/scaffold.py prefill "<the project>" --json
```

With no `--instrument`, it lists rather than writes. It returns `available` —
every instrument block, merged across the layers — and `layers`, which names
each layer and whether it held anything.

- **`available` is empty.** Say nothing and move on. No pack and no drop-zone is
  the ordinary case for a new member and is not an error.
- **`available` has blocks.** Name them, say which layer each came from, and ask
  which ones this paper will actually use. Then:

  ```bash
  python <tools>/scaffold.py prefill "<the project>" --instrument "<a block name>"
  ```

**Say this out loud when you write any of it, because it is the whole safety
of the feature:** every value arrives **commented out**, tagged with the file
and layer it came from, and **counts as MISSING until a human deletes the
`#`**. A prefilled detector model that nobody checked is a fabricated methods
section that reads perfectly, which makes it the most dangerous kind of wrong
this toolkit can produce — it is *usually* right. A value the project already
records is never overwritten, and the block says so where it declined.

**Offer the lab's blocks first and the machine's second.** A pack is one lab's
curated record and a drop-zone is whatever the person at this keyboard put
there; when both name the same instrument, the pack's value wins and the
drop-zone's is still printed, so a member can see that two sources disagreed.

### Write the Lab Pack Brief, If This Lab Has a Pack

Different thing from the prefill, and it runs whether or not the prefill found
anything. Spec: `specs/lab-pack-brief-2026-09-24.md`.

```bash
python <tools>/labpack.py brief --project "<the project>"
```

It writes `plan/lab_pack_brief.md`: **every step of this lab's route, quoted
from the pack verbatim and stamped with the pack's curation date**, the
instruments those steps name, and an index of everything else the pack holds.
A new member's first question at the bench is *how is this actually done here*,
and this is the file that answers it inside the project rather than three
folders away.

**No bundle here, and that is not a degraded version.** The bundle is the
project's half — the decisions and the open questions — and on a folder
scaffolded ten minutes ago there are none. Every step then carries *"nothing
project-specific is recorded for this step yet"*, which is true and is an
invitation. `idea-generation` fills those lines in at its Stage 6 and
**carries the `## Your Notes` section forward unchanged**, so writing it now
costs nothing later.

Four rules, all of them the engine's:

- **No pack, no file, and no sentence about it.** Skipped in silence, like
  every other pack behaviour. Two packs stop and ask, once.
- **It is a source and never a result.** Say the one line that matters when
  you report it: *nothing in it is a methods fact* — the file says so in its
  own header, every quoted block carries the pack's date, and if a number in
  it contradicts what the user knows, the user is right.
- **It never goes under `data/`**, and the engine refuses that destination by
  name. `plan/` is where it lives.
- **`## Your Notes` at the end is the user's.** Tell them it is there: it is
  where an answer they get at the bench should be written down, and no
  regeneration touches it.

## Verifying

`Rscript plan/render_all.R` on a fresh scaffold is the one-line proof that the
pipeline works. It prints `6/6 scripts ok` — the six pre-made slots each run
and report themselves unbuilt — then `preview.html   0 floats  (6 empty
slots)`, and writes `plan/preview.html` and
`plan/floats/figures_and_tables.docx`. Offer it; run it if the user says yes.

**R is frequently installed but not on `PATH` on Windows** (measured here: R
4.6.0 lives at `C:\Program Files\R\R-4.6.0\bin\Rscript.exe` and `which Rscript`
finds nothing). If `Rscript` is not found, look under `C:\Program Files\R\*\bin\`
before reporting that R is missing.

If a package is missing, `render_all.R` prints a single `install.packages(c(...))`
line covering everything at once. Hand that line to the user rather than
installing packages for them.

## Mock data and mock floats

A freshly scaffolded project renders clean and produces nothing. That is
correct — and it means nobody can tell whether the pipeline works, and nobody
sees the house style until they have hand-built a figure. Two optional steps
fix that, and both are **asked, never assumed**.

**Both are for `expects_data: measurements` only.** Check `offers.mock_data` in
the scaffold result before opening your mouth. An imaging project takes
"Image Placeholder Figures" below instead, and a `none` project takes neither —
neither is a gap and neither gets mentioned.

> Want me to generate mock data for this project? It writes a generator you
> own, produces synthetic rows shaped like your hypothesis, and lets you build
> and criticise every figure before a single sample is prepped.

> And mock figures and tables from it? Four figures and two tables in the
> pre-made slots, so `render_all.R` produces a real preview on day one.

Take **no** for an answer on either. An empty project is a working project;
these are a head start, not a requirement. Mock figures without mock data is
not a combination — say so and offer the data step first.

### Why this is safe, and why to say so out loud

Every mock file lands in `data/mock_data/` with a `_mock` suffix. That suffix
is not a naming convention, it is the safety mechanism:

- `load_data()` records the path it read from
- `save_float()` stamps **MOCK DATA** across any figure downstream of one
- `create_floats.R` **refuses** to build the co-author `.docx` at all
- `manuscript.py assemble` blocks the manuscript on the same signal

**Tell the user this in one sentence**, because the fear is reasonable and the
answer is good: mock data cannot reach a coauthor by accident. The way to get
rid of a mock float is to point its script at `data/raw/`, which is also the
way to turn it into the real one.

### Step 1 — the generator, via a subagent

**Dispatch a subagent to design the mock spec.** Deciding what columns a
project has, what kinds they are, and what effect size the hypothesis implies
is judgment over the user's field, and it wants its own context window rather
than a paragraph of this one. Give the subagent:

- the project path, and `data/data_contract.md` if the user has filled it in
- whatever the user has said about the question, the design, and the outcome
- the bundle schema below, and the instruction to return **only** that JSON

```json
{"mock": {"hypothesis": "one sentence", "n": 12, "seed": 1},
 "data_files": [{"file": "measurements",
                 "group_column": "catalyst",
                 "columns": [
                   {"name": "catalyst", "kind": "categorical",
                    "levels": ["A", "B"]},
                   {"name": "temperature_c", "kind": "continuous",
                    "mock": {"A": [70, 20], "B": [70, 20]}},
                   {"name": "yield_pct", "kind": "continuous",
                    "mock": {"A": [42, 4], "B": [71, 4]}},
                   {"name": "turnover_number", "kind": "count",
                    "mock": {"A": 180, "B": 320}},
                   {"name": "selectivity", "kind": "proportion",
                    "mock": {"A": [0.61, 0.07], "B": [0.88, 0.05]}}]}]}
```

`kind` is one of `continuous`, `count`, `proportion`, `categorical`,
`ordinal` — the same vocabulary as `data/data_contract.md`, which is where the
kinds come from when the contract exists. `mock` is per group: `[mean, sd]` for
continuous and proportion, a mean rate for count. A column with no `mock` block
is written blank on purpose and reported as unspecified.

**Without sub-agents**, design the spec inline in this context, or as a
separate invocation when the data contract is long enough to crowd out the
scaffolding conversation. This dispatch buys context *budget*, not blind
isolation: the subagent is told the hypothesis on purpose, so nothing is
compromised by doing the work here and there is no check to skip. That is the
opposite of `writing-engine`'s stats-check, which must be skipped rather than
run in a context that has already seen the hypothesis.

**The numbers are the point.** They encode what the hypothesis predicts, so a
figure built on them is a test of the figure *and* of the claim. If the plot
does not visibly show what the caption asserts, either the figure design is
wrong or the claim is weaker than it sounded — and that is worth learning in an
afternoon rather than after a month of instrument time. Tell the subagent to
choose an effect size the user would actually expect, not a dramatic one.

Then run the engine. **Never write `generate_mock_data.py` yourself** — the
`_mock` suffix contract lives in one place, and a hand-written second copy
would get it right until the once it did not:

```bash
python <tools>/idea.py mock "<project>" --bundle mock.json --json
```

Read the result: `action` (`create` / `skip` / `overwrite`), `unspecified`
(columns the hypothesis said nothing about — surface these), and `mock.ran`
with the rows written. It **never overwrites** an existing generator, because
once it exists it has been edited and the edits are the hypothesis; `--force`
is the deliberate override.

### Step 2 — the floats

```bash
python <tools>/scaffold.py mock-floats "<project>" --bundle mock.json --json
```

Same bundle, no second design pass. It fills the pre-made slots — one float,
one folder, exactly the layout `writing-engine` expects:

| | |
|---|---|
| Figure 1 | **two-panel composite** — a relationship beside a comparison, with A/B tags |
| Figures 2–4 | one outcome column each, by group |
| Table 1 | every numeric column, mean ± SD by group |
| Table 2 | the column inventory — types and observed ranges |

Figure 1 is the composite deliberately: `panel_title()`, the bold A/B tags, the
shared annotation style and the inside legend are the house style, and a
one-panel example does not teach them.

It also appends a caption block per float to `plan/captions.md`, claim-first,
each saying on its face that it is mock. **Only slots still holding the unbuilt
template are filled** — a script anyone has worked on is never touched, with or
without `--force`.

Then render, and say what the user is looking at:

```bash
Rscript plan/render_all.R      # 6/6 scripts ok, preview.html [MOCK DATA]
```

The co-author `.docx` will report `REFUSED — built from mock data` and write
nothing. **That is a pass, not a failure.** Say so in the same breath as
reporting it, or it reads as a broken scaffold.

## Image Placeholder Figures

**The `images` counterpart of the mock floats, and it is not mock.** A cryo-EM,
SEM, gel or spectroscopy project has the same problem a measurements project
has on day one — nothing renders, nobody can see the house style, and the
figure set is imaginary — and the mock route cannot solve it. **Do not
synthesise a micrograph.** This engine cannot make one honestly and a
fabricated one would be the worst file it could write.

What it can build is the figure **structure**: the panel grid, the A/B/C tags,
the titles and the caption, with every panel a light grey box saying what image
is expected in it.

> Want me to lay out your figures now? Each panel comes out as a grey box
> saying what image belongs in it, so the figure set is real and reviewable
> before a single grid is frozen — and each panel fills itself in when you drop
> the .png into the figure's folder.

### Designing the Panels

**Dispatch a subagent**, for the same reason the mock spec gets one: deciding
what a paper's figures should be is judgment over the user's field. Give it the
project path, whatever the user has said about the question and the technique,
and the bundle schema:

```json
{"figures": [
  {"n": 1,
   "title": "Sample quality and 2D classes",
   "claim": "Vitrified particles are monodisperse and give interpretable 2D class averages.",
   "panels": [
     {"file": "panelA_micrograph.png", "title": "Representative micrograph",
      "describe": "Motion-corrected micrograph at 1.2 um defocus, 100 nm scale bar",
      "aspect": 1.0},
     {"file": "panelB_classes.png", "title": "2D class averages",
      "describe": "Top 20 2D class averages, box 256 px"}]}
]}
```

`describe` is the only field worth asking a human for, and it is the point of
the whole command. A panel named `panelB.png` with no sentence under it renders
a grey box reading "pending" — which is exactly the uninformative placeholder
this replaces. `aspect` is height ÷ width and locks the box to the shape the
real image will have, so the figure does not reflow when the image lands;
`title` and `file` both have working defaults.

**Say what the technique actually produces.** "A micrograph" is not a
description; "motion-corrected micrograph at 1.2 µm defocus, 100 nm scale bar"
tells the person collecting it what to collect. That is the second thing this
buys, after the layout.

```bash
python <tools>/scaffold.py image-floats "<project>" --bundle panels.json --json
```

Read `written`, `captions`, and **`undescribed`** — every panel that came
through with no sentence. Surface those; they are the ones that will render as
a bare "pending".

Same never-overwrite rule as everywhere else: only a slot still holding the
unbuilt template is filled, and `--force` reclaims one this command wrote while
still refusing a real figure.

### Say These Two Things Out Loud

**It is not mock, and the `.docx` builds it.** There is no watermark and no
refusal, because nothing here is fabricated — a panel whose image has not
arrived says so on its own face, inside the figure. That is the honest version
of "not collected yet", and it means the figure set can go to a coauthor
before the microscope time.

**A caption block is written per figure**, claim first, with one sentence per
panel saying what it is waiting for. That is what lets `writing-engine` draft
the paragraph that calls out Figure 2B before 2B exists.

Then render:

```bash
Rscript plan/render_all.R      # 6/6 scripts ok, and the placeholders compose
```

To finish a panel: save the image into the float's own folder under the
filename in the script, then delete that panel's `describe =` argument.

### If a float needs drawn art, not a chart

A reaction scheme, an apparatus diagram, a mechanism, a sample-prep flow — none
of those are charts, and none belong in a mock figure script. **Hand off to
`create-graphic-figure`** rather than attempting art here or leaving a
placeholder in a plot.

**Record it when you make the slot**, so the ask outlives the sentence it was
said in: `float new … --art drawn` writes the drawn marker into the float's own
script and prints the `graphic_figure.py add` line to run next. The words
*graphic, schematic, diagram, scheme* and *illustration* in the user's request
settle it; `float lint` then reports a float marked drawn that holds no
PowerPoint source, because that is a picture the user cannot change. Then ask: 

> Figure 2 sounds like a scheme rather than a chart. Want me to set it up as a
> PowerPoint panel you draw yourself? (`create-graphic-figure` writes the slide
> into `plan/figures/Fig02/` and renders it into the figure.)

That skill writes `panelA_source.pptx` into the float's own folder plus a
`panels.R` that renders it whenever the slide changes, and it composes beside
plotted panels through `graphic_panel()`. `render_all.R` stays the only command
anyone runs. Art that does not exist yet renders as a dashed placeholder, so a
figure can be composed before any of it is drawn.

## Hand-off

After the tree and the next steps, ask:

> Scaffolded. Want to run **idea-generation** to work out what this paper argues
> and which floats it needs? (It writes into `plan/README.md` and
> `plan/outline.md`.)

If yes, invoke `idea-generation` with the project path. The hand-off runs both
ways — `idea-generation` can equally call this skill when the thinking came
first and there is no folder yet, which is at least as common.

Mention `drafts/rough_draft.md` once, and do not interview about it:

> One more thing: if you would rather write the paper yourself, `drafts/rough_draft.md`
> is where it goes. Anything under a heading there becomes the highest source of
> truth for that section and the drafter edits it rather than writing its own.
> Leaving it empty is a normal project.

## Things that are wrong to do here

- **Do not populate the project.** Scaffolding writes stubs and instruction
  blocks; filling in the research question, the outline, or the data contract is
  `idea-generation`'s job. An outline you invented is worse than an empty one,
  because the empty one is visibly empty.
- **Do not delete or move anything.** This skill only ever adds. Retiring work
  into `obsolete/` belongs to `writing-engine`.
- **Do not edit a scaffolded file to "fix" it.** If a template is wrong, fix it
  in `tools/project_template/` and re-run `python tests/scaffold.py`.
- **Do not skip `.here`.** It is a zero-byte file and every R path in the
  project resolves through it. The engine writes it; just never remove it.

## When the Engine Itself Is Wrong

**File it. From this stage, not only from the writing one.** If the engine does
something during scaffolding or repairing a project that it cannot justify — refuses a valid input, reports
a number it cannot have measured, writes a file in the wrong place, exits 0 on
a check that never ran — that is a defect in the toolkit, and it is lost with
this session unless you record it:

```bash
python <tools>/manuscript.py log-issue "<project>" --stage setup     --code a_short_slug --detail "what happened, GENERICALLY"     --example "what happened on THIS project"     --title "one line" --wanted "what it should do"     --verify "how the next run answers 'is it still there?'"
```

Three things about this command, and each of them has cost something already:

- **`--stage setup`.** Unstated, the stage is inferred, and the inference is
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
