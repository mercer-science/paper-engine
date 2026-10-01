# Academic Research Paper Engine

Tooling for research paper work: literature search, citation integrity, idea
generation, project scaffolding, analysis, drafting and submission.

**User's domain:** primarily chemistry and structural biochemistry, but the
tools stay general. **Search and verify through `tools/scholar.py`**, never
`pubmed.py` alone — three of five queries drawn from the user's own
surface-science work return **zero** PubMed hits, so a PubMed-only sweep reads
exactly like an empty field.

## Where Everything Is Written Down

**This file is the current state and nothing else.** Read the one below that
answers your question; do not reconstruct an answer from this page.

| File | What it holds |
|---|---|
| `AGENTS.md` | The contract an agent reads first: architecture, how to run the engines, the skills, context isolation, routing, connectors, the house rules, and the suite to run before finishing |
| `README.md` | The user-facing page: what it is, how to install it, what to say |
| `CONTRIBUTING.md` | Working *on* the toolkit: layout, adding a skill or an engine, distribution, who owns which doc |
| `system-changes.md` | The live defect list, **local to this machine and gitignored**. Engine-written — never edit it by hand. The status ladder is load-bearing: fixing an item does not clear it (`log-issue --fixed --detail` moves it to `FIX ATTEMPTED`), and **only a completed run may write `VERIFIED FIXED`**, which also clears its detail |
| `help/instructions.md` | The in-depth guide for members. Where it and a `SKILL.md` disagree, the skill is right |

The design notes (`specs/`, `history/`, `TODO.md`) are the maintainer's and are
not in this repository; see *Current State*.

## Architecture

```
skills/<name>/SKILL.md  eight skills - thin callers: invoke the engines, reason
                        over JSON, talk to the user
tools/                  eighteen engines - standalone CLIs, no agent required,
                        --json on either side of every subcommand
tests/                  suites that measure the engines against reality
```

**Which layer does a given thing belong in? If it has a right answer, it is
Python. If it takes judgment, it is markdown.** "Does this DOI exist and does
its title match?" is tested against reality; "should this coauthor's edit be
applied?" lives in a `SKILL.md`.

**Skills never reimplement engine logic**, and no skill body writes a literal
`python tools/x.py` — every call goes through the `<tools>` placeholder the
skill resolves for itself, which is what lets one `SKILL.md` serve both the
plugin route and the clone route. One engine, many callers: improving retrieval
in the engine upgrades every skill at once.

A third file type is *not* part of the toolkit: the `.R` scripts. Those are
written **into the user's project folder** by `setup-project-directory`,
`toc_graphic.py` and `graphic_figure.py`, and are then the user's to edit.

## Current State

**2026-09-28 — the repository is public.** The engine's design notes
(`specs/`, `history/`, `TODO.md`) and the defect ledger were written against
real, often unpublished projects and quote them, so they stay on the
maintainer's machine and in a private notes repository; all four are
gitignored here. The code, the skills, the help and the tests are public. A
comment or a doc that cites `specs/<name>.md` is citing one of those notes.

**Nothing a project contains may be written into this repository.** Not a
sentence from a draft, not a result, not a filename, not a collaborator's
name. An example in a skill, a test or a comment is made up for the purpose
(`EXAMPLE_STUDY`, Ada Bell, the thermophile arrays). The defect ledger is the
one place a project specific may be recorded, and it never leaves the
machine: `tools/system-changes.template.md` is what a fresh clone starts from.

**The ledger clears itself.** When a run verifies a fix, the item is cut down
to its title, code, status and last sighting, and every other line - the
detail, the specific example - is dropped. Run history keeps its newest 25
rows.

### The Engines

| Engine | What it does |
|---|---|
| `scholar.py` | Six literature indexes behind one normalized record and one verification cascade — Crossref, OpenAlex, Europe PMC, PubMed, arXiv, Semantic Scholar, plus PubChem. **The search and verification route** |
| `pubmed.py` | The PubMed backend `scholar.py` wraps, and the only route to full text (`sections`) and OA PDFs |
| `structure.py` | AlphaFold, UniProt, RCSB, PDBe, EMDB. Keyless, no skill |
| `sequence.py` | BLAST, the genes either side of one on a contig, and what to do when AlphaFold DB has no model. Submit-then-poll, and the CLI does not hide it. **Evidence for feasibility and methods, never for a gap** — a BLAST miss is not evidence of novelty. Offered in idea-generation Stage 5, never run unasked |
| `scaffold.py` | The project structure, the R float pipeline, `github-offer` (whether to offer GitHub and the hand-over to github-ai-project-manager; the engine no longer runs git itself), `adopt` (scaffold a folder that already has work in it and propose a home for the rest, now with `--depth` and `--sweep-unknown`), `survey`, `float-order` (what the filenames imply about the float set — proposes, never renames), `report` (what this folder can and cannot now do), `drop` (an empty scaffold folder the project does not need, recorded in `project.yml` as `dropped_dirs:` so it is neither re-created nor reported missing; `--restore` undoes it), `image-floats`, `harvest`, `prefill`, `float lint` |
| `idea.py` | The deterministic half of idea generation: resolve, context, write, merge |
| `manuscript.py` | State and the build — 33 subcommands: init, status, config, ai-disclosure, outline, plan, agent-brief, reading-findings, ai-voice-findings, flag-answers, retention, run, round, retire-journal, handoff, assemble, supplementary, landscape, submission-package, submission-names, strays, import-prose, move-passage, log-issue, reference-doc, format-check, completeness, figure-files, authors, bib, ris, ingest, response. The only thing here that needs pandoc |
| `prose.py` | Every prose rule with a countable answer — 16 subcommands, `italics`, `entity_forms` and `abbreviations` the newest. Pure stdlib, fully offline. **Nothing here gates**: every one exits 0 with findings. **TWO of them WRITE** under `--fix` — `spelling`, and the always-italic half of `italics` — and both earn it the same way: an exact substring, a lookup, no threshold, every false positive nameable. **Nothing here is merely reported, either** — prose 12: `readability` and `metaprose` findings are handed to module 1c and fixed before any checker reads the paper. The densities are not, and that is what keeps the pass from chasing a number |
| `learn.py` | The learning loop: what the coauthors' edits teach, promoted to rules. **`rules.yml` never leaves the machine** |
| `docx_edits.py` | Tracked changes and comments out of a `.docx`. Never writes one |
| `toc_graphic.py` | The graphical abstract as an authored PowerPoint float |
| `graphic_figure.py` | PowerPoint panel art inside a figure folder |
| `labpack.py` | The lab resource pack and the drop-zones: which pack this copy resolves, where a member's own material may safely live (`~/.paper-engine/resources/`, which no plugin lifecycle touches), and the live inventory read — offered, never automatic, written nowhere. **No lab name appears in this engine** |
| `release.py` | The dated version both plugins carry, the offline check that notices when one is overdue. The version check **reports, never refuses** — a commit is not a release; it uses `git log -G`, never `-S`, because `-S` counts occurrences and so finds the commit that created the version line rather than the bump. |
| `remote.py` | **The only thing in the toolkit that reaches a network** — one `git ls-remote` per marketplace, using the member's own login, cached to `~/.paper-engine/remote-check.json`. Nothing on a drafting path calls it; `labpack.freshness()` and `release.freshness()` read the cache and stay offline, and the suite asserts that against both files' source. Built 2026-09-23 for item 116, where both checks compared two copies that came down in the same download and therefore reported `current` for ever. **`refresh` runs at the opening of all eight skills** (item 117, 2026-09-24) - once a day per repository, 6s timeout, and it prints nothing unless there is news |
| `review.py` | The corpus a **review** is checked against: the scope contract, the recorded searches, the screening log, one record per source with the tier at which it was read, the evidence table, and the checks that hold every sentence to that tier. 12 subcommands. Stdlib only; reaches `scholar.py` through a subprocess |
| `install_skills.py` | The pointers that make the skills findable from any directory, and **`doctor`** - the one command behind README step 4: the programs, the packages and the pointers, each with what its absence blocks. Reports; installs nothing |
| `help_deck.py` | The flow-chart deck. **`build` overwrites a hand-edited file with no undo — nothing runs it except on request** |

### The Skills

`setup-project-directory`, `reorganize-directory`, `idea-generation`, `analysis`,
`refine-figure`, `create-graphic-figure`, `writing-engine`, `flag-resolver`. `AGENTS.md`'s
routing table is how a sentence reaches the right one.

`check-citations` and `submission-prep` were folded into `writing-engine` as
modules and are still callable standalone.

### What Was Last Measured

**2026-09-30, after GitHub moved to github-ai-project-manager.** Offline
suites on Python 3.12 with pandoc 3.1.3 and the optional packages, no R:
manuscript **2735** (1 skipped), portability **1236**, scaffold **574**
(6 skipped; the 12 checks of the old sync gone, 36 for the offer added),
labpack **359**, prose 475, learn 259, install 190, idea 192, review 161,
docx_edits 107, help_deck 118. Run the suites `AGENTS.md` names before
claiming a change is done.

**2026-10-01, after `scaffold.py drop`** (Linux, Python 3.12, pandoc 3.9, no
R): scaffold **594** (6 skipped, all R; 20 checks added), manuscript 2735,
labpack 359, install 190, idea 191 of 200 (8 added). The 9 idea failures
(refs.bib and the pubmed.py title matcher), 12 portability failures and 7
help_deck failures were already failing on the parent commit in the same
container, so they predate this change.

## The Failure This Toolkit Exists to Catch

Almost every defect in `history/BUILD-LOG.md` is one shape: **the check ran, the build
succeeded, and what was wrong is what got reported — at exit code 0.** A
manuscript built with no manuscript in it. A trimmed project reported as empty
while 5262 words sat one directory up. A status ladder with no writer for its
middle rung. A detector wired to a logger that discarded every firing.

So: **a wrong answer at exit 0 is worse than the refusal it replaced**, and a
refusal that gets worked around is worse than a missing feature, because the
workaround looks like output.

`AGENTS.md`'s **House rules** section is the enforced half of this and is not
repeated here. What follows is the toolkit-internal half.

### Traps That Have Already Been Paid For

- **Anchor XML paths to their true parent, never `.//`.** PubMed XML embeds a
  *cited* paper's `<ArticleIdList>`; PMC full text puts `<ref-list>` under
  `<back>`; `.docx` nests `<w:ins>` inside `<w:pPr>`. All three were real bugs,
  and all three read as plausible output.
- **A lookup that is almost right is the recurring bug.** Resolve floats by
  **stem**, never by filename, and key them by **label** — under one-folder-per-float
  every figure writes `figure.png`, so stems collide. The same class:
  `_recorded_fields` matching no key containing a capital (which is every key
  carrying a unit), a non-greedy `\((.+?)\)` stopping at an inner parenthesis,
  `_list_span` used on a mapping.
- **Contracts duplicated on purpose, pinned by a test.** `CAPTION_HEADING_RE`
  (three files, one of them R), `FLOAT_DIR_RE` (three), the
  `# --- LAYOUT CONTRACT ---` block (`manuscript.py`, `prose.py`,
  `toc_graphic.py`, `scaffold.py`), `ADHERENCE_LEVELS`, `ANALYSIS_MARKER_HEAD`
  (Python and R). **Change one, change all, re-run both suites.** Repetition is
  only safe while something compares the copies.
- **`tests/x.py` and `tools/x.py` share a name**, so every suite loads its
  engine by path under a distinct module name via `importlib`. Do not
  "simplify" that back.
- **Staleness is a content hash, never an mtime.** OneDrive rewrites mtimes on
  sync. A ledger believed over the folder is worse than no ledger, because it
  is believed.
- **The form must not be mistakable for the answer.** Template instruction
  prose under a heading became a paper's Funding statement; the `numbers` check
  read its own instruction comments; a scaffolded stub reported as filled in.
  Instructions go in HTML comments, and "is this written yet" is answered by
  comparing against the pristine template, never by a heuristic.
- **Unsourced is unchecked, never inferred.** A journal requirement is sourced
  with a URL or it is `unknown`. An unsourced default that changes what the
  editor receives is not a conservative one — line numbering went on
  unconditionally for months against journals that never asked for it.
- **`stats-check`'s context is an explicit allow-list**, never inherited. It is
  denied `plan/README.md`. If it can see the hypothesis the module is pointless
  and nothing visibly fails. A *given directory* can quietly reverse a denial —
  that is why `data_dir` no longer exists.
- **A measurement instrument may not become a gate — and "it is not a gate" is
  not a reason for nothing to act on it.** `voice`, `metaprose`, `float lint`
  and the length budget all exit 0 with findings. A gate on a prose measurement
  makes the drafter optimize the metric, and a lint that refuses a render
  teaches people to skip the render. **But the second half of that sentence was
  missing for months**, and what it produced was a draft handed back with a
  list of things wrong with it and nothing done about any of them. Prose §12:
  a finding that **names a sentence** goes to the rewrite pass and is fixed
  before the user sees the draft; a finding that names only a **number** goes
  to the user and is withheld from every rewrite. The first is a defect, the
  second can only be chased. Sort new checks into those two boxes.
- **R is installed but not on `PATH`** — `C:\Program Files\R\R-4.6.0\`, and
  `which Rscript` finds nothing (checked 2026-09-10). Look under
  `C:\Program Files\R\*\bin\` before reporting R missing; `history/BUILD-LOG.md`'s
  2026-09-08 entry says R is absent from this machine and that is wrong. `C:\Windows\System32\convert.exe` is NTFS
  convert, not ImageMagick; probe for `magick`. On this machine the only
  working `.pptx` render backend is PowerPoint COM.
- **Reading the file you wrote is not the same as opening it.** In OOXML,
  `w:rFonts/@asciiTheme` beats `@ascii` and `w:color/@themeColor` beats `@val`,
  so a `styles.xml` can name Times New Roman and render in Aptos blue.
- **An example in the toolkit's own text reaches every agent that reads it**,
  for the life of the project it came from — including the denial reasons and
  `why`/`task` strings in `manuscript.py`'s `AGENT_MODULES`, which are
  documentation by the route they travel. Examples are fictional;
  `tests/portability.py` greps for sample-id *shapes*.

## Working Style

The user wants pieces spec'd and verified **one at a time** before moving on,
and wants claims about reliability **measured rather than asserted**. Build,
test against reality, show the numbers, then proceed. A conclusion drawn from a
truncated read is confident and wrong in exactly the same way as one from a
complete read.

**Title Case for every title, heading, label and caption** — a standing
preference, not a choice about one deliverable.
