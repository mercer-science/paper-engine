# writing-engine — review papers

Read this **only when `project.yml:paper_kind` is `review`.** A review is a
second kind of paper, not a second toolkit: everything in the spine still
applies, and what follows is the part that differs. On a research paper none
of it does.

The spine carries the question and the two keys that answer it; this file
carries the consequences.

`manuscript.py handoff --stage review --json` hands a fresh context this file
with the corpus state.

## The corpus is the review's data

| research paper | review |
|---|---|
| `data/raw/` — what the instrument produced | `data/corpus/papers/` — one file per source |
| `data/analysis/analysis.md` — generated numbers | `data/corpus/synthesis.md` — the generated evidence table |
| `methods_facts.yml` — what we did, recorded as we did it | `data/corpus/searches.jsonl` — what we searched, recorded as we searched it |
| a number must appear in `analysis.md` | a characterization must appear in a paper record, **at the tier that record was read** |
| `stats-check`, blind to the hypothesis | `attribution-check`, blind to the thesis — **and it runs on a research paper too**, beside `stats-check`, over the Introduction and Discussion |

So **a review needs a project directory, and not for tidiness**: a review
with no folder has nowhere to put the one artifact that makes its claims
checkable, and the consequence is that a review becomes the one paper this
toolkit cannot check at all. `manages` becomes `[outline, floats, corpus]`; a
meta-analytic review holds both ledgers and nothing special-cases it.

## Layer 0 gains one rule

> **No sentence may characterize a source beyond the tier at which that
> source was read, and no source may be characterized at all unless
> `data/corpus/papers/<citekey>.md` exists and carries a `verified:` line.**

| `read_tier` | the review may say | it may not say |
|---|---|---|
| `title` | that the work exists, and its subject | anything about what it found |
| `abstract` | the paper's own stated claim and headline result, in the source's terms | its n, its conditions, its controls, its limitations, what its figures show, or that it *failed to consider* anything |
| `fulltext` / `pdf` | whatever the retrieved sections support | anything from a section that was not retrieved |
| `figure-digitized` | that a value was read off a published figure, said so | that the value is what the source reported |

**Measured, and this is why the rule is load-bearing rather than
decorative:** of 25 candidates returned for a real chemistry topic, 24 carry
an abstract and **0 carry a PMC identifier** — so `pubmed.py sections`, the
only tiered full-text route here, can read none of them. A chemistry review
built with this engine is an **abstract-tier review** unless the user
supplies PDFs. The engine will be asked, a couple of hundred times, to say
something specific about a paper it has read 180 words of, and the specific
thing will usually be true.

**A negative claim is a claim about the corpus, not about a paper.** *"X has
not been studied"* is licensed by `protocol.md` plus a recorded search in
`searches.jsonl` that would have found it — never by a gap in the drafter's
memory.

## What review mode releases, and what it tightens

One movement, not two: **the rules move off the numbers and onto the
sources.**

Released, and every one of them is layer 4: the `number_density` budget goes
to `high` (which already means *report only*), the one-third-numeric absolute
becomes advisory, the hedge and transition counts are re-pointed —
*report, observe, find, propose, argue, conclude* are attribution verbs, not
hedges, and a review is made of them — and *several citekeys supporting one
claim* is correct and standard, not packing. Comparing quantities across
studies **is** a review's substance.

Tightened, and it is the same rule repointed: **every number in a review's
prose must appear in the record of the source its sentence cites, and the
source must have been read at a tier that carries numbers.** A review is
allowed *more* numbers than a research paper and is held to a *stricter*
account for each one. That is not a trade; the budget was always about the
reader's attention and the ledger was always about the truth.

## The sections are the author's

`SECTION_FILES` is five fixed stems, four of them IMRaD. A review's body is N
thematic sections, and **the `##` headings under `# Structure` in
`plan/outline.md` ARE the section list**, in document order:

```
drafts/source_text_r1/
├── title_abstract.md
├── 01_introduction.md
├── 02_monolayer_formation.md
├── 03_defects_and_ordering.md
└── 04_outlook.md
```

The two-digit prefix carries the order and never reaches the page. Renaming a
heading renames the file, and the round snapshot in `obsolete/drafts/rN/` is
what makes that safe. A review whose `# Structure` names nothing gets
`title_abstract` alone, and `completeness` raises that as **blocking** —
a near-empty section list, every section written, is a paper that reports as
nearly finished with no body in it.

## "As little as a topic" — the input ladder

Every rung is a mechanism that already exists, and the engine's job is to
**say which rung it is standing on.**

| the user brings | what runs |
|---|---|
| a topic, nothing else | scope conversation → `protocol.md` → corpus build → proposed outline → r1 `compose` |
| a topic and inclusion criteria | the same, without the scope conversation |
| an outline | `outline --from <path>`, read with the same grammar and checked against the corpus |
| a rough draft | `--rough-draft <path>`; `draft_mode: edit` per covered section |
| a pile of PDFs | `review.py adopt --pdfs <dir>` → one record each at tier `pdf` |
| a `.bib` or an EndNote export | `review.py adopt --bib <file>`, then `review.py verify` |
| an `Ideas/` folder | `review.py adopt --ideas <dir>` — the explore-mode summary and its `refs.bib` are a corpus seed |
| a project directory | run in place; `manages` names what it holds |
| a published review to update | its reference list is the seed, and the new window is the contribution |

**Two refusals, and both are talked through rather than thrown.**

- **A topic with no protocol does not build a corpus.** *Everything about X*
  is not a scope; it produces four thousand hits, the engine keeps whichever
  came back first, and the review written from that is a summary of one
  index's ranking. `review.py protocol` names the three fields it needs;
  offer to draft them from the topic for the user to correct.
- **A corpus below the floor does not draft.** The floor is in `protocol.md`,
  **written by the user**, and the engine reports against it. No
  engine-chosen minimum: *the twelve papers that have ever used this
  technique* is a complete and correct scope, and a toolkit that refuses it
  has invented a threshold.

## What "does all the brunt of the work" can and cannot mean

**Unattended:** the searches, the paging, the deduplication, the
verification, the abstract-tier records, the synthesis table, the PRISMA
counts, the proposed outline, the draft, every check, the build, the package.

**Never unattended:** the scope (`protocol.md`), the screening decisions
(`screened.csv`), the argument (`plan/README.md`), and any claim about a
paper beyond what was retrieved. Those are the four places where an engine
that guessed would produce a review that is confident and wrong.

**The cost, so it is not a surprise.** Measured: verification runs **5.9 s
per reference** end to end, so a 200-reference bibliography is about **20
minutes** of wall clock for one full pass. That arithmetic is why verdicts
are cached, why retraction status never is, and why `attribution_check:
sample` is the r1 default — not thrift, but the fact that a check nobody will
wait for is a check that gets skipped.

## The corpus commands

```bash
python <tools>/review.py protocol  "<project>" --init   # the scope contract
python <tools>/review.py search    "<project>" --pages 2
python <tools>/review.py screen    "<project>" --key K --decide exclude --reason no-au
python <tools>/review.py record    "<project>" --key K --tier abstract --claims "..."
python <tools>/review.py verify    "<project>"          # --all before submission
python <tools>/review.py synthesis "<project>"
python <tools>/review.py tier      "<project>"          # the layer-0 check
python <tools>/review.py coverage  "<project>"
python <tools>/review.py prisma    "<project>"
python <tools>/review.py status    "<project>"
```

`literature-landscape` does **not** run on a review: the corpus *is* the
landscape, and writing `plan/literature_landscape.md` beside `data/corpus/`
produces two overlapping files, one of which goes stale. It leaves the preset
with the reason printed, not silently.
