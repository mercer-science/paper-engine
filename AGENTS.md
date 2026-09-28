# Agent instructions — Paper Engine

Entry point for any coding agent working in this repository, whatever harness it
is running under. `CLAUDE.md` in this directory is the short project brief —
current state, the documentation map, and the traps that have already been paid
for — and `history/BUILD-LOG.md` beside it is the long-form record of how each piece was
built. Everything in both applies here too. This file is the part that is
**not** specific to one agent product.

## The architecture, and why it survives a change of harness

```
skills/<name>/SKILL.md  thin callers - invoke the engines, reason over JSON, talk to the user
tools/                  shared engines - standalone CLIs, no agent required
tests/                  reliability suites that measure the engines against reality
```

**The engines are the product; the skills are a script for driving them.** Every
`tools/*.py` is a plain command-line program that runs with no agent, no API
key, and no harness. That is deliberate, and it is what makes this toolkit
portable: a different agent needs to read the markdown, not reimplement the
Python.

| Engine | What it does |
|---|---|
| `tools/pubmed.py` | PubMed only. Tiered reading (`sections`) and OA retrieval (`pdf`) are its alone; for search and verification use `scholar.py` |
| `tools/scholar.py` | search and verification across PubMed / Crossref / OpenAlex / Europe PMC / arXiv, with Semantic Scholar behind `related` and `cited-by`, plus PubChem substances. Wraps `pubmed.py`; **use it for search and verification**. Also classifies what KIND each reference is (`source-types`), reads the citation graph for later papers that may disagree (`disputed`), and writes the minimal corpus record any project can have (`corpus-fill`) so the sentence-level attribution pass has something to read |
| `tools/structure.py` | structures rather than papers: AlphaFold, UniProt, RCSB PDB, PDBe and EMDB behind one record. Keyless, no skill. **Evidence for feasibility and methods, never for a gap** |
| `tools/sequence.py` | sequence similarity and genomic context: NCBI BLAST (submit-then-poll), the genes either side of one on a contig, and what to do when AlphaFold DB has no model. Keyless except `fold --submit`. **Evidence for feasibility and methods, never for a gap** |
| `tools/review.py` | the corpus a REVIEW paper is checked against: the scope contract, the recorded searches, the screening log, one record per source with the **tier at which it was read**, the evidence table, and the checks that hold every sentence to that tier. A review's `data/` |
| `tools/scaffold.py` | the universal project structure |
| `tools/idea.py` | resolve a project, report what it records, write a settled idea |
| `tools/docx_edits.py` | tracked changes and comments out of a `.docx` |
| `tools/manuscript.py` | journal state, the claims ledger, run progress, round snapshots, the Word build, the edit ledger, the engine's own defect list, and `strays` - which document in a project folder is the paper |
| `tools/prose.py` | every manuscript rule with a countable answer. TWO of them **correct** rather than report: `spelling`, and the always-italic half of `italics` |
| `tools/release.py` | the dated version both plugins carry, the offline check that notices when one is overdue, and `freshness` - whether THIS installed copy is behind the repository it came from, read from the cache `remote.py` left on disk. No network call, no token. Reports, never refuses - a commit is not a release, and a stale toolkit still drafts. |
| `tools/remote.py` | **the only engine that reaches a network.** One `git ls-remote` per installed marketplace, using the member's own git credential - no token, and it cannot prompt. It caches the answer to `~/.paper-engine/remote-check.json`, and the two `freshness()` readers read only that. `refresh` is what runs by itself - every skill opens with it, rate-limited to one ask a day per repository, 6s timeout, silent unless there is news; `check` is the same thing typed on purpose. Nothing on a drafting path calls either: 6.2's *no network call on the path that generates an idea* is asserted against the source of both halves, and an opening step is not that path |
| `tools/labpack.py` | the lab resource pack and the drop-zones: which pack this copy resolves, where a member's own material may safely live, the live inventory read that is offered rather than automatic, and **`brief`** - one project's copy of the pack, every step quoted and dated, with that project's decisions and open questions against each one. **Content only - no lab name appears in this engine** |
| `tools/learn.py` | the learning loop: what the user's edits teach, the rules induced from them, and the drafting brief that carries them into the next paper |
| `tools/toc_graphic.py` | the TOC / graphical abstract: the `.pptx` to edit, the render script, the staleness hash, the journal's own example graphics in `examples/`, the block in `source_text/` |
| `tools/graphic_figure.py` | PowerPoint panel art inside a figure folder: the `.pptx` to draw in, the render script, the staleness hash |
| `tools/install_skills.py` | make the skills discoverable from any directory, and **`doctor`** - is this machine set up to run the engine: the programs, the packages and the pointers, each row saying what its absence blocks. It is what the README's *"check the paper engine is set up on this machine"* resolves to. Reports; installs nothing |
| `tools/help_deck.py` | build `help/paper_engine_overview.pptx` — the flow chart of how the stages and agents interact, drawn from the module list. Reads no project and writes nothing a paper depends on. **`build` overwrites the shipped deck and discards hand edits, so run it only when the user asks for it in those words** — see the standing rule below |

Run any of them with `--help`. Every command takes `--json`, on **either side**
of the subcommand. Exit codes are meaningful: `0` clean, `1` findings, `2`
refused or unusable input.

## Running the engines

- **Interpreter:** invoke with whatever Python is on PATH — `python` on Windows,
  usually `python3` on Linux and macOS. The skills are written with `python` for
  brevity; substitute freely. Python 3.12 or newer: `manuscript.py` nests quotes
  inside f-strings, which 3.11 and older refuse to parse.
- **Dependencies:** there are exactly five Python packages, and none of them is
  needed to take a paper from an idea to a built `.docx`:

  | package | needed by | if missing |
  |---|---|---|
  | `requests` | `pubmed.py`, `scholar.py`, `structure.py` and `sequence.py`, always | it exits at startup with the pip line |
  | `lxml` | `docx_edits.py`, `help_deck.py`, and `manuscript.py ingest` through the first | that command reports it; nothing else is affected |
  | `pypdf` | reading a PDF reviewer report, re-distilling the writing guides, and `toc_graphic.py examples` | that path reports it; nothing else is affected |
  | `pillow`, imported as `PIL` | `manuscript.py` converting a rendered figure into the journal's upload format (`submission-package`, `figure-files --export`) | that figure is reported **DID NOT RUN** with the pip line and nothing is written in its place; a render already in an accepted format is copied without it |
  | `python-pptx`, imported as `pptx` | `help_deck.py`, and `graphic_figure.py geometry` | `help_deck.py` reports it and no paper is affected; the geometry check reports **DID NOT RUN** and exits 2, and the float still renders |

  Every other engine — `scaffold.py`, `idea.py`, `manuscript.py`, `prose.py` —
  is standard library only and runs on a bare Python install, and so is every
  command of `toc_graphic.py` except `examples`, which reads the journal's
  guidance PDF through `pypdf` and says so plainly when it is absent.

  `help_deck.py` is the one engine whose dependencies are worth nothing to a
  manuscript: it writes `help/paper_engine_overview.pptx`, the deck that
  explains the toolkit. A machine that cannot run it is missing a slide deck,
  not a capability.

  **`graphic_figure.py geometry` is the second reader of `pptx` and it is not
  in that class** (item 103). It opens a drawn float's `.pptx` and checks
  that the slide's own elements do not collide — a leader line through
  another label's words, two labels on top of each other. Without the
  package it says **DID NOT RUN** and exits 2, and `render` goes ahead. That
  is deliberate and it is the only honest option: a check that cannot run and
  stays quiet is worse than no check, because the silence is
  indistinguishable from a pass.

  **Never rebuild the help deck unless the user asked for it.**
  `help/paper_engine_overview.pptx` is hand-editable and is expected to
  carry hand edits. `help_deck.py build` overwrites it in place and there
  is no undo, no backup and no warning — so it is not part of any preset,
  any build order, or any "while I am in here" tidy-up. A rebuild is a
  thing the user asks for, and if the module list has changed the right
  move is to build to a scratch `--out` path and tell them what differs.
  Beyond
  Python, `manuscript.py assemble` shells out to **pandoc**, and the R pipeline
  in a scaffolded project needs **Rscript**.
- **Rendering a graphical abstract needs a slide renderer, and may find none.**
  `toc_graphic.py` writes an `.R` script that tries PowerPoint via COM, then
  LibreOffice headless. When neither is present it **stops and tells the user
  to export the PNG from PowerPoint by hand** — that is a supported path, not
  a failure, and the pipeline picks the file up identically. It must never
  quietly emit a wrong-sized image. Note that `convert.exe` on Windows is NTFS
  convert, not ImageMagick: probe for `magick`.
- **R is installed but is often not on PATH.** Anything shelling out to R must
  look under `C:\Program Files\R\*\bin\` before reporting R missing;
  `manuscript.py` has `_rscript()` for this.

## The skills

Eight, each a directory under `skills/` holding a `SKILL.md`:

| Skill | Read it when the user wants to |
|---|---|
| `setup-project-directory` | start a project, scaffold the tree, repair a broken one |
| `reorganize-directory` | sort out a folder that already has months of work in it: depth, the sweep into `sandbox/unsorted/`, the figure-order read, and a report of what the folder can and cannot now do |
| `idea-generation` | work out what to write about, find a gap, decide the float set |
| `analysis` | work out which tests the data supports, fill in `data/analysis/analysis.R` with the user, run it, and read back the numbers. Writes numbers and R; never prose and never interpretation |
| `create-graphic-figure` | give a figure panel drawn artwork - a scheme, a diagram, a mechanism - authored in PowerPoint |
| `refine-figure` | change how a float looks: edit the generated `figure.R` / `panels.R` / `table.R`, render, and hold the image against the claim its caption makes |
| `flag-resolver` | walk the outstanding `**[FLAG: ...]**` markers with the user, one at a time, and record the answers into `edits_status.md` as `pending`. Writes no prose; it runs between rounds, and the writer applies what it recorded |
| `writing-engine` | draft, revise, assemble, check, or submit a paper |

Each `SKILL.md` opens with YAML frontmatter carrying `name` and `description`.
Some harnesses load these automatically from that frontmatter; **if yours does
not, they are ordinary markdown instruction files — read the relevant one in
full before starting that kind of work.** Nothing in them depends on being
auto-loaded.

**One skill is not one file.** `writing-engine` is a spine plus per-stage
reference files under `skills/writing-engine/reference/`, because the single
file had grown to 219 KB — larger than the whole engine payload of a full
submission round — and a harness reads a skill's body when the skill is
invoked, so a context cleared mid-round re-paid for all of it before it could
be told which part it needed. The spine is what every invocation needs; the
reference files are read one stage at a time, and `manuscript.py handoff
--stage <stage>` names the one to read. **`HANDOFF_STAGES` in `manuscript.py`
is the routing table, and `tests/manuscript.py` holds it against the reference
directory in both directions** - a file no stage names, and a stage naming a
file or a heading that is not there, both fail the suite. A section moved out
of the spine and routed to by nothing is a rule deleted with extra steps, so
moving one is a two-file change by construction. The pointer mechanism is unchanged:
the reference files are siblings of the real `SKILL.md`, resolved relative to
it, and they travel with the skill directory on both the plugin route and the
clone route. `specs/context-compaction.md` §3 is the reasoning.

**Being found is a separate problem from being read.** A harness that indexes
skills reads them from its own directory, so a session started in the user's
paper folder has none of this in scope — and that failure is silent, because an
agent that never heard of the skill simply writes the paper without it.
`tools/install_skills.py install` writes a pointer into that directory for each
skill: the frontmatter copied verbatim, plus the absolute path of the real
`SKILL.md` and of this toolkit. Pointers, not copies, so the instructions stay
in one place; `--dest` takes any directory, and `status` says when a pointer has
gone stale. It never overwrites or deletes a file it did not write.

## What a harness must provide, and what to do if it does not

The skills are written against four capabilities. Only the second one changes
the *output* if it is missing, and it changes it silently, so it is the one to
take seriously.

| Capability | Used for | If the harness lacks it |
|---|---|---|
| Run a shell command | every engine call | nothing works; stop and say so |
| **A fresh, restricted context** | the checking modules of `writing-engine` — and, between the stages of one round, the orchestrator's own | see below — this one matters |
| Read and write files | source text, config, reports | nothing works |
| Web search and fetch | journal author guidelines (`writing-engine` §4) | mark every requirement `unknown` and ask the user to paste the guidelines; **never infer one** |

### The one that matters: context isolation

`writing-engine`'s checking modules each declare what they are **given** and
what they are **denied**. The strict case is `stats-check`, which must not see
the hypothesis, `plan/README.md`, `plan/outline.md`, or the caption claim lines.
An agent that knows what we hoped to find reads an ambiguous result as
favourable, and **nothing visibly fails when the isolation is broken** — the
report still looks fine, it is just worthless.

**The allow-lists are not prose any more.** `manuscript.py agent-brief`
resolves a module's list against a real project and emits the sub-agent's
prompt; the caller hands it over verbatim rather than composing one. A prompt
written by the spawning agent is written by something holding everything the
denied list is about, and that was the route the contamination took.

```bash
python tools/manuscript.py agent-brief "<project>" --module stats-check        --journal "<journal>" --json     # -> .prompt, .given, .denied, .spawn
python tools/manuscript.py agent-brief . --list   # all 15, and which 7 spawn
```

Harnesses differ in how to run it:

- **If it can spawn a sub-agent with a chosen context**, do that, with the
  brief's `prompt` as the whole prompt. Never with a *fork*-style sub-agent
  that inherits the parent's context — that undoes every denial while still
  reporting that a sub-agent ran, which is why the brief names it in
  `spawn.never`.
- **If it cannot**, run that module as a **separate session** — a fresh
  invocation whose prompt is that same `prompt` field and nothing else.
  Cheaper and equally valid.
- **If neither is possible in the moment**, say so and skip the module. A
  `stats_report.md` produced by an agent that has read the hypothesis is worse
  than no report, because it will be trusted.

Nine modules run this way — `outline`, `draft-sections`, `abstract`,
`revise-prose`, `comprehension-check`, `attribution-check`, `stats-check`,
`citation-check`, `reviewer-check` — and six of those are **blind**, where
breaking the isolation does not fail visibly. The other eight are
deterministic engine calls or read the reports the isolated ones wrote;
`agent-brief --list` gives the reason for each. The contract is enforced in
`tests/manuscript.py`, which asserts that no denied file can reach a module
through its given list or its prompt.

**Two of the blind ones are new, and each has a denial that carries the
whole module.**

- **`comprehension-check`** is the only module that READS the paper rather
  than checking its conformance. Every other check can be satisfied by
  prose no reader can follow. It is denied `plan/outline.md` and
  `plan/README.md` above all, because **a reader who has read the plan can
  follow anything** — they already know what the paper is going to say. It
  reports and never rewrites.
- **`attribution-check`** is a review's `stats-check`, and it is denied the
  review's **thesis**. An agent that knows what a review argues reads an
  ambiguous abstract as agreeing with it, and it does so sincerely — a
  research paper has one hypothesis and a handful of tests, while a review
  has a claim in nearly every sentence.

## Routing — getting from a sentence to the right module

**A skill that is installed but never *reached* is the failure this section
exists to stop.** `install_skills.py` solves being found by the harness; this is
about being found by the sentence. Both failures are silent in the same way: the
paper still gets written, just without the module that existed to make it right,
and nothing reports the omission because nothing knew to.

**Two rules first, because they are what keep this from being annoying.**

**Routing offers, it never runs.** A cue names the module and asks. One line,
and "no" ends it. The whole toolkit is built on *nothing is written that was not
recorded* and on previewing before writing; a skill that fires itself off a
passing mention breaks both.

> That sounds like `create-graphic-figure` — it puts a PowerPoint panel in
> `plan/figures/Fig03/` that you draw yourself and the pipeline renders. Want me
> to set it up?

**One skill owns the turn.** Two cues in one sentence is common ("set up the
project and find me a gap"), and running both produces a conversation with two
agendas. Name both, take the one that has to happen first, say the other is
next. `setup-project-directory` and `idea-generation` are the pair this happens
to most, and **either order is legitimate — ask which they would rather start
with** rather than choosing for them.

The table is not exhaustive and is not a parser. It is the list of phrasings
that must not be missed, written down so that "it did not occur to the agent"
stops being the explanation.

| The user says something like | Route to | Note |
|---|---|---|
| what should I work on, is there a gap, what has not been done, what would make a paper | `idea-generation` | offer the three save destinations at the end; never assume a project |
| set up / start / scaffold a project, make me a folder for this paper | `setup-project-directory` | offer `idea-generation` if no idea is recorded |
| my folder is a mess, files everywhere, I don't know where anything is, sort out / clean up / tidy this folder | `reorganize-directory` | it calls the same `adopt`; what it adds is `--depth`, `--sweep-unknown`, `float-order` and the end-of-run report |
| check the paper engine is set up, is this installed properly, did the install work, what am I missing | `install_skills.py doctor` | modular; no skill needed. **This is README step 4 and it has one command** — do not improvise a set of shell probes. It reports the programs, the packages and the pointers with what each gap blocks, installs nothing, and exits 2 when something required is absent |
| is anything missing, did I lose a file | `scaffold.py check` | modular; no skill needed |
| I have a folder full of work and want the structure around it | `setup-project-directory` | it runs `scaffold.py adopt` - scaffolds, then proposes a home for every unfiled file. Moves nothing without `--apply` |
| what is this `notes/` folder, can the drafter use it | `scaffold.py survey` | modular; `writing-engine` runs it once per project and records the answer |
| I need fake / dummy / placeholder data to test a figure | `idea.py mock` | modular |
| show me what a figure would look like before I have data | `scaffold.py mock-floats` | needs mock data first; say so |
| my evidence is images, not rows - lay out my figures | `scaffold.py image-floats` | grey panels describing the image expected in each. NOT mock: no watermark, and the .docx builds it |
| a scheme, a diagram, an apparatus, a mechanism, a sample-prep flow, **a protein figure**, a structure panel, "draw" | `create-graphic-figure` | if it is a *structure*, `structure.py` fetches the model and the panel is still drawn here |
| this figure is ugly / wrong colours / wrong panel order / rebuild it | `refine-figure` | `scaffold.py float lint` is its engine half |
| what tests should I run, set up my statistics, my analysis.md is missing | `analysis` | fills in sections 1 and 2 of `analysis.R` only; the battery in 3 and 4 is provided and is never edited per project |
| what is still blocking this paper / answer these flags / it says NOT SENDABLE | `flag-resolver` | `prose.py flags --json` reads them, `manuscript.py flag-answers` records the answers. Offered at the end of a writing-engine run, never auto-run |
| find me the paper that says X, is this citation real, check my references | `scholar.py` (`search`, `verify`, `check-refs`) | modular; never answer from memory |
| what does this compound weigh, what is its SMILES | `scholar.py compound` | modular |
| is there a structure for this protein, what does AlphaFold have | `structure.py` | feasibility and methods only — **never gap evidence** |
| what else looks like this sequence, what is on this operon, fold this | `sequence.py` (`blast`, `neighbors`, `fold`) | offered in idea-generation Stage 5, **never run unasked**. A BLAST miss is not evidence of novelty |
| draft / write / rewrite the introduction, the methods, the whole thing | `writing-engine` (drafting, scoped) | state the plan and wait. **A named subset of sections scopes the ROUND, not just the drafter** — the build is a second question |
| what settings was this image taken at, what microscope was that, fill in my methods | `scaffold.py harvest` | modular; the instrument wrote them into the file, so they are never a `[FLAG: author]` |
| format for *journal*, does this fit their limits, what do they require | `writing-engine` + `format-check` | never infer a requirement |
| my coauthor sent edits, here is a tracked-changes file | `writing-engine` ingest + `docx_edits.py` | modular for the extract |
| the reviews came back | `writing-engine` `revision` preset | |
| build the .docx, assemble it, give me something to send | `manuscript.py assemble` | |
| is this ready to submit, what does the journal still need | `writing-engine` `submission-package` | the AI-disclosure row is part of this |
| write a review of X, a review paper on X, survey the literature on X, a perspective / tutorial / mini-review | `writing-engine` with `paper_kind: review`, and **offer the project directory in the same breath** | a review needs a folder because **the corpus is the review's data**. `manuscript.py config <p> --journal X --paper-kind review`, then `review.py protocol --init` |
| what has been published on X, build me a reading list, screen these papers, how many papers are there on X | `review.py protocol` then `search` | modular; no skill needed |
| is my corpus complete, what did I miss, how many did I exclude and why | `review.py coverage` / `prisma` | modular |
| is this readable, does this make sense, is it too technical, does it flow | `prose.py readability`, and offer `comprehension-check` | the module reads the paper and reports what it understood; `readability` is the countable half and costs nothing |
| the engine did something wrong / it keeps doing X | `manuscript.py log-issue` | **never** write into `system-changes.md` by hand |

**When the cue is ambiguous, ask one question with the two candidates named.**
Do not run the cheaper one to find out.

**"Find me a gap" is `idea-generation`; "write me a review" is a review.**
They share a literature sweep and they are different jobs — one ends in a
decision about what to do next, the other in a paper. Ask which, in those
words, when the sentence could be either.

The single most expensive misroute is **"figure" meaning four different
things**: a plotted float (`figure.R`), drawn art (`create-graphic-figure`), an
existing float that needs changing (`refine-figure`), and the graphical abstract
(`toc-graphic`). Ask which, in those words. Guessing produces a `.pptx` in a
folder that wanted a plot, and the user finds out a week later.

## Connectors — discovery only, never load-bearing

Some harnesses can attach third-party research connectors (Consensus, Elicit,
Scite, PubMed, and others). They are useful here and they are **never** part of
how a stage works. Three rules, short because they have to survive being read
quickly. Full reasoning: `specs/external-services.md` §2.2.

**1. A connector may widen a search. It may never close one.** Use one while
brainstorming, to catch vocabulary and papers the keyless sources miss. But a
gap is not established, and a candidate is not dropped, on connector output
alone.

**2. Nothing reaches a file until `scholar.py` verifies it.** A
connector-surfaced paper is a *lead* — a title and maybe a DOI. It becomes a
citation only after `scholar.py verify` returns a match, and it enters
`refs.bib`, a literature summary, `plan/README.md` or any draft only in the form
`scholar.py cite` produced. **A connector's output is somebody else's index,
which for citation purposes is the same epistemic category as a recollection:
plausible, unretrieved.**

**3. Every skill must complete its whole job with zero connectors connected.** A
person who has authorized nothing gets the same paper, from the same engines. A
connector may make a stage *better*; it may never be the reason a stage *works*.
That is what stops the group splitting into people whose toolkit behaves one way
and people whose behaves another.

**The consequence, stated plainly: no connector call is ever covered by a
test.** Availability varies per person and per OAuth state, so it cannot be. A
capability that cannot be tested cannot be relied on — which is exactly why
rule 3 is a ceiling rather than a preference.

**Offer, do not assume**, and attribute the source when reporting:

> "34 hits from `scholar.py` (Crossref 12, OpenAlex 15, Europe PMC 7), plus 6
> leads from Consensus — 4 of the 6 verified, 2 could not be retrieved and were
> dropped."

**"Could not be retrieved and was dropped" is the correct outcome**, and it is
reported rather than silently swallowed. Same honesty rule as the PubMed
coverage caveat: say which thing happened.

## House rules that hold regardless of harness

These are not stylistic. Each one exists because a specific failure was found
the hard way, and each is enforced by an engine that will refuse rather than
guess.

- **A defect found is a defect FIXED, in the same session that found it.**
  Set 2026-09-21. Filing an item and moving on is what produced a ledger
  carrying six OPEN entries nobody had started, the oldest of them months
  old — and every one of them had been *described* well enough to fix on the
  day it was written. So `log-issue` still files the item, because a fix
  needs something to check it against, and then the fix is made and
  `log-issue --fixed --detail` records what changed.

  **Two things this rule does not say.** It does not say close the item:
  `FIX ATTEMPTED` is a claim and `VERIFIED FIXED` is evidence, so only a run
  that exercised the code may write the third rung, and that has not changed.
  And it does not forbid filing without fixing — it narrows it to one case,
  which is that the fix belongs to a part of the system the current work
  cannot safely reach. Then the item says so in as many words, and says what
  would have to be true to reach it. "I was busy with something else" is not
  that case.

- **Nothing is written that was not recorded.** Methods prose comes from
  `data/methods_facts.yml` and `provenance.json`; claims come from the evidence
  bracket on each `plan/outline.md` line; the reviewer response comes from
  `edits_status.md`. A missing value is a visible `**[FLAG: ...]**`, never an
  inferred one. An invented methods number is the worst output this toolkit
  could produce — it is unfalsifiable from inside the project and would go to
  print.
- **Never cite from memory.** Every citation goes through `scholar.py verify`
  before it reaches a written file, and `idea.py write` refuses a bundle whose
  literature carries no verdict. It is "every citation", not "every PMID",
  because a *Surface Science* paper has no PMID — and until `scholar.py` existed
  this rule was unsatisfiable for that half of the user's field. Record whichever
  identifier the paper actually has: a PMID, a DOI, or an arXiv id. `landscape`
  and `idea.py write` accept all three, and a DOI is the normal case here, not
  the exception.
- **Search through `scholar.py`, not `pubmed.py`.** A PubMed-only sweep of this
  group's subject returns nothing and reads exactly like an empty field, which is
  how an imaginary gap gets written. `pubmed.py search` asks one index of six.
- **A `PREPRINT` flag is not noise.** arXiv is last in the cascade, so anything
  that verifies as a preprint had no journal version to find. Cite it as a
  preprint or find the version of record; never drop the flag silently.
- **Never invent a journal requirement.** Sourced with a URL, or `unknown`.
- **Never write a `.docx` by hand, and never hand-edit a citation number.**
  `manuscript.py assemble` is the only thing that produces Word, and it carries
  three gates. Citation numbers are derived at render time; a patched number is
  correct until the next build and wrong forever after.
- **Never work around a refusal.** The mock-data gate, the citekey gates, and
  the flag gate on `submission-package` are the toolkit saying "I did not make
  this up". If one fires, fix the input.
- **The engine keeps its own defect list, and so do you.** `manuscript.py
  assemble` writes what a run noticed about *itself* into `system-changes.md` —
  a heading it supplied because the section order was in slugs, a citekey that
  reached gate 3, a round the marker never opened — and says so in one line at
  the end of the run. Only **engine** faults go there: a missing float, an
  empty `authors.md`, an unfinished section are the draft's problems and
  already have somewhere to live. `manuscript.py log-issue --list-codes` shows
  what the engine logs by itself, and `log-issue` is how you file what *you*
  noticed during a run, in the same shape. It refuses an item with no
  verify-by, because an item the next run cannot check is one nobody can ever
  close. **An item's description must be generic** — a statement about the
  engine, true of every project it could happen on — because this file is
  shared and a redacted copy travels upstream; anything project-specific goes
  in `--example`, which is the one field that is never linted and the one
  place a specific may live. A sample id, composition or oxidation state
  elsewhere is refused with the field and the token named; a measurement or
  ratio is reported and the item still files. Fixing an item never clears it,
  it moves to `FIX ATTEMPTED` and
  waits for a run that does not see it. `log-issue --fixed --detail "what you
  changed"` is the **only** writer of that middle rung (with `--code`, it files
  and marks in one gesture). Never hand-edit `system-changes.md` to mark
  something fixed, and note that `--fixed` cannot write `VERIFIED FIXED`: only
  a completed run may close an item, and only for a code the engine detects.
  **`--stage` says which stage found it, and every skill files with its own**
  — `setup`, `idea`, `analysis`, `figures`, `flags`, `writing`, `review`,
  `engine`. Unstated it is inferred from the journal and round, which are
  writing-stage concepts and empty everywhere else, so the inference is right
  in one stage and a guess in the other six; the record marks an inferred
  stage as inferred so a maintainer can tell the counted ones from the
  guessed. An unknown value is refused by name with the list. The engine half
  of this was always stage-agnostic — `log-issue` takes its project
  positionally with a default — so what was missing was six skills saying so.
  Until 2026-09-21 the stage was recorded only in the spooled copy of the
  record, which went upstream; upstream is gone and `manuscript.py` now owns
  `STAGES` and renders the stage into the item itself.
- **American spelling, and the engine corrects it rather than reporting it.**
  `prose.py spelling --fix` runs in module 1c before any checker reads the
  paper, and the drafting brief asks for the dialect so there is usually
  nothing to correct. It is the one check in `prose.py` that writes, and it is
  allowed to because the finding names an exact word, the fix is a lookup, and
  every false positive can be listed by name rather than estimated. Quotations,
  proper nouns, code spans and `references.bib` are never touched.
- **A reference that is not a paper is said to be one, not refused.**
  `check-refs` classifies before it verifies, and a database record, a dataset
  or a thesis never reaches the cascade — it comes back `not_a_paper`, not
  `not_found`, because `not_found` is the word reserved for a citation that may
  be invented. Citing a PDB entry in a methods section is correct; the engine
  says what the reference *is* and the user decides.

## Before you finish

Run the suite for whatever you touched. All of them are offline except
`reliability.py` and `fulltext.py`, which hit NCBI.

```bash
python tests/prose.py          # 475 checks, offline, ~1s
python tests/learn.py          # 259 checks, offline, ~2s
python tests/manuscript.py     # 2515 checks, drives real pandoc and real R
                               #    (~25 min)
python tests/install.py        # 221 checks, offline, a temporary --dest.
                               #   Also `doctor`
python tests/scaffold.py       # 594 checks, drives real R
python tests/idea.py           # 193 checks, drives real R
python tests/review.py         # 161 checks, offline; the corpus, the tier
                               #    ladder, the screening log, PRISMA
python tests/labpack.py        # 272 checks, offline; both ladders, the
                               #   at-risk drop-zone, the layer merge, the
                               #   four unknown inventory cases, the
                               #   connector-file read (rung 2 of 5.2), and
                               #   the project brief - every step, quoted,
                               #   and the notes carried forward
python tests/docx_edits.py     # 107 checks, offline
python tests/structure.py      # 197 checks, offline; a stub HTTP server
                               #    replays real AlphaFold/PDB payloads
python tests/sequence.py       # 100 checks, offline; a stub HTTP server.
                               #    Its payloads are CONSTRUCTED to the
                               #    documented shapes, not captured - said
                               #    in the suite's own docstring
python tests/toc_graphic.py    # 312 checks offline; --render drives a real
                               #   PowerPoint/LibreOffice export (319 with it)
python tests/graphic_figure.py # 62 checks offline; --render drives real
                               #   PowerPoint and real R (72 with it). The
                               #   geometry block needs python-pptx and
                               #   SKIPS, loudly, without it
python tests/cite.py           #  54 checks offline; --render puts the
                               #    written .bib through real pandoc (59)
python tests/citation_integrity.py
                               # 104 checks, offline; the byline, the year,
                               #    the rendered collision, the DID NOT RUN
                               #    line and corpus-fill. A stub Source
                               #    drives the real cascade
python tests/help_deck.py      # 118 checks, offline; holds the help deck
                               #    against the engine it describes
python tests/portability.py    # 1258 checks: the contract this file
                               #    describes, enforced
python tests/reliability.py    #  network: PubMed
python tests/fulltext.py       #  network: PMC and the AWS OA mirror
python tests/scholar.py        #  network: Crossref, OpenAlex, Europe PMC,
                               #    PubMed, PubChem. Re-derives the verification
                               #    thresholds; --quick to sample, ~10 min full
```

`tests/portability.py` checks that the claims on this page are still true —
stdlib-only imports, `--json` on both sides of every subcommand, and no skill
naming a mechanism only one harness has. If you add an engine or a skill, it
will tell you what this file needs to say about it.
