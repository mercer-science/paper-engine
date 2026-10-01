<!--
  THE GUIDE. Written by hand; nothing checks the prose, so when a module
  changes this is the file that has to be remembered.

  IT IS MARKDOWN SO THAT GITHUB RENDERS IT. GitHub renders HTML in a repo
  view for nobody, so the styled page this
  replaced opened as a wall of markup for every member who followed the link.
  Markdown also needs nothing beyond the repository itself.

  So the formatting here is what GitHub's own renderer gives and nothing else:
  alert blocks, tables, collapsible sections. No HTML that GitHub strips (it
  drops style= and class=), no images, no external anything. It also has to
  stay readable as plain text in an editor, which is the other reason there is
  no cleverness in it.

  THE ROOT README IS THE SHORT VERSION AND IS USER-FACING ONLY - install it,
  say what you want. This file is the in-depth one: how it connects, what each
  skill does, and how the machinery works. Maintainer instructions are in
  ../CONTRIBUTING.md and belong neither here nor in the README.

  Links to a SKILL.md are RELATIVE on purpose - they resolve in the GitHub
  view, in a clone, and in an editor's preview alike.
-->

# Paper Engine

**From "there might be a gap here" to a formatted submission — without inventing anything on the way.**

A set of skills and command-line engines that take a research paper from a half-formed idea to a journal-formatted submission. You do not have to learn any of it to use it: say what you want in ordinary words, and [section 4](#4-just-say-what-you-want) is the whole interface. Everything after it is here for when you want to know what is actually happening.

---

### On this page

|  | Section | For |
|---|---|---|
| 1 | [What this is](#1-what-this-is) | the fifteen-second version, then how it is put together |
| 2 | [Install it once](#2-install-it-once) | connecting it, proving it connected, and fixing it when it did not |
| 3 | [The four stages](#3-the-four-stages) | how the work is shaped, and how the machinery works |
| 4 | [Just say what you want](#4-just-say-what-you-want) | **the only section you have to read** |
| 5 | [Stage A: idea and setup](#5-stage-a-idea-and-setup) | finding a gap, scaffolding a folder |
| 6 | [Stage B: your own work](#6-stage-b-your-own-work) | where things go, and the two figure skills |
| 7 | [Stage C: the writing engine](#7-stage-c-the-writing-engine) | outline, draft, check, build, answer the flags |
| 8 | [Stage D: submission](#8-stage-d-submission) | the package a journal wants |
| 9 | [What it learns from you](#9-what-it-learns-from-you) | the learning loop |
| 10 | [Every command, on its own](#10-every-command-on-its-own) | the reference you will come back for |
| 11 | [When something looks wrong](#11-when-something-looks-wrong) | and what is not built yet |
| 12 | [The rules that never bend](#12-the-rules-that-never-bend) | six lines |

---

## 1. What this is

A set of skills and command-line engines that take a research paper from a half-formed idea to a journal-formatted submission.

The **skills** are the judgment — what the gap is, what the paper should argue, whether a coauthor's edit should be applied. The **engines** are everything with a right answer: does this citation resolve to a real paper, does the draft fit the word limit, is every figure called out in order.

> [!IMPORTANT]
> **The one thing it will not do is make something up.** Every value it writes came out of a file somebody filled in, and where there is no value it writes a visible `**[FLAG: ...]**` instead of a plausible guess.

### The three moving parts

Knowing which of the three you are dealing with explains almost every question about the toolkit — including why some things need Python and some do not.

| Part | What it is | Where it lives |
|---|---|---|
| **Eight skills** | Markdown instructions your agent reads. They ask you things, decide things, and drive the engines. No skill counts anything itself | `skills/<name>/SKILL.md` |
| **Fourteen engines** | Plain command-line Python programs. No agent, no API key, no harness — you can run any of them yourself | `tools/*.py` |
| **Your project folder** | Every fact about your paper: the outline, the data, the captions, the journal's requirements, the round history | your own directory, nowhere else |

**The engines are the product; the skills are a script for driving them.** That is why Python is not optional. A skill's first move is almost always to shell out to an engine and read its JSON — so a machine with the skills and no Python has the judgment and none of the answers.

It is also why **your folder is the only memory**. The toolkit stores nothing about your paper. Everything it knows, it re-reads from your project every time — which is what lets a round pick up months later, on a different machine, in a session that has never seen the paper. The one exception is the writing rules it learns from your edits, and those stay on the machine that learned them ([section 9](#9-what-it-learns-from-you)).

---

## 2. Install it once

### 2.1 What has to be on the machine

**Four things, and Claude Code on its own is not enough.** The first three the toolkit cannot work around.

| Needed | What stops without it | Notes |
|---|---|---|
| **Git** | the install itself — `/plugin marketplace add` clones the repository | must be on `PATH` |
| **Python 3.12+** | every engine, so: everything | `python` on Windows, `python3` elsewhere |
| **pandoc** | building the `.docx`. Nothing else can do it | **must be on `PATH`** — `shutil.which("pandoc")` is the only lookup there is |
| **R** | the figure and table pipeline | does *not* need to be on `PATH` on Windows; a default `C:\Program Files\R\` install is found |

Then five Python packages, once:

```bash
pip install requests lxml pypdf python-pptx pillow
```

Only the first is needed for the common path — `requests` is what the literature engines search through, and they exit at startup with the pip line if it is missing. `lxml` reads tracked changes out of a `.docx`, `pypdf` reads a PDF reviewer report, `python-pptx` builds the help deck, and `pillow` converts a figure into a journal's upload format when its render is not already in one. **None of the five is needed to take a paper from an idea to a built `.docx`.**

The R packages are one line as well — and you do not have to get the list right up front. The first render prints exactly which ones are missing and the `install.packages` line that fixes all of them at once, before you have done any work.

```r
install.packages(c("ggplot2", "dplyr", "readr", "patchwork", "yaml", "here",
                   "jsonlite", "ragg", "png", "officer", "flextable",
                   "base64enc", "colorspace", "farver"))
```

> [!NOTE]
> **Genuinely optional:** PowerPoint or LibreOffice, for drawn artwork panels and the graphical abstract — with neither, the engine stops and asks you for a PNG exported by hand, which is a supported path and not a failure. And a free NCBI API key, which only raises the rate limit on PubMed. No account and no API key is required to use any of this.

### 2.2 Route 1 — the plugin

**Two routes exist and both are supported.** They install the same eight skills from the same eight `SKILL.md` files — the plugin does not carry a second copy of anything. Use the plugin if you are in the group and have Claude Code; use the clone if you do not have plugin access or you are working on the toolkit itself.

First, in the terminal you will start Claude Code from. Both lines are **steps, not troubleshooting**: each one prevents a failure whose error message does not mention it.

```powershell
# Windows PowerShell
$env:CLAUDE_CODE_PLUGIN_PREFER_HTTPS = "1"
$env:CLAUDE_CODE_PLUGIN_KEEP_MARKETPLACE_ON_FAILURE = "1"
```

```bash
# macOS / Linux
export CLAUDE_CODE_PLUGIN_PREFER_HTTPS=1
export CLAUDE_CODE_PLUGIN_KEEP_MARKETPLACE_ON_FAILURE=1
```

Then, inside Claude Code, two commands:

```
/plugin marketplace add mercer-science/paper-engine
/plugin install paper-engine@paper-engine-marketplace
```

**The repository is the marketplace**: there is no server and no token, and because the repository is public there is nothing to be granted.

You never need to know where the plugin landed on disk. Each skill resolves its own engine paths, starting from the directory the harness hands it.

### 2.3 Route 2 — the clone

```bash
git clone https://github.com/mercer-science/paper-engine.git
cd paper-engine
copy tools\.env.example tools\.env
python tools/install_skills.py install
python tools/install_skills.py status
```

`install_skills.py` writes a small pointer file per skill into your personal skills directory, naming this checkout's absolute path. It derives that path from its own location, so it is correct on any machine and under any username without editing anything. The pointer names the real `SKILL.md` rather than copying it, so **editing a skill needs no re-install** — only moving the toolkit or changing a skill's `description:` does.

Updating this route is three lines, every time:

```bash
git pull
python tools/install_skills.py install
python tools/install_skills.py status
```

> [!WARNING]
> **Do not skip the install step, and do not skip `status`.** The pointer is the only reason an agent standing in your paper's folder knows these skills exist, and each pointer names the checkout by absolute path — so renaming or moving the folder kills all six. **An agent that cannot resolve a skill does not report a missing skill. It writes the paper without it, and the result looks like work.** That happened here: the toolkit was renamed, `install` was not re-run, and every session for days drafted with no skills loaded and nothing looking wrong. `status` is the only thing that says so.

### 2.4 The NCBI key, which is optional

`tools/.env` holds a free NCBI key and a contact email. It only raises the rate limit on PubMed — every literature engine works without it, and the search engines run keyless across Crossref, OpenAlex, Europe PMC and arXiv regardless.

It is deliberately not in the repository, so each person makes their own from `tools/.env.example`. On the clone route that is the `copy` line above. On the plugin route there is nothing to do unless you want the key, in which case ask the agent — *"set up the NCBI key for the paper engine"* — and it will find the file beside the engines and tell you what to paste in.

### 2.5 Prove it connected

**Either route, the check is one sentence to the agent:**

> Check the paper engine is set up on this machine.

It will resolve an engine, run it, and tell you what it found. What good looks like: the engines answer, and on the clone route every skill reports `installed` and `current`.

| `install_skills.py status` says | Meaning |
|---|---|
| `installed`, `current` | this is the state you want |
| `stale` | the skill's `description:` changed since you installed. Re-run `install` |
| `missing` | no pointer at all — the skill will never be offered |
| a path that does not exist | the toolkit moved or was renamed. Re-run `install` from its new location |

The other half of proving it is to watch it get *used*. When a skill fires, the agent names it before doing anything. If you ask for something in [section 4](#4-just-say-what-you-want)'s left column and nothing is named, the skills are not loaded — go back to `status`.

### 2.6 When the install does not work

| What you see | What it actually is |
|---|---|
| `/plugin marketplace add` fails with an SSH or host-key error, on a repository you can read | GitHub shorthand clones over SSH by default. Set `CLAUDE_CODE_PLUGIN_PREFER_HTTPS=1` and try again |
| It worked, then quietly stopped picking up changes | the background refresh runs with git credential helpers disabled, so a private marketplace can stop refreshing without saying so. `CLAUDE_CODE_PLUGIN_KEEP_MARKETPLACE_ON_FAILURE=1` keeps the last good copy working. To find out whether it has happened to you, [11.1](#111-am-i-behind) — that check uses your own login and is not affected |
| A skill is never offered, or the drafts look thin | the pointers. `install_skills.py status`, then `install` |
| `ModuleNotFoundError: requests` | the pip line in [2.1](#2-install-it-once) |
| `python` is not recognised | `python3` on macOS and Linux; on Windows, Python is not on `PATH` |
| The build fails saying pandoc is missing | pandoc has to be on `PATH`; nothing else can build the `.docx` |
| A render says R is missing on a machine where R is installed | R is often not on `PATH`. The engines look under `C:\Program Files\R\*\bin\` first; if yours is elsewhere, put `Rscript` on `PATH` |
| A slide will not render to PNG | no PowerPoint and no LibreOffice. Export it by hand — same result, thirty seconds |

If it is none of those, say what happened to the agent and let it file the defect ([section 11](#11-when-something-looks-wrong)).

### 2.7 Where to start a session

**Start it in the paper's own folder, never in the toolkit.** The skills find their engines by themselves; what they cannot do is guess which paper you mean. A session that starts in the toolkit has to be told the project by path every time, and the toolkit folder is also the one place where an accidental edit hits everybody's copy instead of your paper.

### 2.8 Two things that destroy work

> [!CAUTION]
> **Never run `/plugin uninstall`.** It deletes the plugin's data directory, and that takes your learned writing rules and any unsent defect reports with it — no prompt, no warning, and **no upstream copy exists anywhere, by design**. To move machines or reinstall, copy that directory out first. This is an open defect, not intended behaviour; see [section 11](#11-when-something-looks-wrong).

> [!CAUTION]
> **Never pass the toolkit around as a folder** — not over OneDrive or a shared drive, not as a zip, and not with `/plugin marketplace add <a local path>`. A directory source is a filesystem *copy*, so it carries the files this repository deliberately does not ship: somebody's NCBI key, their own learned drafting rules, and copyrighted PDFs. `.gitignore` only applies to a route that goes through git. That was measured, not assumed. Git or the plugin; nothing else.

---

## 3. The four stages

There is a picture of this: [`paper_engine_overview.pptx`](paper_engine_overview.pptx) in this folder — nine slides, one click per step, including the two feedback loops nobody expects.

| Stage | What happens | What exists at the end |
|---|---|---|
| **A — idea and setup** | Find a gap that is real and reachable; scaffold a folder for it. | A project directory, a recorded question and hypothesis, a planned float set, a verified reading list, and mock data shaped like the hypothesis. |
| **B — the work** | You do the science. The aids get out of the way. | Real data in `data/raw/`, rendered floats, your own analysis notes. |
| **C — the writing engine** | Outline, draft, check, build. Several passes, most of them checks. | A `.docx` a coauthor can read, with everything still outstanding listed inside it. |
| **D — submission** | Journal formatting, the submission package, the AI-use declaration. | A folder you can upload, and a checklist of what the journal still wants. |

> [!TIP]
> **The order is a default, not a contract.** Idea first and folder later, folder first and idea later, or data first and everything else later — all three work, and the third is the common one for somebody arriving with a finished dataset and a deadline.

### How the machinery works

Four mechanics explain most of what you will see. None of them is something you have to operate.

**Nothing runs until you say so.** A skill states the plan — which modules, in what order, what it will read, how many agent calls it costs — and waits. It never fires itself off a passing mention, and it will not expand a round you scoped narrowly.

**An agent call is a fresh reader with a built context.** Heavy modules run as their own agent with a deliberately assembled set of files rather than the whole folder. That is why the plan quotes a number: agent calls are the expensive part, and a lighter preset buys some back. The countable checks — length, citations, cross-references, number density — cost nothing and run either way, in about two seconds.

**A round is one pass at the paper.** Round 1 is a first draft; round 5 might be after two coauthors and a reviewer. Each round's reports, its built manuscript and the edits received for it retire together into `obsolete/drafts/rN/` when the next round opens, with that round's prose in a `source_text_rN/` folder of its own beside them — so the journal folder always answers "what is being sent right now" with one file. Nothing opens the next round for you.

**Changing journals leaves one live folder.** When you `init` a second journal, the engine offers `retire-journal`, which moves the old journal's whole folder into `obsolete/drafts/<JOURNAL>/`. It is an offer, never automatic, it deletes nothing, and it refuses rather than bury anything that is still standing — an open run, a coauthor's edit you have not applied, a round log it could not merge. Run it with `--dry-run` first to see exactly what would move.

**A flag is a question the pipeline could not answer.** When a value is not recorded anywhere, the drafter writes `**[FLAG: ...]**` in the prose instead of a guess. Flags are raised freely, a self-resolution pass answers every one that does not need a human, and what survives is listed in the built `.docx` and blocks the submission package. Answering them is a conversation of its own — `flag-resolver`, in [section 7](#7-stage-c-the-writing-engine).

---

## 4. Just say what you want

This section is why the rest of the page is optional reading. Say something like the left column and the right column is what will happen. The agent **offers** — it names the module and asks. It never fires itself off a passing mention.

| You say something like | What it reaches for |
|---|---|
| what should I work on · is there a gap · what has not been done · what would make a paper | [`idea-generation`](../skills/idea-generation/SKILL.md) |
| set up a project · start a paper · make me a folder for this | [`setup-project-directory`](../skills/setup-project-directory/SKILL.md) |
| my folder is a mess · is anything missing · did I lose a file | `scaffold.py check` |
| I already have a folder full of work · reorganise it | `setup-project-directory` |
| what is this extra folder · can the paper use it | `scaffold.py survey` |
| I need fake / dummy / placeholder data to test a figure | `idea.py mock` |
| show me what a figure would look like before I have data | `scaffold.py mock-floats` |
| my evidence is images, not rows · lay out my figures | `scaffold.py image-floats` |
| a scheme · a diagram · an apparatus · a mechanism · a protein figure · "draw" | [`create-graphic-figure`](../skills/create-graphic-figure/SKILL.md) |
| this figure is ugly · wrong colours · wrong panel order · rebuild it | [`refine-figure`](../skills/refine-figure/SKILL.md) |
| what tests should I run · set up my statistics · it says analysis.md is missing | [`analysis`](../skills/analysis/SKILL.md) |
| find the paper that says X · is this citation real · check my references | `scholar.py` — never from memory |
| what does this compound weigh · what is its SMILES | `scholar.py compound` |
| is there a structure for this protein · what does AlphaFold have | `structure.py` |
| what else looks like this sequence · what is on this operon · fold this | `sequence.py` — offered in idea generation, never run unasked |
| draft the introduction · write the methods · rewrite the discussion | [`writing-engine`](../skills/writing-engine/SKILL.md), scoped to those sections |
| format for *journal* · does this fit their limits · what do they require | `writing-engine` + `format-check` |
| my coauthor sent edits · here is a tracked-changes file | `writing-engine` ingest + `docx_edits.py` |
| the reviews came back | `writing-engine`, `revision` preset |
| build the .docx · assemble it · give me something to send | `manuscript.py assemble` |
| is this ready to submit · what does the journal still need | `writing-engine` submission package |
| what is still blocking this · answer the questions it raised · what are all these flags | [`flag-resolver`](../skills/flag-resolver/SKILL.md) |
| my folder is a mess · files everywhere · I don't know where anything is · sort out / clean up / tidy this folder · sort my figures out | [`reorganize-directory`](../skills/reorganize-directory/SKILL.md) |
| the engine did something wrong · it keeps doing X | `manuscript.py log-issue` |
| put this on GitHub · sync this between my computers · stop syncing · put this file on GitHub | [github-ai-project-manager](https://github.com/mercer-science/github-ai-project-manager), a separate tool. The engine only offers it and hands over; if it is not installed, you are offered the install |

> [!NOTE]
> **"Figure" means four different things**, and guessing wrong costs a week. A plotted float (`figure.R`), drawn art (`create-graphic-figure`), an existing float that needs changing (`refine-figure`), or the graphical abstract (`toc-graphic`). If it is not obvious you will be asked which, in those words.

**Being more specific always wins.** Naming the journal, the sections, or the round — *"draft the introduction and methods for Langmuir"* — scopes the work exactly, and a scope you give outranks anything the folder suggests. What is left out is reported as *out of this round's scope*, never as a gap.

---

## 5. Stage A: idea and setup

### `idea-generation` — what the paper is going to argue

**Use it when** you have an intent rather than a project: a direction, a technique you want to apply, a dataset you suspect has a paper in it, or nothing but a field. **It ends with** a settled question, the papers that establish the gap, the claim the paper makes, the float set that carries it, and a statement of what data would have to exist.

Six stages, and the order is the point: the questions come *after* the literature, because an uninformed question wastes the answer.

1. **Your intent, in your words.** Nothing is refined yet.
2. **Get current.** Your lab's own `Resources/` folder first — the SOPs name your instruments, and those names are the search vocabulary — then a sweep across six literature indexes. Abstracts only at this stage.
3. **Informed questions.** The ones whose answers change which gaps are reachable. *"idk" is a first-class answer* and does not stall anything: unknown is not the same as infeasible.
4. **Gap synthesis.** Three to five candidates, each with what establishes it. A gap needs a paper stating the limitation, or two papers that conflict — "nothing came back from the search" is not a gap.
5. **Feasibility.** Against what your lab can actually do.
6. **Narrative and the float set.** The figures the paper needs, each with the one claim it carries.

At the end you choose where it goes:

| Destination | What you get |
|---|---|
| `Ideas/<name>/` | Four files, for thinking that has not earned a project yet: `summary.md` (the fifteen-second version), `relevant_literature_summary.md`, `refs.bib`, and `ideas.md` — the candidates you dropped and why, which is the part that stops the same ground being re-covered in three months. |
| a new project folder | The full scaffold, with the literature *moved* in rather than downloaded again. |
| a project that already exists | An **additive merge**: guiding papers, a dated takeaway block, proposed methods, and an outline *proposal*. Nothing is overwritten — every row of the preview is ADD, APPEND, NEW or PROPOSE, and the writer refuses to commit anything else. |

**What it will not do:** invent a gap out of a search that returned nothing, cite a paper it has not verified, or write a proposed method into `data/methods_facts.yml` — proposed methods and recorded facts are kept apart on purpose, because the drafter treats that file as measured truth.

#### `plan/lab_pack_brief.md` — the lab's own steps, in your project

**If your lab has a resource pack installed**, idea generation leaves one more file behind: every step of your lab's route — growth, grid prep, freezing, collection, processing — **quoted from the pack word for word and dated**, with whatever you decided about that step and whatever you left open written against it.

It is there so the question you hit at the bench three weeks later has somewhere to be answered from. Ask about any of it in a session opened in the project folder.

**Three skills write it**, so you get it whichever way you started: `idea-generation` at the end, `setup-project-directory` when it scaffolds a new folder, and `reorganize-directory` when it sorts out a folder that already had months of work in it. The last two have no decisions to record yet, so every step reads *"nothing project-specific is recorded for this step yet"* — that is the invitation, and idea generation fills those lines in later without touching your notes.

Four things about it:

- **Every number in it was true on the pack's curation date, for the standard case.** If it contradicts what you know, you are right and the pack is stale — say so, and fix it at the source. **Nothing in it is a methods fact**; what you actually did goes in `data/methods_facts.yml`, written by you.
- **`## Your Notes` at the end is yours.** Regenerating the file leaves that section exactly as you wrote it.
- **It can be rebuilt any time**, on any project: `python tools/labpack.py brief --project "<the project>"`. And `--check` says in one line whether the pack has moved since the file was written.
- **No pack, no file**, and nothing goes wrong — every question a pack would have made specific is asked generically instead, which is how the skill has always worked.

### `setup-project-directory` — the folder everything else assumes

**Use it when** you want a project to exist, or when an existing folder is missing pieces. It asks six questions, all with working defaults, and every answer is recorded in `project.yml` where you can edit it afterwards.

| Asked | Default | Why it matters |
|---|---|---|
| Project name | — | becomes the folder name. `snake_case`, no spaces: unquoted paths with spaces break `Rscript` and `pandoc` silently |
| Parent directory | the nearest `Projects/` above where you are | where it lands |
| Field: chemistry / biochemistry / other | `chemistry` | selects the `data/methods_facts.yml` template |
| Target journal | blank → `generic` | selects figure column widths, dpi and base font in `theme_journal.R` |
| Mock data? | no | synthetic rows shaped like the hypothesis |
| Mock figures and tables? | no | fills the pre-made float slots, so a render produces a real preview on day one |

What you get, and what each part is for:

| | |
|---|---|
| `plan/` | what the paper argues: `README.md`, `outline.md`, `captions.md`, and one folder per float |
| `data/` | what was measured: `raw/`, `raw_images/`, `analysis/`, `methods_facts.yml`, `data_contract.md` |
| `drafts/` | the writing, one folder per journal, plus `log.md` |
| `obsolete/` | what previous rounds replaced, and journals you are no longer sending to. Nothing is deleted, and nothing is drafted from here |
| `project.yml` | the answers above, plus the title and PI |

**At the end it offers GitHub, and does not do it.** *"Would you like this project on GitHub?"* — a backup, and the same project on the lab computer and your laptop. Yes hands over to [github-ai-project-manager](https://github.com/mercer-science/github-ai-project-manager), which asks you what to keep off GitHub (all data is your decision) and keeps the project in step from then on. No is recorded in `project.yml` as `github: declined` and you are not asked again; you can still ask for it any time. `reorganize-directory` ends with the same offer. A project set up before 2026-09-30 keeps the engine's old sync, and it keeps working; the manager can take it over.

It **never overwrites**, which is what makes it safe to run against a folder you are not sure about — and what makes `scaffold.py check` usable as a repair tool. Point it at a folder that is missing half the tree and it reports what is absent, which floats have no script, and which scripts are sitting somewhere nothing will render them.

**Not every project needs every folder.** If yours has no use for `data/raw/`, `data/mock_data/` or another empty scaffold folder, say so and the folder is recorded as dropped (`scaffold.py drop`, written to `project.yml` as `dropped_dirs:`). After that it is not re-created, not reported missing, and nothing is filed into it. Deleting it by hand is not enough: a folder that is simply gone looks exactly like one a sync lost, and `check` reports it missing. `drop` removes only an empty folder and never deletes a file. `--restore` brings a folder back.

### Mock data, and mock floats

Offered at setup: rows shaped like your hypothesis, and example figures built from them. A figure built on hypothesis-shaped mock data is a test of the figure *and of the claim*, before any sample is prepped — and sometimes the answer is that the claim is weaker than it sounded, which is much cheaper to learn now.

> [!CAUTION]
> **Mock data cannot reach a coauthor.** Three safeguards, and none of them is a convention you have to remember: every generated file carries a `_mock` suffix, every float built from one is watermarked, and the coauthor `.docx` build *refuses* while any float came from mock rows. Do not remove a watermark to get a clean preview — that watermark is the thing standing between mock data and somebody else's inbox.

### If it is a review paper

Say so and you get the same folder with one part swapped:

```
python tools/scaffold.py scaffold "<path>" --paper-kind review
```

`data/` becomes `data/corpus/` — **the corpus is a review's data**. Everything downstream is unchanged: the same outline, the same drafting, the same checks, the same `.docx`. You can start from as little as a topic; you can also hand it an outline, a `.bib`, a folder of PDFs or a rough draft, and it starts from there instead.

Three differences worth knowing before you start one:

| | |
|---|---|
| **The scope contract comes first** | `review.py protocol --init` writes the questions — what is in, what is out, the date window, the venues, how many papers is enough. The corpus build is gated on it, and the floor is *your* number, not the engine's. |
| **Every record carries how much you read** | Abstract, sections, or full text. Nothing in the prose may out-run that, and `review.py tier` is what enforces it. This is the review equivalent of not inventing a methods number. |
| **The number limits come off, provenance tightens** | A review is argument, not measurement, so the density budget is released. In its place, every characterization of a source has to point at a verified record. |

`literature-landscape` does **not** run on a review — the corpus *is* the landscape.

---

## 6. Stage B: your own work

This stage is yours. The toolkit's job here is to be somewhere obvious to put things, and two skills for when a figure is not right yet.

### Where things go

| Put it here | What it is |
|---|---|
| `data/raw/` | Tables, as `.csv`. This is what the analysis reads. |
| `data/raw_images/` | Micrographs, gels, spectra — anything that is an image rather than a table. **If you have been calling this `source_images/`, this is it.** |
| `data/analysis/analysis.R` | **The project's one analysis script.** You fill in two sections — which columns are outcomes, which columns split them — and the battery in the other two is provided. The [`analysis`](../skills/analysis/SKILL.md) skill fills those two in with you. |
| `data/analysis/analysis.md` | **Generated**, by `Rscript data/analysis/analysis.R`. Numbers, and no interpretation. It is the only place the drafter may take a number from. What the numbers *mean* goes in `plan/README.md`. |
| `plan/author_information/authors.md`, `plan/author_information/affiliations.md` | Author order, addresses, grants, and the contribution matrix. **They ship blank — fill them in before you run the writing engine.** Two minutes on day one, when you already know the answers. The engine asks if you did not, but that is a backstop. |
| `plan/author_information/conflict_statements/` | The **signed competing-interest forms**, one file per author, named with that author's initials — `JV.pdf`, `RWM_icmje.pdf`. Most journals will not send a paper out for review until every one is in. `submission-package` reads the file names, tells you who has not returned one, and never opens a form. The Competing Interests *sentence* printed in the paper stays in `affiliations.md`; these are the paperwork behind it. |
| `plan/author_information/conflict_statements/blank_form/` | The journal's own **unsigned** form — the one everybody signs. The writing engine goes and finds it for you, on the round the Competing Interests statement first gets flagged, so you do not have to hunt through a publisher's site. It is a separate folder because a blank sitting among the signed ones would be counted as somebody's signature. |
| `data/methods_facts.yml` | Instrument settings and reagent details, filled in at the bench. Thirty seconds now; a day to reconstruct in eight months. Anything left blank becomes a visible flag in the draft rather than a guess. |
| `data/data_contract.md` | Each column's kind, units, expected range and permitted values. The kind decides the geom and the statistical test, so a figure argued from the contract is most of the way to the right form. |
| `plan/figures/Fig01/` | One folder per float, holding its script and its output. The folder name *is* the number, so renumbering is renaming a folder. |

```bash
Rscript plan/render_all.R
```

renders every float and writes `plan/preview.html`. Open the preview and **look at the images** — every way a float script can be wrong still renders successfully.

> [!NOTE]
> **Every number in the paper is in `analysis.md`. Not every number in `analysis.md` is in the paper.** A value you cut from the text stays in the file — nothing ever deletes one — so you can drop a number from the paper without losing it, and the drafter will not put it back. Mark a line `[must appear]` to be told when the draft has stopped carrying it.
>
> `plan/README.md` is the file **denied to the statistics checker**, because it is where "the thermophile looks tighter, as expected" gets written down. See [section 7](#7-stage-c-the-writing-engine).

### `refine-figure` — the float is not right yet

**Use it when** a figure or table needs to look different, when a rendered float is unreadable or ugly, when the panels or axes or colours are wrong, when a table needs different columns, or when the plot does not show the claim its caption asserts. It edits the R scripts you own — `figure.R`, `panels.R`, `table.R`, and the mock-data generator beside them.

The scaffold already wrote a runnable script for every float, so the common operation is not *write a float script from nothing* — that happens once. It is **edit a script that runs, look at what came out, edit it again**, ten or twenty times per float. The loop, and the order matters:

1. **Read the claim.** `plan/captions.md` carries a bold claim sentence per float, written at idea time. **That sentence is the specification** — a session that has not read it is decorating.
2. **Read the contract.** `data/data_contract.md` decides the geom and the test.
3. **Read what the script does now**, and which data it reads — real, mock, or both. `plan/floats/float_provenance.json` records that per float.
4. **Edit one thing at a time**, re-rendering between changes. A script that breaks after six edits costs more to diagnose than six renders cost to run.
5. **Render, and look at the image** — not at the exit code.
6. **Hold the image against the claim.** Would a reader see the claim without being told? If not, say which of the two is wrong.

**One claim per figure** is the standing constraint, and the most common finding in this loop is a panel carrying a second claim that wants its own float. `scaffold.py float lint` reports the traps that render successfully and produce the wrong figure; it never edits anything and **it is never a gate** — a lint that blocks a render teaches people to skip rendering, which is the one thing this loop cannot survive.

> [!TIP]
> **The honest outcome is sometimes "the claim is weaker than it sounded."** Learning that in an afternoon on mock data, before the samples are prepped, is the entire reason mock data exists.

### `create-graphic-figure` — the panel nobody draws in code

**Use it when** a panel needs drawn art rather than a chart: a reaction scheme, an apparatus diagram, a mechanism, a sample-prep flow. Nobody draws those in ggplot, and the honest answer is not a drawing API but the editing surface you already have.

**A graphic panel is one PowerPoint slide.** The engine writes the `.pptx` at the journal's geometry, you draw in it, it renders to `.png` at the right pixel size, and the same script that draws the plotted panels composes it beside them. It asks two questions, both with defaults — is this the whole figure or one panel of several, and how wide does it sit — and deliberately does not ask about dpi or pixels, which are resolved at render time from the journal's own geometry.

What it will tell you, because each is a real trap:

- **The `.pptx` is yours and is never overwritten.** Same rule as your outline. The engine refuses rather than replacing it; if you want to start a panel over, deleting it is your call.
- **What it writes is a layout skeleton, not a drawing.** It opens at the right size with the right font, carrying `[TK: ...]` labels for every string a human has to choose. It will not invent labels into your slide, and it will not describe art it has not seen.
- **Do not resize the slide.** The aspect ratio is what the composed figure preserves.
- **Art that does not exist yet is fine.** A missing panel renders as a dashed grey placeholder, so the whole figure set can be built and criticised while the art is still being drawn.
- **Staleness is the file's SHA-256, never its modification time** — OneDrive rewrites mtimes on sync, which makes an mtime check both re-render files nobody touched and call a file current after a sync replaced it.

`Rscript plan/render_all.R` stays the only command you run; unchanged art is skipped. After the art renders the figure still needs its caption block and a callout in the text, and both are `writing-engine`'s job — you will be told rather than having a caption written silently.

---

## 7. Stage C: the writing engine

One skill, [`writing-engine`](../skills/writing-engine/SKILL.md), and it is modular: almost never does the whole pipeline need to run.

### How a round actually goes

1. **You say what you want.** *"Draft this for Langmuir"*, or *"just redo the discussion"*, or *"the reviews came back"*.
2. **It reads the folder first.** What the project holds, which sections are still stubs, whether there is an outline, whether an earlier run was interrupted, how many journal requirements are still `unknown`.
3. **It states the plan and waits.** The module list in order, what it will draft *from* — one line per source, with `EMPTY` for a file that exists and says nothing and `MISSING` for one that was never created, because those are different problems with different fixes — and the estimated agent calls.
4. **You say go**, or narrow it. A scope you give is honoured exactly.
5. **It runs, recording each step as it finishes** — so a run that dies mid-way is resumable rather than restartable.
6. **You get a `.docx`, a set of reports, and a flag count.** If flags remain, it says so and offers `flag-resolver` rather than starting a twenty-question conversation you did not ask for.

**It clears its own context part-way through, and tells you when it does.** A round has two halves — writing the prose, and building the document the journal gets — and the second half needs the source text and the journal's rules and nothing that was said in the first. So at that seam it writes down anything you said that is not already in a file, records where the run got to, and starts fresh. You get one sentence saying so; it is not a question, because a question at every seam is how a good idea gets declined into uselessness. The plan you approve at step 3 already shows you where those points are.

Two things are worth knowing about it. **Nothing is lost that was written down**, and everything the second half needs is written down — that is why the seam is where it is. **What is at risk is anything you said that nobody recorded**, which is why the engine writes it down first and why, if you want a preference to survive, saying it once to the engine (`config --instruct`) beats saying it once in conversation. If you would rather keep talking about the draft across the seam, say so, or set `compaction: ask` in `writing_config.yml` — or `off` to turn it off entirely.

### The modules

| Module | What it does |
|---|---|
| `outline` 🔹 | Proposes or completes the paragraph plan — one line per paragraph, the claim then its evidence. |
| `literature-landscape` | What the field says, from six indexes, one verified reference per line. Runs before drafting in every preset that drafts. |
| `learn-from-edits` | Reads your own edits and turns them into drafting rules for *this* round. |
| `draft-sections` 🔹 | Writes the body sections from the outline and the recorded sources. The abstract is drafted last, from the finished sections, by its own blind agent. |
| `revise-prose` 🔸 | **Fixes what the readability checks measured, before any checker reads the paper** — it is handed every finding that names a sentence and works those first. It cannot change a single claim, number or citekey; all three are checked unchanged afterwards, and a section that fails is restored. It is deliberately **not** shown the densities (passive share, sentence length), because a number that names no sentence can only be chased. |
| `comprehension-check` 🔸 | **Reads the paper and reports what it understood** — the only module that reads rather than conforms. It is given the `audience` you declared and nothing about what the paper was trying to say, so where it loses the thread is where a reader will. Every other check can be passed by prose nobody can follow; this one cannot. |
| `revise-prose --from-comprehension` 🔸 | One bounded rewrite pass whose brief is the comprehension report, over the sections that report named and no others. **It runs, every round** — you are told what it changed rather than asked first, and it publishes a `Left alone` list for every finding it declined. Set `comprehension_fix: ask` if you would rather approve each one. |
| `evidence-check` | Every number in the prose has to trace back to something recorded. |
| `stats-check` 🔸 | Reads the statistics against the data, *without being told what they were supposed to show*. Research papers only. |
| `attribution-check` 🔸 | **Review papers only.** Reads each paragraph against the retrieved text of every source it cites, without knowing what the review argues — so it cannot talk itself into a source saying what the thesis needs. |
| `assemble` | Builds the `.docx` behind six gates. |
| `citation-check` | Every in-text key resolves, every entry is cited, nothing duplicated. In every preset that writes prose, and never offered as a choice — it has one right answer. |
| `reviewer-check` 🔸 | Reads the built paper the way a reviewer would. Light on a coauthor round, heavy before submission. |
| `final-check` | Reads back what the other checks wrote. The one module that is *supposed* to see everything. |
| `submission-package` | The five (or six) files a journal wants alongside the manuscript. |
| `respond-to-reviewers` | The response letter, written from the edit ledger. |
| `toc-graphic` | The graphical abstract, as a PowerPoint slide you draw. |

🔹 runs as its own agent  🔸 runs **blind**

### What "blind" means, and why it fails quietly

A blind module runs with a context built from an explicit allow-list, and the things it is denied are denied on purpose. `stats-check` is the strict case: **an agent that knows what you hoped to find reads an ambiguous result as favourable** — and nothing visibly fails when that isolation breaks. The report still looks fine. It is simply worthless, and it is trusted *because a report exists*.

On a **review** the strict case is `attribution-check` instead, for exactly the same reason pointed at somebody else's data: an agent that knows the thesis will read a hedged sentence in a cited paper as supporting it.

> [!IMPORTANT]
> So if a blind module cannot be given its own context, the rule is **skip it and say so**. No report is better than a contaminated one.

### The presets, and what each costs

| Preset | Agent calls | For |
|---|---|---|
| `draft` | 7 | Getting words down. No blind checks. |
| `coauthor` | 10 | Something to send round. Adds the evidence and statistics checks. |
| `submission` | 13 | Everything, including the graphical abstract and the package. |
| `revision` | 8 | After reviews or coauthor edits come back. Starts by ingesting them. |
| `sections` | 7 | Prose only — *"just the introduction and methods"*. Nothing is built, checked against a journal or shipped. |

Every count is one lower when the abstract came from your own `drafts/rough_draft.md`, because the blind abstract agent does not run; the plan says so when that happens.

**Skipping a module does not skip the checks that need no agent.** The whole countable audit runs in about two seconds either way, so even a `draft` run tells your coauthor about an uncited reference inside the `.docx`. What a lighter preset buys back is agent calls, not arithmetic.

<details>
<summary><b>Scoping a round, and giving standing instructions</b></summary>

<br>

```bash
python tools/manuscript.py plan "<project>" --journal Langmuir --preset coauthor
python tools/manuscript.py plan "<project>" --journal Langmuir --sections introduction,methods
python tools/manuscript.py config "<project>" --journal Langmuir --instruct "do not use the word novel"
python tools/manuscript.py config "<project>" --journal Langmuir --weight captions=primary
```

Three scopes, and the difference matters because anything you only *say* is gone by the next session:

| Scope | How |
|---|---|
| this run only | say it in the prompt; `--add` / `--drop` for one module |
| every run | `config --instruct "..."`, `--skip`, `--set`, `--weight` — recorded in the project |
| undo a standing one | `config --forget "..."` |

Standing instructions live in the project's `writing_config.yml`, are hand-editable, and are read back to you by `plan` before any run starts. An instruction persists into every later round until you `--forget` it. `--weight captions=primary` says which source decides when two of them disagree.

</details>

<details>
<summary><b>When a coauthor or a reviewer sends something back</b></summary>

<br>

Put the file in `drafts/edits/` — the project's one inbox, beside the source text — and say so. `docx_edits.py` reads the tracked changes and comments out of it — who changed what, and what each comment asked — and `ingest` turns that into rows in `edits/edits_status.md`: the ledger of every incoming request and what happened to it.

```bash
python tools/docx_edits.py extract "edits/manuscript_r5_JV.docx"
python tools/docx_edits.py plain    "edits/manuscript_r5_JV.docx" --reject
python tools/manuscript.py ingest   "<project>" --journal Langmuir
```

The ledger is what the response letter is written from, and it is also what `learn-from-edits` reads to work out how you actually write ([section 9](#9-what-it-learns-from-you)). Word files are inputs here and outputs everywhere else: no module edits a `.docx`.

**Your own markup and a coauthor's return are not the same kind of thing, and the ledger now says which is which.** Mark up the `.docx` the last round built and `ingest` reads those too, as `own`: they are applied on the next round and then **spent**, because by r5 nobody is arguing about r2's wording. A file in `edits/` is `coauthor` — it came from somebody who is not watching this and will not see the paper again for weeks — so it **stands** until the paper goes out.

> **Nothing overrides a coauthor's sentence silently.** If a later pass wants to rewrite text one of them changed, it stops and writes a flag naming them: *"JD put this edit: x, but we are now thinking y will read better, because…"* Their words stay until you answer it. You can override it — that is the point of asking — and if the same rule keeps proposing to undo your colleagues' work, three refusals retire the rule.

A tracked change is the words they want, kept as typed. A **comment** is an instruction, and it licenses a rewrite of the paragraph it is attached to and nothing else — *"make this flow better"* is a real request the engine can now carry out without treating it as permission to restyle the section. Where a coauthor types an instruction into the body as if it were prose, you get asked which they meant rather than one of the two guesses.

</details>

<details>
<summary><b>When you edit a section yourself</b></summary>

<br>

Open `drafts/source_text_rN/<section>.md`, change a word, and that section is **yours**. The engine recorded a hash of what it wrote, so the next round can tell your edit from its own writing, and nothing rewrites a section you have taken over — the checks still run over it and report, but the passes that rewrite prose skip it and say they did. `--redraft <section>` hands one back when you want it recomposed, and it names what it is about to discard rather than counting it.

Whitespace is not an edit: an editor that trims trailing spaces on save does not freeze your paper. A changed word is.

The plan block tells you where you stand before anything runs — how many sections are yours, and how many carry prose the engine has no record of writing, which is the state a project imported from elsewhere starts in.

</details>

<details>
<summary><b>If a run stops part-way</b></summary>

<br>

A full run is several agent calls over a lot of text, and it stops for reasons that have nothing to do with the paper — usage runs out, a session closes, a machine reboots. Every module records where it got to *as it finishes*, with the SHA-256 of each file it wrote, so the next invocation continues instead of starting again. Just say *"pick up where the last run stopped"*; it will tell you which step it resumes from, and it re-checks recorded outputs against the folder rather than believing its own ledger.

</details>

> [!NOTE]
> **An unfinished paper still builds, and says so.** A half-written outline and an undrafted discussion are the normal state for most of a project's life. Nothing refuses to run because the paper is not finished — instead, when a section is not written yet (or a figure uses mock data), the built `.docx` carries a bold `[FLAG: incomplete — PAPER NOT COMPLETE]` block naming it, so the coauthor who never saw your terminal knows too. Everything else still to do — author details, journal fields, the graphical abstract — is listed in `reports/rN/outstanding.md` and in the terminal, not in the paper, so a draft can go to coauthors as it is. From round 2 on, where the paper differs from the outline, captions or analysis, you are asked which differences to act on; nothing is changed to match the plan unless you pick it.

> [!NOTE]
> **Every build also tells you how the paper READS.** Under that verdict comes a second block, and it answers a different question: not whether the paper is finished but whether it is readable. Paragraphs that dead-end instead of handing off, paragraphs with no bridge into them from the one before, sentences that hold their subject and verb far apart, pipeline text that got left in the manuscript — then a row per section giving its word and sentence counts, sentence length (mean **and spread**), how long each paragraph's opening sentence runs, and the share of sentences in the passive.
>
> **None of it blocks anything, by design.** It never appears in the outstanding list and never reaches the coauthor's copy. A style rule that can fail a build teaches the writer — human or otherwise — to write for the metric, and the spread is the example worth keeping in mind: prose whose sentences are all the same length reads worse than prose whose average is longer. These are numbers to read and argue with, not a grade.

### `flag-resolver` — answering the questions the draft raised

**Use it when** a build reports flags remaining, when the submission package says NOT SENDABLE, or when you want to know what is actually blocking the paper. It walks the outstanding flags one at a time, records what you decide, and stops. **It writes no prose.**

It runs standalone on any project at any time — `flag-resolver`, or `--list` to print them and stop, or `--type author` for one kind. It needs a journal folder to *record* answers, and a project without one gets the whole walk-through plus a clear statement that there is nowhere to record them yet, never an invented folder.

Flags come in the order that respects your attention — `author` first, then `data`, `decision`, `conflict`, `stats`, `citation`, `journal` — cheapest to answer first. Each one arrives with five things:

1. **The flag verbatim**, with the sentence and the paragraph around it. A flag read without its paragraph gets answered wrong.
2. **Which module raised it, and in which round.** A `stats` flag from r2 still standing in r5 is a different conversation from one raised yesterday.
3. **What it blocks.** Every flag blocks the submission package; some also block a specific check.
4. **What the self-resolution pass already tried** — *"I searched Crossref and PubMed for this claim and found nothing"* changes the question from *find a citation* to *is this yours, or should it come out*. You should never be asked to redo work the pipeline already did.
5. **Options** — never more than four, drawn from the flag's type, and free text is always accepted and recorded verbatim.

Three answers exist for every flag whatever its type: **answer it**, **it is a limitation**, or **defer it with a reason**. Deferring is a recorded decision, not a shrug — *"I'll add it later"* is a defer and gets written down as one. A DOI you supply goes through `scholar.py verify` before it is accepted, because a mistyped DOI in a reference list is a retraction-grade error and it arrives most often by hand.

Answers land in the edit ledger as pending rows and are applied by the next writing round. Your answers survive rewording and renumbering: a flag's id is hashed from the section, the type and the text of the question, never from where it sat on the page.

---

## 8. Stage D: submission

`submission-package` writes, every run, whatever the journal asks:

- **Cover letter** — addressed, with the significance framing taken from the abstract rather than invented.
- **Title page** — byline, affiliations, counts, keywords.
- **Statements** — contributions in CRediT's own wording, from the matrix you ticked; funding from the grant table.
- **Suggested reviewers**, with conflicts noted — unless the journal wants them in the cover letter, in which case they go in the letter and there is no second file.
- **Checklist** — every field of the journal's requirements, ticked or flagged.
- **AI-use declaration** — a sixth file, when the journal wants one of its own.

Each file is headed with the requirement it was written against — "required" or "not requested (nothing sourced)" — so the shape of it is visible either way.

### Suggested reviewers live in one place

**You keep the list in `drafts/source_text_rN/suggested_reviewers.md`**, beside `ai_disclosure.md`. That file is created for you the first time the package is built and is never overwritten, because everything *in* the package is rewritten on every run — a list kept there would be retyped every round.

Where it comes out depends on `submission.suggested_reviewers_in` in the journal's requirements: `cover_letter` puts the names in the letter and writes no separate file, `separate` writes the standalone upload, `portal` writes neither because the journal collects them in its own system. Unrecorded, you get the standalone file, which is the safe answer both ways.

### The name the files are sent under

`manuscript_r5.docx` is the right name while you are working and the wrong name in an editor's inbox, which holds forty of them. When the paper is ready to go:

```bash
python tools/manuscript.py submission-names "<project>" --journal Langmuir --name EXAMPLE_STUDY
```

That writes `EXAMPLE_STUDY_manuscript.docx`, `EXAMPLE_STUDY_title_page.docx` and the rest into `drafts/Langmuir/submission/upload/`. **It copies — the round-numbered files stay where they are**, because that is what the engine reads on the next round. Upload what is in `upload/` and nothing else; the `.md` files you edit are deliberately left out.

It refuses three things, and each one is worth reading rather than forcing past: an outstanding flag, a build made from mock data, and a `short_name` that is only your project folder's name — that last one is the name an editor sees, so it asks you to choose it rather than guessing. **Re-run it after any rebuild**; the copies do not update themselves.

### The flag gate

The package **refuses to send** while any `**[FLAG: ...]**` remains. It still writes the files and reports itself **NOT SENDABLE**, which is the same information without an empty folder. The way through it is [`flag-resolver`](../skills/flag-resolver/SKILL.md), not a hand edit.

### What the journal requires, and where those answers come from

A journal's limits and required files are read from the journal's own instructions and recorded with the URL and the date they were read. **A requirement nobody has read is `unknown`, never inferred from a similar journal** — and `unknown` fails the checklist row rather than passing quietly. `format-check` holds the built file against what was recorded: word counts by section, float counts, reference cap, figure geometry.

### The AI-use declaration

Journals want this three different ways — a statement in the paper, a declaration in the submission, or an explicit statement that neither is needed — and one journal can want more than one. Ask for it with:

```bash
python tools/manuscript.py ai-disclosure "<project>" --journal Langmuir
python tools/manuscript.py ai-disclosure "<project>" --journal Langmuir --draft
python tools/manuscript.py ai-disclosure "<project>" --journal Langmuir --from-ledger
```

**The engine drafts the declaration; you confirm it.** While the journal's requirement is still `unknown`, the command scaffolds blanks — `--draft` refuses on those, because drafting against a policy nobody has read is answering a question nobody asked. Once it is recorded (read the journal's own guidelines and note the URL), `--draft` writes this toolkit's default statement into the stub under **one** flag that means "confirm this is true, don't compose it" — and it never overwrites a stub you have already written into or confirmed. On request, `--from-ledger` *appends* what the engine *factually did* out of the run ledger, naming the round every line came from, never replacing the statement above it. It writes nothing the ledger does not contain, and on a project with no ledger it writes nothing at all.

> [!WARNING]
> **Blank is not an option on the checklist row.** `ai_disclosure.required: unknown` *fails* that row, because "nobody looked" and "not required" are the two things it exists to keep apart. A journal that genuinely requires no declaration gets a ticked row carrying the date and the URL it was read from.

### After a decision comes back

`respond-to-reviewers` writes the response letter from the edit ledger — every reviewer point, what was done about it, and where in the manuscript it changed. It is written from what the ledger records rather than from memory of the round, which is why ingesting the reviews properly matters more than it looks.

---

## 9. What it learns from you

Every edit you or a coauthor makes to a built `.docx` is read back, classified, and — once the same lesson has been seen twice — turned into a drafting rule that goes into the brief *before* the next round writes a word. One observation is never a rule: a registry that learns from a single instance fails invisibly, as slightly worse first drafts on an unrelated paper months later.

Two things it will never learn: a rule that licenses a number nobody measured, and a rule that loosens a citation requirement. Those are refused on write.

```bash
python tools/learn.py brief
python tools/learn.py stats
```

> [!IMPORTANT]
> **Your learned rules never leave your machine.** They quote your own unpublished prose, so nothing sends them anywhere — not to the toolkit's maintainer, not to your group, not with a defect report. That is enforced in code and asserted by a test, not left as an intention. It is also why `/plugin uninstall` is the one command never to run: the rules live in the plugin's data directory and there is no copy of them anywhere else.

---

## 10. Every command, on its own

No skill and no agent is required for any of these. Every one takes `--json`, on either side of the subcommand. Exit codes mean something: `0` clean, `1` findings, `2` refused or unusable input. Full documentation for every engine is [`tools/README.md`](../tools/README.md).

<details>
<summary><b>Projects and floats</b> — scaffold, check, float folders, mock data</summary>

<br>

| To do this | Run this |
|---|---|
| Scaffold a project | `python tools/scaffold.py scaffold "<path>"` |
| Find what a project is missing | `python tools/scaffold.py check "<path>"` |
| Record an empty scaffold folder the project does not need, or bring it back | `python tools/scaffold.py drop "<path>" data/raw [--restore]` |
| Adopt a folder that already has work in it | `python tools/scaffold.py adopt "<path>"` |
| See what the structure does not explain | `python tools/scaffold.py survey "<path>"` |
| See the tree | `python tools/scaffold.py tree "<path>"` |
| Should GitHub be offered, and is the manager installed | `python tools/scaffold.py github-offer "<path>"` |
| Add / renumber / migrate a float folder | `python tools/scaffold.py float new\|renumber\|migrate "<path>"` |
| Lint the float scripts | `python tools/scaffold.py float lint "<path>"` |
| Prefill instrument settings from the facility record | `python tools/scaffold.py prefill "<path>" --instrument "…"` |
| Read the acquisition settings out of the image files | `python tools/scaffold.py harvest "<path>"` |
| Mock data | `python tools/idea.py mock "<path>" --bundle idea.json` |
| Mock floats | `python tools/scaffold.py mock-floats "<path>" --bundle idea.json` |
| Image placeholder floats | `python tools/scaffold.py image-floats "<path>" --bundle panels.json` |
| Read a lab's `Resources/` folder | `python tools/idea.py resources "<lab folder>"` |
| Write (or rebuild) this project's lab pack brief | `python tools/labpack.py brief --project "<path>"` |
| Has the pack moved since the brief was written? | `python tools/labpack.py brief --project "<path>" --check` |
| Merge a settled idea into an existing project | `python tools/idea.py merge "<path>" --bundle idea.json` |
| Panel art: add, and is it stale | `python tools/graphic_figure.py add\|status "<path>"` |

</details>

<details>
<summary><b>Literature, compounds and structures</b> — never cite from memory</summary>

<br>

| To do this | Run this |
|---|---|
| Search the literature | `python tools/scholar.py search "<query>" --limit 25` |
| Verify one citation is real | `python tools/scholar.py verify --doi 10.1016/j.susc.2005.01.038` |
| Verify a whole bibliography | `python tools/scholar.py check-refs "<path to .bib>"` |
| What KIND each reference is — paper, preprint, database, thesis | `python tools/scholar.py source-types "<path to .bib>"` |
| Has a later paper disagreed with this one | `python tools/scholar.py disputed "<doi>"` |
| What cites this, and what it cites | `python tools/scholar.py related\|cited-by "<doi>"` |
| Compound data | `python tools/scholar.py compound "copper(II) acetate"` |
| Full text of an open-access paper | `python tools/pubmed.py sections <PMID>` |
| Predicted or experimental structures | `python tools/structure.py alphafold --uniprot P00918`<br>`python tools/structure.py pdb --id 1CBS` |
| What else looks like this sequence | `python tools/sequence.py blast --accession P58335 --submit` |
| What else is in this region — the operon question | `python tools/sequence.py neighbors --accession <acc> --window 10` |
| No AlphaFold model — what now | `python tools/sequence.py fold --accession P58335` |

`scholar.py` is the one to search and verify through. PubMed alone cannot see *Surface Science* or *Vacuum*, and reports a paper that exists as `not_found`.

**A BLAST miss is not evidence of novelty.** Sequence and structure data are evidence for **feasibility** and for **methods** — never for a gap. A gap still needs a paper stating the limitation, or two papers that conflict.

</details>

<details>
<summary><b>The manuscript</b> — plan, build, check, measure</summary>

<br>

| To do this | Run this |
|---|---|
| What the writing engine would run | `python tools/manuscript.py plan "<path>" --journal X` |
| Build the manuscript | `python tools/manuscript.py assemble "<path>" --journal X` |
| What is still outstanding | `python tools/manuscript.py completeness "<path>"` |
| Which document in this folder is the paper | `python tools/manuscript.py strays "<path>"` |
| Check the built file against the journal's format | `python tools/manuscript.py format-check "<path>" --journal X` |
| Open the next round | `python tools/manuscript.py round "<path>" --journal X` |
| Retire a journal you are no longer sending to | `python tools/manuscript.py retire-journal "<path>" --journal X --to Y --dry-run` |
| What a fresh session needs to pick this round up | `python tools/manuscript.py handoff "<path>" --journal X` |
| The AI-use declaration | `python tools/manuscript.py ai-disclosure "<path>" --journal X` |
| Drop uncited references | `python tools/manuscript.py bib "<path>" --prune --dry-run` |
| Who changed what in a `.docx` | `python tools/docx_edits.py extract "<file.docx>"` |
| Number density, callouts, length | `python tools/prose.py density\|crossrefs\|length "<path>"` |
| What it has learned from you | `python tools/learn.py brief` |

</details>

<details>
<summary><b>The toolkit itself</b> — the skills and the help deck</summary>

<br>

| To do this | Run this |
|---|---|
| Are the skills findable from any folder | `python tools/install_skills.py status` |
| Make them findable (clone route) | `python tools/install_skills.py install` |
| What the engine files against itself | `python tools/manuscript.py log-issue --list-codes` |
| Measure the help deck | `python tools/help_deck.py check` |
| Rebuild the help deck — **discards hand edits** | `python tools/help_deck.py build` |

> [!WARNING]
> The deck in this folder is **yours to edit** in PowerPoint. `build` overwrites it in place with no undo, so nothing runs it automatically — and if you want a fresh one from the module list, build it somewhere else first: `build --out "%TEMP%\new_deck.pptx"`.

</details>

<details>
<summary><b>Review papers</b> — the corpus, the screening log, the tier rule</summary>

<br>

For a research paper `data/` holds what was measured. For a review, `data/corpus/` holds **what was read** — and the field the whole thing turns on is *how much* of each paper was read.

| To do this | Run this |
|---|---|
| Scaffold a review project | `python tools/scaffold.py scaffold "<path>" --paper-kind review` |
| Where the corpus stands | `python tools/review.py status "<path>"` |
| Write the scope contract | `python tools/review.py protocol "<path>" --init` |
| Search, logging every query | `python tools/review.py search "<path>" --query "..."` |
| Decide a candidate in or out | `python tools/review.py screen "<path>" --key K --decide include` |
| Bring in a `.bib`, a folder of PDFs, or an idea run | `python tools/review.py adopt "<path>" --bib refs.bib` |
| Record what one paper says, and at which tier | `python tools/review.py record "<path>" --key K --tier abstract` |
| Check the records against the live indexes | `python tools/review.py verify "<path>" --all` |
| Build the evidence table from the records | `python tools/review.py synthesis "<path>"` |
| **Hold the prose to the tier it was read at** | `python tools/review.py tier "<path>"` |
| What the corpus does and does not cover | `python tools/review.py coverage "<path>"` |
| The PRISMA-style counts | `python tools/review.py prisma "<path>"` |

> [!IMPORTANT]
> `tier` is the one to know. **No sentence may characterize a source beyond the tier at which that source was read**, and no source may be characterized at all unless its record exists and carries a `verified:` line. A plausible false attribution has the property that makes it the worst thing this toolkit could emit: nothing in the folder contradicts it, and it reads like scholarship.

`review.py echo` also exists — it looks for prose that tracks a source's own wording too closely. It is **shipped uncalibrated**: it reports `advisory` and gates nothing, because no threshold has been derived against real drafted review prose yet. Treat its output as a prompt to look, not as a finding.

</details>

---

## 11. When something looks wrong

### The paper looks thin, or a skill was never offered

Check the install before anything else. **A harness that cannot resolve a skill does not report a missing skill — it writes the paper without one, and nothing looks wrong.** [Section 2](#2-install-it-once) has the check and the fix.

### The engine did something wrong

```bash
python tools/manuscript.py log-issue "<project>" --stage <stage> --code short_slug \
  --title "one line" --where "where in the engine" \
  --detail "what happened, generically" \
  --example "what happened on this project" \
  --wanted "what should have happened" --verify "how to reproduce it"
```

You can also just say what happened and let the agent file it.

**This works at every stage, not only while writing.** `--stage` is one of `setup`, `idea`, `analysis`, `figures`, `flags`, `writing`, `review`, `engine`, so a defect you hit while scaffolding a project or refining a figure is filed the same way and is recognisable upstream as having come from there. Leave it out and it is guessed at from the journal and the round — right while writing, a guess everywhere else — and the record says it was guessed. Every skill carries this command now; before 2026-09-18 only the writing engine did, so a fault found anywhere else was simply lost when the session ended.

The project argument is positional and defaults to the current directory, so this works before a project exists.

> [!IMPORTANT]
> **The description is generic; the specifics go in `--example`.** This file stays on your computer (it is gitignored), and closed items are cleared of their detail automatically — but it is read at the start of every session, so an item says *"the drafter dropped a row from the table"*, and the sample that happened to be in that row goes in `--example`. A sample id or a composition anywhere else is refused, with the field and the token named; a measurement or a ratio is only reported, because blocking a defect from being filed over a number in its explanation would be worse than the leak. `--example` is optional and most items do not need one.

> [!CAUTION]
> **Never edit `system-changes.md` by hand.** It is the engine's own defect list and the engine maintains it: the item numbering, the status convention and the "seen again" counts are all computed. One extra blank line in it once stopped every write for days, and the refusal was reported in a field nobody was reading.

**Fixed it yourself?** Add `--fixed` — on its own to mark an item that is already in the file, or beside `--code` to file and mark in one go. `--detail` then says *what you changed*, and it is required, because a status with no evidence under it cannot be checked by the next person. It is the only thing that writes that status, and it deliberately cannot write `VERIFIED FIXED`: an item is closed by a later run that fails to reproduce the defect, never by the person who fixed it.

### The folder looks wrong

```bash
python tools/scaffold.py check "<project>"
```

It never writes anything, so it is safe to run on a folder you are unsure about. It lists what is missing, which floats have no script, and which scripts are sitting where nothing will render them.

### Nothing you write leaves your machine

There is nothing to configure, and that is the whole of it. The engine keeps a defect list about **itself** — what it got wrong, in terms general enough to be true of any project — in `system-changes.md` inside the toolkit. Your prose, your data, your citations and your learned writing rules stay where they are.

Until 2026-09-21 there was a second half to this: a GitHub token, a consent gate, and a private repository the engine pushed redacted defect records to. It was removed. It cost every member three browser screens before they had written a word, and in the whole time it existed **it never sent anything** — 69 records sat waiting on a token nobody had minted.

### What is not built yet

Stated here because this is where somebody would go looking, and a help page that describes a module which does not exist is worse than one that says "not yet".

- **`/plugin uninstall` destroys your learned rules.** An open defect with no fix, and the reason for the warning in [section 2](#2-install-it-once). Copy the plugin's data directory out before you reinstall or move machines.
- **Nothing opens the next round automatically.** A plain `writing-engine` call can therefore edit prose inside a round that has already shipped. Known and deliberately left unfiled until it actually happens to somebody.
- **There is no offline update check.** Whether your installed plugin is behind the repository is something you find out by updating it.
  *(Built since — see [11.1](#111-am-i-behind) below. It is two commands, and the first one is the only thing in the toolkit that reaches the network.)*

### 11.1 Am I behind?

**You do not have to remember to ask.** Every skill checks at its opening step and tells you if you are behind — one line, before it starts work, and nothing at all when there is nothing to say. Nothing updates itself and nothing is ever blocked; it reports and gets out of the way.

If you want to ask by hand anyway:

```
python tools/remote.py refresh        # what the skills run. Asks only if due
python tools/remote.py check          # asks GitHub now, whatever the cache says
python tools/release.py freshness     # is this toolkit behind?
python tools/labpack.py show          # is the lab pack behind?
```

**What the automatic check costs you: almost nothing.** It asks GitHub at most once a day per repository and gives up after six seconds, so on a normal day every skill you open reads an answer already on your disk. Measured on the maintainer's machine: **0.17s** when the cached answer is good, against 2.4s for a real round trip. If you are offline it fails once, silently, records that it failed, and does not try again for an hour — so a plane is not eight timeouts.

**It runs at a skill's opening and nowhere else.** No stage, no engine and no draft makes a network call, which is the guarantee the toolkit has always made and still makes.

`remote.py check` uses **the GitHub login you already have** — the same one `/plugin marketplace add` used. There is no token to set up and nothing to paste. If your login has expired it fails quietly rather than opening a login window, and the freshness line then says `UNKNOWN` instead of pretending.

What the answers mean:

| What you see | What it means |
|---|---|
| `current as of the check today` | Genuinely current, and it says when it last had grounds to believe that |
| `the repository has moved since this copy was installed` | Run `/plugin update`. If that reports nothing to do, the change did not carry a version bump and your copy is fine |
| `UNKNOWN (...)` | It will not guess. The reason is on the line — usually a login that has expired, or a week with no successful answer |

**Why `UNKNOWN` matters more than it looks.** Until 2026-09-23 this check compared two copies on your own disk that came down in the same download, so it reported `current` whether or not it was. A check that says "I do not know" is worth more than one that says "you are fine" without grounds — silence and good news look identical, and that is exactly how eleven days of corrected instrument entries went unread on the maintainer's own machine.

---

## 12. The rules that never bend

- **Nothing is written that was not recorded** — a missing value becomes a visible flag, never a plausible guess.
- **No citation from memory** — everything resolves through `scholar.py` first, or it does not reach a file.
- **Mock data cannot reach a coauthor** — suffix, watermark, and a build that refuses.
- **An unfinished paper still builds, and says so** — inside the document, where the coauthor will read it.
- **A check that cannot be isolated is skipped, not faked** — no report beats a contaminated one.
- **A requirement that was not read is `unknown`** — never inferred from a similar journal.

---

*Written by hand and checked against nothing — if a module has changed and this page has not, this page is the one that is wrong. The picture beside it, [`paper_engine_overview.pptx`](paper_engine_overview.pptx), has a generator, but the generator runs only when somebody asks it to, so that deck is yours to edit. See [`README.md`](README.md).*

*Details live in the skills themselves — [`writing-engine`](../skills/writing-engine/SKILL.md), [`idea-generation`](../skills/idea-generation/SKILL.md), [`setup-project-directory`](../skills/setup-project-directory/SKILL.md), [`create-graphic-figure`](../skills/create-graphic-figure/SKILL.md), [`refine-figure`](../skills/refine-figure/SKILL.md), [`flag-resolver`](../skills/flag-resolver/SKILL.md), [`analysis`](../skills/analysis/SKILL.md), [`reorganize-directory`](../skills/reorganize-directory/SKILL.md) — and the reasoning lives in the maintainer's design notes, which are kept private because they quote real projects. Where this page and a skill disagree, **the skill is right**. Setting somebody else up is [`SETUP-NEW-MEMBER.md`](SETUP-NEW-MEMBER.md); changing the toolkit is [`CONTRIBUTING.md`](../CONTRIBUTING.md).*
