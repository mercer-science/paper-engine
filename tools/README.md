# pubmed.py

NCBI E-utilities client. Its main job is **citation integrity**: proving a reference
points at a real, indexed paper — and catching retractions, errata, and fabricated
or drifted metadata — before it reaches a manuscript.

## Setup

**No account and no key are required.** NCBI's E-utilities are a public API. A free
key only raises the rate limit — 3 requests/second anonymously, 10 with a key.

```bash
python tools/pubmed.py setup --no-key      # use it right now, anonymously
python tools/pubmed.py setup --key <KEY>   # validated with NCBI before saving
python tools/pubmed.py setup               # interactive: explains, then prompts
python tools/pubmed.py status              # what's configured + a live probe
```

To get a key: sign in at https://account.ncbi.nlm.nih.gov/settings/ → **API Key
Management** → create key. A bad key is rejected at setup rather than saved, so you
never end up with a silently broken config.

Credentials land in `tools/.env`, which is gitignored. Real environment variables
override the file, so a key exported in your shell or set in `.claude/settings.json`
→ `env` also works.

Requires `requests` (already installed).

## Commands

| Command | What it does |
|---|---|
| `search "<query>"` | Search PubMed. `--limit`, `--from/--to` (years), `--sort`, `--filter "AND review[pt]"`, `--abstract` |
| `fetch <PMID...>` | Full records: abstract, MeSH terms, retraction notices |
| `verify` | Check one citation is real. `--title`, `--doi`, `--pmid`, `--author` (repeatable), `--year` |
| `check-refs <path>` | Verify a whole reference list. Accepts `.json`, `.bib`, or one title per line |
| `cite <PMID...>` | Format as `--style ama` (default), `apa`, or `bibtex` |
| `related <PMID>` | Papers PubMed considers related |
| `sections <PMID...>` | Named full-text sections. `--sections discussion,conclusions` (default), `--max-chars` |
| `pdf <PMID...> --out <dir>` | Download open-access PDFs; writes a `.md` stub when there is none |
| `status` | API key + connectivity check |
| `setup` | Configure access. `--key`, `--no-key`, `--email` |

Add `--json` to any command for machine-readable output. It works on either side
of the subcommand: `pubmed.py --json verify ...` and `pubmed.py verify ... --json`.

## Verification model

`verify` resolves a citation by the strongest identifier available — **PMID → DOI →
title** — then cross-checks the claimed metadata against the record it found.

**Status:**

- `verified` — an identifier matched exactly, or title similarity ≥ 0.93
- `partial` — title similarity 0.80–0.93; wording is close but not exact, worth a look
- `not_found` — nothing matched; for a citation that was supposed to be real, treat
  this as likely fabricated

**Flags** are raised independently of status, so a `verified` paper can still be
unusable. Flags include: retraction, expression of concern, erratum, year mismatch
(> 1 year off), DOI mismatch, PMID mismatch, and no cited author appearing on the record.

That split matters — the common failure isn't an invented title, it's a *real* title
carrying invented authors or the wrong year.

**Printed labels** narrow `status` by flags, so a retracted paper never reads as fine
at a glance: `[OK]` clean, `[CHECK]` verified but flagged, `[RETRACTED]` retraction or
expression of concern, `[PARTIAL]`, `[NOT FOUND]`. The JSON `status` field is unchanged.

**Exit codes:** `0` clean, `1` problems found, `2` error. `check-refs --fail-on-problem`
returns 1 if any reference is unclean, which makes it usable as a pre-submission gate.

## Tiered reading

Pulling full text for every search hit burns the context window on papers that
turn out to be irrelevant. Almost all triage — is this on topic, does it state a
limitation, does it conflict with that other paper — is answerable from the
abstract.

| Tier | Content | Command | When |
|---|---|---|---|
| 0 | Title, journal, year | `search` | Ranking a result set |
| 1 | **Abstract** | `search --abstract`, `fetch` | **Default. Always.** All triage happens here |
| 2 | **Named sections** | `sections` | Only after Tier 1 marks the paper relevant |
| 3 | Full text | `sections --sections ...` with every heading | Rare, explicit request |

**Never escalate a paper past Tier 1 without a stated reason.** "This is one of
the motivating papers and I need its stated limitation" is a reason. "It came
back in the search" is not.

Escalation is nearly always to discussion/conclusions — where authors state what
they could not do, which is the raw material for a gap. Methods matters for
feasibility; results when two abstracts are too compressed to tell whether the
papers actually conflict.

**Measured cost** (`python tests/fulltext.py`, 25-PMID set):

| | tokens |
|---|---|
| 25 abstracts (Tier 1) | ~8,200 |
| one escalated discussion | ~2,600 |
| **working point** — 25 abstracts + 5 discussions | **~21,200** |
| all 25 escalated instead | ~65,000 |

Section names are matched against the titles journals actually use, so `methods`
reaches "Materials and Methods" and "STAR★Methods" alike.

**Tiers 2–3 need a PMC record**, which exists only for the open-access subset and
the author-manuscript collection. For anything else the abstract is all there is;
`sections` returns `available: []` with `note: "no PMC record; abstract only"`
rather than erroring, so a caller can never quietly claim it read a discussion it
never saw.

## Open-access retrieval

`pdf` resolves in three steps and always writes something:

1. **PMC open-access subset** via the AWS Open Data mirror.
2. **Unpaywall**, for a legal OA copy at the publisher or a repository.
3. **A `.md` stub** with the same filename stem — title, authors, journal, year,
   DOI, links, abstract — so the folder is never silently incomplete.

Files are named `{FirstAuthor}{Year}_{slug}.pdf`, and a `refs.bib` covering every
requested PMID is written alongside.

Only open-access content; no paywall circumvention. Two real-world limits worth
knowing:

- PMC's own `/articles/<id>/pdf/` endpoint now answers with a proof-of-work
  anti-bot challenge, and NCBI retired `oa.fcgi` and the FTP dataset directories
  in August 2026. The AWS mirror is the current sanctioned no-login route.
- Several publishers (Wiley, Elsevier) refuse scripted downloads of their *own*
  OA PDFs. When that happens the stub records the working URL and says the host
  refused — it does not claim the paper is closed access.

## Scope limit

PubMed indexes biomedical and life-sciences literature. A `not_found` for a CS,
physics, or humanities paper may just mean it is out of scope — not that it is fake.
The tool says so in its notes rather than declaring such a citation invalid.

## Examples

```bash
python tools/pubmed.py search "CRISPR off-target" --limit 10 --from 2022
python tools/pubmed.py verify --title "Deep learning" --year 2015 --author "LeCun Y"
python tools/pubmed.py verify --doi 10.1038/nature14539
python tools/pubmed.py check-refs manuscript/references.bib --fail-on-problem
python tools/pubmed.py cite 26017442 --style bibtex
python tools/pubmed.py sections 32142651 --sections discussion,conclusions
python tools/pubmed.py pdf 32142651 33301246 --out project/papers/
```

---

# scholar.py

The same job as `pubmed.py verify`, across four indexes instead of one. **Use this
to verify anything**; use `pubmed.py` for PubMed-only discovery, full-text
`sections`, and OA `pdf` retrieval, which only it can do.

**Why it exists.** PubMed is a biomedical index. Measured on five queries drawn
from real surface-science work, it returned **zero hits on three of five** —
*Surface Science*, *Applied Surface Science*, *J. Vac. Sci. Technol.* and
*Vacuum* are not indexed there at all. For those papers `pubmed.py verify`
returns `not_found` because they are out of scope, not because they are fake, so
the rule "every citation goes through verify" could not be satisfied for half of
a chemistry bibliography. `scholar.py` closes that.

**No key, no account, no email.** Every source here is keyless.

## Sources

| Source | Role |
|---|---|
| **PubMed** | asked first; still authoritative for biomedical work. `pubmed.py`, unchanged |
| **Crossref** | DOI metadata, retraction flags, and the journals PubMed does not index |
| **OpenAlex** | discovery, citation graph (`related`, `cited-by`), OA status |
| **Europe PMC** | biomedical superset of PubMed, plus preprints |
| **PubChem** | substance data for `compound` |

## Commands

| Command | What it does |
|---|---|
| `search "<query>"` | Query several indexes and merge. `--limit`, `--from/--to`, `--source` |
| `verify` | Check one citation is real, in any index. `--title`, `--doi`, `--pmid`, `--author`, `--year` |
| `check-refs <path>` | Verify a whole reference list. `.json`, `.bib`, or one title per line. Classifies first, so a record that is not a paper never reaches the cascade. Compares the byline as an ordered LIST — a cited surname the record does not carry and a wrong first author are flags, an abbreviated byline and a year one out are notes — and counts both in `summary.byline_differences` and `summary.year_notes`, where an entry carrying only notes is no longer `clean` |
| `corpus-fill <project>` | One `data/corpus/papers/<citekey>.md` per bibliography entry that has none, in the format `review.py` writes, at the tier retrieval actually reached. Gives the sentence-level attribution pass something to read on a folder this engine never scaffolded. **Refuses on a project that carries a corpus protocol** — a systematic review's tiers are a claim about a screening procedure |
| `source-types <path>` | What KIND each reference is — offline, no network, no index |
| `disputed <DOI\|PMID>` | Later papers whose titles suggest they disagree. Signals, never verdicts |
| `cite <DOI\|PMID...>` | Format as `--style ama` (default), `apa`, `bibtex`, `acs` |
| `related <DOI\|id>` | Papers OpenAlex considers related |
| `cited-by <DOI>` | Papers citing this one, most-cited first |
| `compound "<name\|CAS\|SMILES>"` | Formula, MW, SMILES, InChIKey, synonyms, registry numbers |
| `status` | Which sources answer right now |

`--source` takes `auto` (the default cascade), `all`, one source by name, or
several comma-joined — `--source crossref,openalex` asks exactly those two. An
unknown name is refused **by name**, with the vocabulary, rather than as a
usage block.
`--json` works on either side of the subcommand, as with `pubmed.py`.

## The cascade

`verify` asks **PubMed → Crossref → OpenAlex → Europe PMC** and stops at the
first source that verifies. Within each source it resolves by the strongest
identifier available — **PMID → DOI → title** — because a DOI is an identifier
and a title is a guess. The result carries `source` (which index answered) and
`sources_tried` (which stayed silent), so a `not_found` says how hard it looked.

A `partial` never stops the cascade — a later index may confirm the paper
outright.

**The output is a superset of `pubmed.py verify --json`**: same keys, same
`status` values, plus `source`, `sources_tried` and `unreachable`. Anything
reading the old keys keeps working.

## What kind of source it is — the third axis

`status` answers *does it exist*; flags answer *should you cite it*. Neither
can answer *is this a paper at all*, and a reference list is full of things
that are real, citable and not papers.

Measured, and this is why it exists: a UniProt entry in a real bibliography
went through the cascade, missed in four indexes **because it is not a paper**,
and came back `not_found` — the word this engine reserves for invented
citations.

So `check-refs` classifies every entry first, offline, from the `.bib` fields.
A record whose class is not a paper **is never sent to an index**, and comes
back as `status: "not_a_paper"` with a `source_class`:

```jsonc
{"class": "database", "peer_reviewed": false,
 "why": "UniProt - a sequence and annotation database - a database record, "
        "not a peer-reviewed paper",
 "evidence": "url", "verify": false}
```

Classes: `journal-article`, `preprint`, `database`, `book`, `chapter`,
`thesis`, `software`, `dataset`, `web`, `unknown`.

**`peer_reviewed` has three values and the third one matters.** `true`, `false`
and **`null`** — *the entry does not say*. A bare `@misc` with a title and
nothing else is not evidence that something was not peer reviewed.

**Nothing here refuses a citation.** A methods section citing a PDB entry is
required to; a review citing a UniProt accession is doing something normal.
The engine says what a reference is and the user decides.

Two guards worth knowing, because both were measured rather than assumed. A
database name in a **title** is only read when the entry has no journal, no DOI
and no PMID — otherwise *The UniProt Knowledgebase in 2023*, a real *Nucleic
Acids Research* paper, would be classified as the database it is about. And a
host this list has never heard of degrades to `web`, never to `journal-article`.

## `disputed` — has a later paper disagreed?

A **retraction** is the publisher withdrawing a paper, and the cascade has
always caught it. A **contradiction** is a later paper disagreeing, and no
index has a field for it. Scite classifies exactly this and needs a key, which
the keyless-only decision rules out.

What is reachable is the citation graph plus the citing papers' titles:

```
python scholar.py disputed 10.1126/science.1197258 --limit 60
```

Titles are matched against a **tiered** phrase list — `asserted` for a title
that exists to argue (*Comment on …*, *Reply to …*, *Failure to replicate …*),
`contextual` for one that might (*revisited*, *re-evaluation*). An anchored
phrase must lead the title **and** be followed by something that names a paper
or an author: measured on the first live run, *Response to Arsenic in
Rhodococcus aetherivorans BCP1* is a bacterium responding to arsenic, not a
published Response, and it is `contextual`.

**`signals: []` means nothing in the titles that were read said so.** It is not
a clean bill of health, the payload says so in words, and it ships
`"calibrated": false` until the tiers are measured against a known corpus.
Exit code is 0 whether or not there are hits: this command finds reading, not
faults.

## What it will not do

- **It will not use Crossref's relevance `score` as evidence.** Crossref returns
  a top hit for *any* string, and measured against real and fabricated titles the
  score ranges overlap — fabrications outscored a genuine paper. A score-based
  gate would attach invented citations to real but unrelated DOIs and call them
  `verified`. Title similarity is the only gate.
- **It will not adopt a match it is not sure of.** Below the verified threshold
  the status is `partial`, `match` stays `null`, and **no DOI is emitted** — a
  candidate is shown, not adopted. A confidently wrong identifier is worse than
  a miss, because a miss sends you to look and a wrong DOI does not.
- **It will not report a source failure as a verdict.** A source that does not
  answer is named in `unreachable[]` and the result says the search was
  incomplete. A source being down must never look like a paper being fake.
- **It will not invent a PMID.** Non-PubMed records carry `pmid: ""`, and the
  BibTeX writer omits the field rather than emitting an empty one.
- **It will not guess a journal abbreviation.** Crossref's
  `short-container-title` is *not* an ISO abbreviation — for
  `10.1016/j.susc.2005.01.038` it returns the full title "Surface Science" — so
  it is ignored, and the abbreviation is resolved from the **NLM Catalog by
  ISSN**, the same source PubMed's own comes from (`0039-6028` → `Surf Sci`).
  A journal the catalog does not list keeps `""` and reports that it needs a
  hand edit. This is why the same DOI cites identically through Crossref and
  through PubMed.
- **It will not pick a CAS number for you.** PubChem returns several registry
  numbers unranked and mixes in EC/EINECS numbers; `compound` returns the whole
  list. `cas_check_digit_ok` is arithmetic on the number itself, not a claim
  about which one to cite.
- **It is not SciFinder.** No free source indexes CAS reaction data. That is
  unavailable, not deferred.

## Known degradation, stated rather than hidden

- Crossref records often have **no abstract** (`""`), so nothing downstream may
  claim to have read one.
- OpenAlex supplies display names, not `Surname Initials`, so initials are
  re-derived and can differ from PubMed's (`Chen Y` vs `Chen YL`). Journal, year,
  volume, issue, pages and DOI agree; the author string is the part that drifts.
- `is_retracted: false` means "no source said so", not "not retracted".
- No full text and no PDFs outside PMC — use `pubmed.py sections` and
  `pubmed.py pdf`.

```bash
python tools/scholar.py status
python tools/scholar.py verify --title "The adsorption and incorporation of oxygen on Cu(100)"
python tools/scholar.py verify --doi 10.1016/j.susc.2005.01.038
python tools/scholar.py check-refs drafts/references.bib --fail-on-problem
python tools/scholar.py cite 10.1016/j.susc.2005.01.038 --style bibtex
python tools/scholar.py cited-by 10.1016/j.susc.2005.01.038 --limit 5
python tools/scholar.py compound "trimesic acid"
```

Re-measure: `python tests/scholar.py` (needs network).

---

# scaffold.py

Creates the universal scientific-paper project structure. Every project under
`Research/<PI> - <Field>/Projects/` gets the same shape, so every skill can
assume where things live without checking: `plan/captions.md` is always the
caption source of truth, `data/analysis/analysis.md` always holds the
p-values, `plan/outline.md` is always one line per paragraph.

Pure standard library — no dependencies, and it runs without Claude.

Spec: `specs/setup-project-directory.md`. Caller: `skills/setup-project-directory/SKILL.md`.

## Commands

| Command | What it does |
|---|---|
| `scaffold <path>` | Create every missing piece. `--title`, `--short-name`, `--field`, `--journal`, `--pi`, `--one-liner`, `--dry-run`, `--only <subtree>`, `--round <n>`, `--paper-kind`, `--expects-data` |

**`--only <subtree>`** restricts the run to one top-level subtree of the manifest — `drafts`, `plan`, `data`, `obsolete`, `.claude`, `.vscode`. It exists for `manuscript.py init`, which needs `drafts/source_text_rN/` on a manuscript-only project and must *not* create an empty `plan/` and `data/` alongside it. The choices come from `FILES` and `EMPTY_DIRS` rather than a second hand-written list, and the match is on the path segment, never a prefix — `--only data` must not sweep in a future `data_bench/`.

**`--round <n>`** labels the source text folder with the round it holds: a fresh project gets `drafts/source_text_r1/`, and `manuscript.py init` on a project sitting at r8 passes `--round 8`. Default is `project.yml:revision`, then 1. A project that already has a source_text folder keeps it whatever the flag says — writing `source_text_r1/methods.md` into a project at r8 would create the second folder the label exists to make visible rather than to cause.

**`--expects-data`** is what the project will HOLD, which is neither what it IS (`--paper-kind`) nor what it is ABOUT (`--field`). `measurements` is rows in a table and is the default. `images` keeps `data/raw_images/` and the analysis pipeline — an imaging project still has numbers — and drops `data/mock_data/`, because a synthetic micrograph is not a thing this engine can honestly make; `image-floats` is the offer that replaces mock data there. `none` is a perspective or theory paper and gets no `data/` at all. Declared, never sniffed: an empty `data/` and "this project will never have data" look identical on disk, and one of them is a scaffold nobody has filled in. The scaffold result carries an `offers` block so a caller reads the policy rather than re-deriving it, and `check` reports a declared absence as `out_of_scope` rather than as a gap.

| `check <path>` | What is present, what is missing, and whether anything has been rendered. Exit 1 if incomplete |
| `adopt <path>` | `scaffold`, then a proposal for every top-level entry the structure does not explain. `--apply` moves the certain ones; everything else is a reported question. `--depth N` classifies inside folders no rule matched, and nothing found below the top level is ever `clear`. `--sweep-unknown`, with `--apply`, moves the entries NO rule matched and that propose nothing into `sandbox/unsorted/`, with a manifest saying where each came from — never a manuscript-shaped document, whatever its tier. Same metadata flags as `scaffold` |
| `float-order <path>` | Every file whose NAME claims to be a float, the version groups where two files claim one slot, the gaps, and where filename order disagrees with the prose. **Proposes only** — it creates no folder, writes no caption and renames nothing, and with no prose it says the order is a guess |
| `report <path>` | What this folder can and cannot now do: structure, sorted, float order, AI disclosure, cover letter, citations. **Every row says what it did or why it could not, and never nothing** |
| `survey <path>` | Every top-level entry the tree does not explain, and what it could feed — `drafting`, `citations`, `data`, `floats`, `analysis`. `--use X --as ROLE` / `--ignore X` records the answer in `project.yml:extra_sources` so it is asked once. `--all`, `--note`, `--dry-run` |
| `tree <path>` | The annotated project tree |
| `github <path> [--owner ORG] [--repo NAME] [--url URL]` | Make the project a git repository with a private GitHub home and turn on its sync (`.claude/hooks/sync.sh`, run by the session hooks). Without `--repo` a repository that would be created is only **proposed**, so the user confirms its name first. Safe to re-run; also how a project scaffolded before the sync gets it |
| `float list <path>` | Every float folder, in manuscript order, with the scripts in it |
| `float new <path>` | Add the next float folder. `--figure` / `--table`, `--slug`, `--supplementary`, `--art plotted\|drawn\|mixed` |
| `float renumber <path>` | Move a float. `--from N --to M`, `--figure` / `--table`, `--dry-run` |
| `float migrate <path>` | Move a flat-layout project's loose scripts into folders |
| `float lint <path>` | What a float script gets wrong that a static read can catch, including a float marked **drawn** that holds no PowerPoint source. `--float "Figure 2"`. **Always exits 0** |
| `mock-floats <path> --bundle <json>` | Fill the pre-made float slots with runnable scripts over `data/mock_data/`. `--force`, `--dry-run` |
| `image-floats <path> --bundle <json>` | Fill figure slots with composed light-grey panels, each carrying a sentence saying what image is expected in it. **Not mock** — nothing is fabricated, so there is no watermark and the co-author `.docx` builds it. `--force`, `--dry-run` |
| `prefill <path>` | Write commented facility defaults from `instruments.md` (and the pack's `software.md`) into `data/methods_facts.yml`. `--instrument "..."` and `--software "..."` (both repeatable), `--resources`, `--dry-run`. A software block carries no version, on purpose |
| `harvest <path>` | Read the acquisition settings the instrument wrote into the project's own image files and record them as **fact**. `--under "<subfolder>"`, `--dry-run` |

`--json` works on either side of the subcommand, as with `pubmed.py`.

**`harvest` and `prefill` are siblings and they differ in exactly one thing:
who recorded the value.** A prefill default is the facility's typical number
for an instrument somebody else may have used differently, so it is written
**commented out** and counts as missing until a human deletes the `#`. A
harvested value is what *this* instrument wrote about *these* exposures — a
Thermo/FEI microscope puts an ini block naming the instrument, the detector,
the voltage, the dwell time, the field width and the pixel size into every
`.tif` it saves — so it is written **uncommented**, and a value in it must
never become a `**[FLAG: author]**`. Measured on one real project: 158 of 158
image files carried a block, and the round nevertheless flagged three of those
values for an author to supply, one of which had two answers because two
microscopes were used (item 46).

Three rules the command turns on:

- **A key whose files disagree is written as the disagreement, never as the
  majority.** A methods section naming one microscope on a project that used
  two is false, and the majority is the most confident way to be false.
- **A value measured afresh per exposure is a range, not a list.** A few
  hundred files legitimately carry dozens of distinct working distances; the
  block records low–high and the
  median, and the exact per-file numbers live in
  `data/instrument_metadata.json`, which is also the provenance for every
  count in it.
- **A value a person recorded outranks anything derived.** The harvest block
  is regenerated on every run, so a correction goes anywhere else in the file
  — where the already-recorded test finds it and leaves it alone. The block's
  own header says so.

**`--figures N` / `--tables N`** on `scaffold` decide how many float slots are
pre-created (4 and 2). They are slots, not a contract: `check` does not report a
deleted `Fig03` as a defect, because how many floats a paper has is the paper's
business.

## One float, one folder

Figure 1 is `plan/figures/Fig01/`, and the script, the `.png`, the `.pdf` and
any PowerPoint panel art live inside it. The script is `figure.R` — **the folder
name carries the number**, so renumbering a figure is renaming its folder and
nothing inside it moves.

The grammar is tolerant: `Fig01`, `Fig1`, `Fig 1`, `Fig_1`, `Figure01` and
`Fig01_yield_by_catalyst` are all Figure 1; `FigS01` is Figure S1. The trailing
slug is for whoever reads the file tree, and the **number is the identity** — a
caption block resolves to a folder by number, so adding a slug needs no edit
anywhere else.

**`mock-floats` is the day-one preview.** A fresh scaffold renders clean and
produces nothing, so nothing proves the pipeline works and nothing shows the
house style. This fills the slots from `data/mock_data/` — Figure 1 as a
two-panel composite so the panel system is visible, Figures 2-4 one outcome
each, Table 1 the summary, Table 2 the column inventory — and appends a
claim-first caption block per float. It is safe because every script reads
through `load_data()` from `data/mock_data/`: `save_float()` watermarks the
output and `create_floats.R` refuses the co-author `.docx`. **Only slots still
holding the unbuilt template are filled**, with or without `--force`, so a
script anyone has worked on is never touched.

**The generator itself belongs to `idea.py`, not here.** `idea.py mock` is the
standalone door into the same generator `idea.py write` produces from a full
idea bundle, because the rows are wanted on day one and the hypothesis is not
settled yet. One engine, two callers: the `_mock` suffix contract is what arms
every safeguard downstream, and a second copy of it would be right until the
once it was not.

**`float renumber` does both halves or neither.** It moves the folders *and*
rewrites `plan/captions.md`, including putting the caption blocks back in
numeric order — document order in that file *is* manuscript order, so headings
numbered 1, 2, 3 sitting in the order 2, 3, 1 build a co-author document with
the figures wrong and every caption right. Moving 3 to 1 walks the others down
rather than landing on an occupied number, and a move plus its inverse restores
the file byte for byte.

**A loose `.R` directly in `plan/figures/` is no longer run.** It is reported by
`render_all.R` and by `check`, because a project carried over from the old flat
layout would otherwise render nothing and still report success. `float migrate`
converts one: it moves each `figNN_slug.R` into `FigNN_slug/figure.R`, brings
the rendered outputs with it, and names anything whose number it could not read
rather than guessing.

`--field` is `chemistry` \| `biochemistry` \| `other` and selects the
`data/methods_facts.yml` template — a cryo-EM block or an instrumentation block.
`--journal` selects the figure geometry preset in `plan/theme/theme_journal.R`
(ACS / Wiley / Nature, `generic` otherwise).

## It never overwrites

The single guarantee everything else rests on. Running `scaffold` against a
folder that already has work in it creates only what is missing and reports
every path it skipped; a re-run on a finished project is a no-op. That is what
makes it safe to run when you are not sure whether a project was scaffolded,
and what lets `check` be used as a repair tool.

**`project.yml` is never rewritten, so what it records wins over a contradicting
flag.** Passing `--field other` to a project whose `project.yml` says
`chemistry` keeps `chemistry` and reports the ignored flag in `ignored_flags`
rather than leaving the run and the file disagreeing. To change a recorded
value, edit `project.yml`.

## The manifest is the contract

`FILES` and `EMPTY_DIRS` in `scaffold.py` are an explicit list, not a walk of
the template directory, because the destination layout is what every later skill
reads. `tests/scaffold.py` asserts the two agree in both directions — no
manifest entry without a template, and no template file unreachable from the
manifest.

Templates live in `tools/project_template/`. Text templates (`.md`, `.yml`,
`.bib`) carry `{{placeholder}}` values; the `.R` sources deliberately do not —
they read `project.yml` at run time, so they stay valid R and can be linted in
place.

## The R float pipeline it writes

`plan/render_all.R` discovers every `.R` directly inside `plan/figures/` and
`plan/tables/`, runs each in its own environment, then builds
`plan/preview.html` and `plan/floats/figures_and_tables.docx`. Files starting
with `_` are skipped (which is what keeps `_template.R` out of the run), as are
OneDrive conflict copies (`...-DESKTOP-A4B6PBU.R`).

Three properties are enforced by the render layer rather than by convention,
and all three are measured by `tests/scaffold.py`:

- **One call writes both formats.** `save_float()` emits `.png` at the journal
  dpi and `.pdf` via `cairo_pdf` at the journal column width. `save_table()`
  emits `.html` for the preview and `.rds` — the flextable object that becomes a
  **native, editable Word table** in the `.docx`, so nothing is converted on the
  way to a co-author and no formatting can break in transit.
- **Mock data cannot reach the hand-off.** `load_data()` records whether each
  read came from `data/raw/` or `data/mock_data/`; `save_float()` watermarks
  anything downstream of mock input and `generate_preview.R` banners it; and
  `create_floats.R` **refuses** to build the co-author `.docx` at all. Override
  deliberately with `FORCE_MOCK=1`, which stamps the warning inside the document.
- **Colour is never the only signal.** `save_float()` simulates deuteranopia and
  grayscale in memory and warns when two series become indistinguishable. The
  saved files stay full colour — the fix is a redundant shape or linetype
  aesthetic, never desaturation.

`plan/setup.R` also writes `data/analysis/provenance.json` on every run — R
version, package versions, and the seed — so the software half of the methods
section is recorded without anyone typing it.

**R is often installed but not on `PATH` on Windows.** Measured here: R 4.6.0 at
`C:\Program Files\R\R-4.6.0\bin\Rscript.exe`, which `which Rscript` does not
find. Look under `C:\Program Files\R\*\bin\` before concluding R is missing.

## Examples

```bash
python tools/scaffold.py scaffold "Projects/thermophile_arrays" \
    --title "Chemoreceptor arrays above 60 C" --field biochemistry --journal Nature
python tools/scaffold.py scaffold "Projects/existing_work" --dry-run
python tools/scaffold.py check "Projects/thermophile_arrays" --json
python tools/scaffold.py tree "Projects/thermophile_arrays"
python tools/scaffold.py adopt "Projects/existing_work"
python tools/scaffold.py survey "Projects/thermophile_arrays" --json
python tests/scaffold.py                     # 504 checks, incl. the R pipeline
```


---

# idea.py

The deterministic half of `idea-generation`: find the project directory the user
meant, report what the project already records so nothing is re-asked, and turn
one settled-idea bundle into files without ever destroying work already there.

The judgment half — what the gap is, which candidates survive, how the paper is
framed — is `skills/idea-generation/SKILL.md`. Pure standard library.

Spec: `specs/idea-generation.md`. Worked example bundle: `tests/idea_bundle.json`.

## Commands

| Command | What it does |
|---|---|
| `resolve <path>` | The as-typed path → the project that was meant. Exit 0 only when it resolves to a scaffolded project |
| `context <path>` | `project.yml`, which sections are already written, existing float blocks, and an inventory of the lab's `Resources/` |
| `write <path> --bundle <json>` | Every artifact a settled idea produces. `--mode explore`, `--dry-run`, `--no-mock-run` |
| `mock <path> --bundle <json>` | The mock-data generator alone, written and run. `--no-run`, `--force`, `--dry-run` |

`--json` works on either side of the subcommand.

### `resources` and `merge`

| Command | What it does |
|---|---|
| `resources <any path>` | The lab's `Resources/` folder — files, subfolders, and the first heading of every text document. **No document body**, so it is cheap enough to call before the first query |
| `merge <project> --bundle <json>` | Additively merge a settled idea into a project that already has work in it. `--apply`, `--cap-ack`, `--check-refs` |

**`resources` resolves from anywhere** — the lab folder, a project inside it, or
the `Resources/` folder itself — because Stage 2 runs *before* a project folder
exists. Resolution is by what is on disk rather than by a name pattern, for the
same reason `find_lab_folder` is: a lab folder named differently must not become
an unsupported case. An absent `Resources/` is reported, never an error.

Those names are the point: the SOPs name this lab's actual instruments,
techniques and materials, and **the names are the search vocabulary.** A lab
whose procedures folder is full of QCM and TPD should not have its first gap
sweep run on the words the user happened to type.

**`merge` is a dry run unless you pass `--apply`**, because a merge must never
be a surprise. Every action is `add`, `append`, `new`, `propose` or `skip` —
**there is no `modify`, and the writer raises rather than committing one**, so
"nothing is overwritten" is a property of the code and not a sentence in the
preview.

Two things stop it and ask, and neither writes anything:

- **The project is being drafted.** A `manuscript_rN.docx` under `drafts/`
  restricts the merge to the two purely additive operations — the guiding papers
  and the README takeaway — and everything else goes into one dated
  `plan/idea_proposal_<date>.md`. An idea-stage merge that rewrote a drafted
  paper's outline is destructive in a way no preview makes safe.
- **The guiding-paper cap.** Past eight the merge is **blocked**, not warned:
  show the combined list, ask which to keep, re-run with `--cap-ack`. Nothing is
  dropped silently and nothing exceeds the cap silently.

A paper already in the project is matched on DOI, then PMID, then normalized
title — using `pubmed.py`'s own matcher, because writing a third title matcher
is how three matchers disagree. When `pubmed.py` cannot be imported (it needs
`requests`) the dedup falls back to identifiers only **and says so**, rather
than growing a local title comparison.

An empty `plan/outline.md` is filled in place, banner and all; one that already
has lines gets the proposal beside it. That is not fussiness: `prose.py` reads
the proposed-outline banner **file-wide**, so appending a proposal to a settled
outline would declare the whole approved paragraph plan unapproved, and every
verdict computed against it inherits that.

## resolve

Four outcomes, none of them an error: `scaffolded` (use it), `close_match` (a
typo, a case difference, or the `Projects/` parent — confirm the candidate),
`unscaffolded` (offer `setup-project-directory` first), `missing` (offer to
create it). Candidates are ranked with scaffolded directories ahead of merely
similar names, because a scaffolded near-miss is almost always the answer.

## context

The command that stops the skill asking for things already written down. It
answers "which sections of `plan/README.md` say something yet" **by comparing
against the pristine template**, not by a heuristic — the scaffolded
`## Study design` is a table of empty cells with row labels, and any rule loose
enough to call that empty also calls a real half-filled table empty.

It also finds the `<PI> - <Field>/` folder by its `Resources/` or `Ideas/`
marker rather than by a name pattern, and lists what is in `Resources/` — SOPs,
training notes, safety data sheets. Names and paths only; no file contents, so
it stays cheap to call.

## write

One bundle in, every artifact out, in one pass: `project.yml`,
`plan/README.md`, `plan/outline.md`, `plan/captions.md`, `plan/ideas.md`,
`data/data_contract.md`, `data/templates/*.csv`,
`data/mock_data/generate_mock_data.py` (then run, producing `*_mock.csv`), and
the literature summary. `--mode explore` writes only the record and the
summary, with no project tree.

### What it refuses

Refusals are for things that would put something wrong into the project, and
they are checked before anything is written:

- **A PMID cited in prose that is not in the retrieved literature set.** This is
  §4 of the spec made structural: a citation from memory cannot reach a file.
- **Literature with no `status`, or a status other than `verified` / `partial`.**
  Carrying `pubmed.py verify`'s verdict through the bundle is what makes "every
  PMID was verified" checkable rather than a promise.
- **A float whose caption heading would not parse** under `read_captions.R` —
  a space in the filename, a malformed label. An unparseable block is invisible
  to the preview, the Word floats, and the manuscript, and nothing else would
  report it.
- **A float with no claim**, two floats writing the same file, an unrecognised
  column `kind`, or a `group_column` that is not a column.

The spec's caps — 3–8 papers, 3–6 floats, 3–5 candidates — **warn** rather than
refuse. They are design features but they are judgment calls.

### What it will not overwrite

- **A section someone has written in** keeps every word and gains a dated block
  underneath. A section still identical to what the scaffold wrote is filled in
  place.
- **`project.yml` values already recorded** win over the bundle, and the
  contradiction is reported — the same rule `scaffold.py` uses.
- **An existing `generate_mock_data.py`** is left alone. Once written it is the
  user's to edit.
- **Caption blocks are not duplicated** on a re-run.

### Mock data

The generated `generate_mock_data.py` is stdlib-only and carries the hypothesis
in its header, with the effect sizes as editable constants. The rows encode what
we expect to see **if the hypothesis holds** — including whichever measure is
supposed to show *no* effect, which is what makes the dataset a real test of the
figure rather than a demonstration. A column the hypothesis says nothing about
is written blank and reported, never invented.

Everything it writes lands in `data/mock_data/` with a `_mock` suffix, which
arms the safeguards in the render layer: `save_float()` watermarks, the preview
banners, and `create_floats.R` refuses the co-author `.docx`.

## Examples

```bash
python tools/idea.py resolve "Projects/Array Symmetry" --json
python tools/idea.py context "Projects/array_symmetry"
python tools/idea.py write "Projects/array_symmetry" --bundle idea.json --dry-run
python tools/idea.py write "Projects/array_symmetry" --bundle idea.json
python tests/idea.py                         # 64 checks, incl. the R pipeline
```

---

---

# structure.py

Keyless structural-biology engine: AlphaFold DB, UniProt, RCSB PDB, PDBe and
EMDB behind one normalized record and one CLI. No API key, no email, no OAuth.

**Why a second engine rather than a sixth source in `scholar.py`.**
`scholar.py` is a *literature* engine: its record is a paper, and its cascade
answers "does this citation resolve to a real published work?" A protein
structure is not a paper and does not answer that question, so bolting AlphaFold
into the verify cascade would put a source that can never verify a reference
inside a loop whose whole job is verifying references. Same architecture,
second application. Spec: `specs/external-services.md` §§3–4.

**There is deliberately no skill.** "Check AlphaFold for this protein" is one
command, reachable from a bare ask, from `idea-generation` Stage 5, or from
methods drafting, with no project directory required.

## Commands

| Command | What it does |
|---|---|
| `alphafold --uniprot P00918` | Predicted structures for one accession. `--download <dir>`, `--format cif\|pdb\|bcif` |
| `alphafold --name "carbonic anhydrase 2"` | Resolves the name and **stops at the candidate list**. `--organism 9606` |
| `resolve --name "..."` | Name → UniProt accession candidates. `--organism`, `--limit` |
| `pdb --id 1CBS` | One experimental entry, from RCSB, degrading to PDBe |
| `pdb --search "HKUST-1"` | Full-text search over the PDB. `--limit` |
| `emdb --id EMD-3061` | One cryo-EM map entry |
| `status` | Which sources answer right now, and what each is for |

`--json` on either side of the subcommand, as everywhere else.

## What structure data may be used for

**It is evidence for feasibility and for methods. It is never evidence for a
gap.** "No PDB entry exists, therefore this is unstudied" is the
PubMed-found-nothing fallacy with a different index — and measured, a full-text
search for HKUST-1, a MOF with a large literature, returns `total_count: 1`.
Every empty or thin result carries that caution in `notes`.

Legitimate: a feasibility answer ("a 1.8 Å crystal structure exists, so the
purification is known to work"), a methods proposal citing a deposited
structure's conditions, and the honest report that AlphaFold has no model for a
target — which is itself a finding.

## Four things that were measured, and are encoded rather than assumed

**The User-Agent is load-bearing.** AlphaFold answers `User-Agent: probe` with
`403 Forbidden`, reproducibly, and a descriptive one with `200` and the same
14,887 bytes. It is deterministic UA filtering — **not** a rate limit and
**not** a key requirement. This is the `Cu(111)` trap in a new costume: a
failure that reads as "the service needs credentials" when it means "send a real
User-Agent". So the header is one module constant, sent on every request, and
`tests/structure.py` asserts it rather than trusting it. **Do not add an API key
in response to a 403 here; there is none to add.**

**`404` and `400` are two different answers and must never collapse.**

| Input | Result | Means |
|---|---|---|
| `P00918` | `200`, a list | a model exists |
| `Q8WZ42` (titin, 34,350 aa) | `404`, body `{}` | well-formed, **no model** — a real coverage gap, and reportable as one |
| `NOTREAL9`, or `CAH2_HUMAN` | `400` + an error message | the **wrong kind of identifier** |

`CAH2_HUMAN` is the overwhelmingly likely mistake, because it is what people
actually type: a UniProt *ID* rather than an *accession*. The engine says so and
points at `resolve`.

**Name resolution never picks.** Searching human carbonic anhydrase 2 returns
`[P23280, P00918, P35219]` and **the correct accession is second**. Taking the
top hit would fetch the wrong protein's structure and report it confidently.
`resolve` returns candidates and a `chosen` of `None`, always. Latency supports
asking anyway: UniProt search was the slowest call measured here at 1.73 s,
about 8× AlphaFold's own.

**Several entries can mean two opposite things, and only the entry id says
which.** `P00520` returns four, and the spec recorded them as four fragments of
one sequence. They are four *isoforms*:

```
AF-P00520-F1    1-1123      AF-P00520-3-F1  1-1118
AF-P00520-4-F1  1-1142      AF-P00520-2-F1  1-1117
```

— every one `F1`, and every one whole. AlphaFold *does* fragment very long
sequences, as `F1`/`F2`/…, and that case needs the opposite handling: several
fragments must never be reported as a whole protein, while several isoforms must
never be silently collapsed to the first one. `parse_entry_id` reads the id
rather than counting entries, and each record carries `isoform`,
`fragment_index`, `covers` and `is_whole_sequence`.

## `confidence` is never a bare number

pLDDT is the model's own **per-residue** confidence on a 0–100 scale.
`fractionPlddtVeryHigh: 0.985` means 98.5% of residues are modelled at very high
confidence; it is **not** a measured accuracy and not a claim that the structure
is correct. The statement travels with the numbers in the record, and every
human line that prints a pLDDT prints what pLDDT is — because a bare "97.4" in a
terminal becomes "97% accurate" in a methods section, and that sentence is wrong
in print. A predicted structure is always labelled predicted.

## Degradation

A source that did not answer is never reported as a record that is not there.
`pdb --id` falls back from RCSB to PDBe and says so, and the fields PDBe does
not carry are left unset rather than filled in from somewhere else. Every
command still exits 0.

Re-measure: `python tests/structure.py` — offline, with real captured payloads
replayed by a stub HTTP server, including the `403`, the `404` and the `400`
bodies the live services actually returned.

# docx_edits.py

Reads tracked changes and comments out of a Word `.docx`. The ingest half of
`writing-engine`: a coauthor return or a reviewer's marked-up file goes in,
and every insertion, deletion, and comment comes out with its author, its date,
and the sentence it sits in.

The judgment half — whether an edit should be applied, whether two authors
collided, what a comment is really asking for — is `writing-engine`'s
`ingest-edits` module. This engine only reports what is in the file.

**It never writes a `.docx`.** Word files are outputs of the pipeline and inputs
only from `edits/`. Requires `lxml`.

Spec: `specs/writing-engine.md` §11. Fixtures: `tests/fixtures/`, built by
`tests/make_docx_fixtures.py`.

## Commands

| Command | What it does |
|---|---|
| `extract <file.docx>` | Insertions, deletions, comments, authors, field codes, notes |
| `plain <file.docx> [--accept\|--reject]` | The document text with all changes taken or all undone |

`--json` works on either side of the subcommand.

## extract

```json
{
  "file": "manuscript_r5_JV.docx",
  "authors": ["G Jensen", "J Vale"],
  "insertions": [{"author": "...", "date": "...", "context": "...", "text": "...",
                  "paragraph": 1, "moved": false, "nested": false}],
  "deletions":  [...],
  "comments":   [{"id": "0", "author": "...", "initials": "GJ", "date": "...",
                  "anchor": "...", "text": "...", "resolved": false, "paragraph": 2}],
  "formatting_changes": 1,
  "field_codes_present": true,
  "citation_managers": ["EndNote"],
  "notes": ["contains EndNote field codes; citations read as plain text"]
}
```

**`context` is the sentence in the *original* text**, not the edited one. That is
deliberate: context exists to match an edit back to a paragraph in
`source_text/*.md`, and the markdown still says what we sent out. Character
offsets do not survive the `.docx` round trip, so the sentence is the anchor. A
wholly new paragraph has no original text to sit in and falls back to the
paragraph as the author left it.

Adjacent runs of one revision are merged. Word splits a typed phrase across many
runs — a spell-check pass is enough to do it — and an unmerged list fills the
ledger with fragments.

## plain

`--accept` (the default) is the document as the editor wants it. `--reject` is
what we sent them, which is the view that lines up with `source_text/`.

A run inside `<w:ins><w:del>` — typed by one author and cut by another — is in
**neither** view: accepting all changes drops it, and rejecting all changes drops
it too. `extract` reports it as a deletion with `"nested": true`, and says so in
the notes, because a run that vanishes from both views would otherwise vanish
silently.

## What it reports that a naive parser misses

- **Moves.** Word emits `<w:moveFrom>`/`<w:moveTo>` for dragged text, not a
  delete plus an insert. A parser that only knows `w:ins` and `w:del` loses a
  moved paragraph entirely. Both halves are reported with `"moved": true`.
- **Formatting-only revisions** (`<w:rPrChange>` and friends) are counted, not
  listed — they change no text, and putting them in the ledger as edits would
  bury the ones that matter.
- **Reference-manager field codes.** Detected and named (EndNote, Zotero,
  Mendeley, Citavi), never rewritten — see §9 of the spec for why EndNote is
  never driven programmatically. Ordinary Word fields (PAGE, TOC) are ignored:
  they do not touch citations and flagging them would cry wolf. The field
  *instruction* never reaches the extracted text; the rendered *result* does.
- **OneDrive sync-conflict filenames** (`..._JV-DESKTOP-A4B6PBU.docx`) are called
  out in `notes`. The file is still parsed — the caller decides — but reading one
  alongside its original is reading a coauthor's markup twice.

## Examples

```bash
python tools/docx_edits.py extract "edits/manuscript_r5_JV.docx"
python tools/docx_edits.py --json extract "edits/manuscript_r5_JV.docx"
python tools/docx_edits.py plain "edits/manuscript_r5_JV.docx" --reject > baseline.txt
python tests/make_docx_fixtures.py           # rebuild the fixtures
python tests/docx_edits.py                   # 107 checks, offline
```

---

# manuscript.py

The state and the build behind `writing-engine`: where a journal folder goes,
what a round snapshot must contain, whether a citekey survived into the `.docx`,
and what state every external edit request is in.

Pure standard library except for the `.docx` ingest, which shells out to
`docx_edits.py` (lxml) and reads PDFs with `pypdf`. `assemble` needs **pandoc**.

Re-measure: `python tests/manuscript.py` (207 checks, drives real pandoc and
real R; both sections skip cleanly and say so when absent).

## Commands

| command | does |
|---|---|
| `init <project> --journal X` | create `drafts/{JOURNAL}/`, its config, the requirements skeleton, `reports/`, `submission/`, `plan/theme/journal_target.yml`, and the project's `drafts/edits/` inbox one level up. **Creates `drafts/` and `project.yml` if absent** (from `scaffold.py --only drafts`), so it works on a bare folder and on a manuscript-only project. `figures/` only when `figures.placement: separate`. Refuses a path inside another project, and a populated top-level `source_text/` |
| `status <project> [--journal X]` | journals targeted, round, which sections are stubs, **whether there is an outline and whether a run was interrupted**, floats and whether any is mock, whether the analysis has run, how many requirements are unknown |
| `outline <project>` | the claims ledger: its state, and an inventory of what one could be built from |
| `outline <project> --write B.json [--replace] [--dry-run]` | validate a proposed outline, diff it against the current one, and write `plan/outline.md` |
| `config <project> --journal X` | show the dials; record your own instructions with `--instruct`, `--set`, `--style-ref`, `--waive`, `--skip`, `--forget`; declare what the project holds with `--manages outline,floats,analysis` or `--manages none` |
| `plan <project> --journal X --preset P` | resolve a preset into a module list, with `--add` / `--drop`, the standing `skip_modules`, and `--sections a,b` / `--except a,b` / `--redraft s` to scope the round. `--stop-after <module>` ends it early; `--build` runs the submission half of a round the sections have narrowed. `--adherence 1-5` supplies the round's outline adherence; without it the standing preference is used and the round is labelled `(not asked)` |
| `retention <project> --journal X` | did the drafting pass keep what edit mode promised: every baseline sentence present or listed as Removed, numbers a subset, citekeys and flags additive only. `--restore` writes a rejected section back from its baseline verbatim with the flags appended |
| `flag-answers <project> --journal X` | record what a person decided about a flag (`--answer ID=TEXT`, ids from `prose.py flags --json`) into `edits_status.md` as `type: flag`, state `pending`; with no `--answer`, report the answers already recorded and any marked `applied` whose flag is still in the prose. It writes NO prose |
| `landscape <project> [--scaffold]` | the state of `plan/literature_landscape.md` - which of the three questions have verified PMIDs behind them - and the subject terms to search on |
| `run <project> --journal X ...` | the run ledger: `--start [--sections a,b | --except a,b] [--redraft s]`, `--step M --state S [--item I] [--wrote P]`, `--finish`, `--abandon`. The scope is sticky; `--sections all` clears it |
| `authors <project>` | the author list, the contribution matrix, the affiliations and the grants - and the byline, CRediT statement and funding statement they generate |
| `figure-files <project> [--journal X]` | every captioned float drawn and rendered, every drawn float captioned, and - where `figures.placement: separate` - every upload present, named as `figures.file_naming` asks and in a type `figures.file_format` accepts. Unsourced means unchecked, and the report says so (spec 7.3) |
| `completeness <project> [--journal X]` | what still stands between this and a finished paper |
| `strays <project>` | which document in this folder is the paper, and what every other one is. Reports only — it moves nothing. Skips `obsolete/` and `sandbox/`, and exits 1 when a manuscript-shaped document sits somewhere the structure does not explain |
| `round <project> --journal X` | open round N+1: snapshot the float set and the scripts into `obsolete/figures/rN/` and `obsolete/tables/rN/`, the source text into `obsolete/drafts/rN/source_text_rN/` (a folder of its own, named for the live folder as it stands), **move** the live round's `submission/`, `reports/rN/` and received edits in beside it with a hash manifest, and rename `source_text_rN/` to `source_text_rN+1/` |
| `retire-journal <project> --journal X [--to Y]` | move a spent journal folder whole into `obsolete/drafts/X/`, so `drafts/` holds one live journal. Refuses on an open run, on standing coauthor edits without `--to`, and on a `log.md` the hoist could not verify; hashes every file before the move and re-hashes after it. `--dry-run` prints the action list and writes nothing |
| `reference-doc <project> --journal X` | build `journal_requirements/reference.docx` from `requirements.yml`; `--line-numbers` to add line numbering the journal did not ask for |
| `assemble <project> --journal X` | build `submission/manuscript_rN.docx` through pandoc, behind six gates; `--force` to overwrite a build that has been edited since |
| `submission-package <project> --journal X` | the files that go with the manuscript - cover letter, title page, statements, checklist, and suggested reviewers unless the journal wants those in the letter - all of them, every run |
| `submission-names <project> --journal X [--name NAME]` | this round's package copied into `submission/upload/` under the name it is sent as, e.g. `EXAMPLE_STUDY_manuscript.docx`. **Copies; renames nothing.** Refuses on an outstanding flag, on a `short_name` nobody chose, and on a mock-data build |
| `format-check <project> --journal X [--file F]` | hold a built `.docx` to `requirements.yml`: font, size, colour, spacing, margins, line numbers, heading levels |
| `bib <project> [--prune] [--dry-run]` | the bibliography against what is actually cited; drops entries nothing cites |
| `ris <project>` | `references.bib` to `references.ris` |
| `ingest <project> --journal X` | the `edits/` inbox to `edits_status.md` |
| `response <project> --journal X` | `edits_status.md` to the point-by-point letter |

`--json` is accepted on either side of the subcommand, same as `pubmed.py`.

## The AI-use declaration

```bash
python tools/manuscript.py ai-disclosure "<project>" --journal Langmuir
python tools/manuscript.py ai-disclosure "<project>" --journal Langmuir --from-ledger
```

Journals want this three genuinely different ways — a statement in the paper, a
declaration in the submission, or an explicit statement that neither is needed —
and one journal can want more than one. `requirements.yml` gained an
`ai_disclosure` block, acquired the way every other requirement is: **`unknown`
until read, and `unknown` refuses rather than guessing.** A wrongly-worded
declaration is worse than an absent one, because it is a false statement about
how the paper was written.

**The engine does not write the statement.** It scaffolds
`drafts/source_text_rN/ai_disclosure.md` as a stub made of
`**[FLAG: author]**`, with the journal's own `scope` and `exempt` text quoted
above the blanks so the author answers the journal's question and not a generic
one. `submission-package` already refuses while any flag remains, and **that
refusal is the whole gate**, which is why the stub is written in flag form
rather than as a helpful draft.

**`--from-ledger` states what the engine factually did**, out of the
append-only `reports/rN/run_log.md` of every round, live and retired, naming the
round every line came from. It writes nothing the ledger does not contain: on a
project with no ledger it writes **nothing**, and says so, rather than
summarising what a run of this engine usually does. A module the ledger records
as `skipped` is never claimed as having run. Offered, never automatic.

The checklist gets one row, and **a blank one is not permitted**:
`required: unknown` *fails* it, because "nobody looked" and "not required" are
the two things that row exists to keep apart. A journal that genuinely requires
none gets a ticked row carrying the date and the URL it was read from.

`in_submission: cover_letter` puts the declaration **in the letter** and writes
no separate file — two files that must agree is how they come to disagree.

## What the round is drafting from, and how much each source counts

```bash
python tools/manuscript.py config "<project>" --journal X --weight captions=primary
python tools/manuscript.py config "<project>" --journal X --forget-weight captions
```

`source_weights` in `writing_config.yml`, four levels — `primary`, `normal`,
`low`, `ignore` — and anything unlisted is `normal`.

**A weight is instruction text in the drafting brief, not a coefficient.**
`primary` means *this source decides when sources disagree*; `low` means *use it
only where nothing else covers the point*; `ignore` means *do not read this file
this round*. It **never** changes which files a module is *given* — those lists
are the isolation contract and no dial may touch them — with the single
exception of `ignore`, which removes the file and names it in the denied list,
so a file that is not being read is never silently not being read. The suite
asserts, for every level, that no weight can add a file to a denied module's
given list.

`plan` prints the sources before the estimate and waits, as it already did:

```
  Drafting from:
    plan/outline.md            21 paragraph lines
    plan/captions.md           6 floats            PRIMARY  (your config)
    data/analysis/analysis.md  1.2 kB
    plan/README.md             EMPTY - never filled in
    data/analysis/analysis.md                MISSING - never generated
```

**`EMPTY` and `MISSING` are different words and both are printed**, because the
fix for each is different: one file needs filling in and the other needs
creating. Out of scope is printed as out of scope and never as a problem —
`manages` already settled that. And on a project where every source is filled
and nothing is weighted the block collapses to one line, because the common case
must not get noisier.

The cost is measured: the block spawns two `prose.py` interpreters, about 3.3 s,
essentially all of it Python startup. A cache took it from three to two.

## The outline, including when there is not one

`plan/outline.md` is the claims ledger every later check reads, and it is
routinely still the scaffolded stub when the data and the floats are already
in. `outline` answers that case rather than assuming it away.

```bash
manuscript.py outline <project>                                   # state + sources
manuscript.py outline <project> --write proposal.json --dry-run   # validate + diff
manuscript.py outline <project> --write proposal.json             # write it
manuscript.py outline <project> --write revised.json --replace    # change one
```

Read mode reports the state as **`missing` / `empty` / `present`** — three
different answers, not one error — plus an inventory of what an outline could
be built from and a `can_draft` verdict. **An outline proposed from nothing is
a fabricated paper and reads exactly like a good one**, so `can_draft` false
means stop, not guess. Two things the inventory has to get right, both found by
running it on a bare scaffold: `plan/README.md`'s own `## Status` section is
filled on a folder where nothing has been decided, and the scaffolded
`methods_facts.yml` is the whole file with every value blank.

The bundle is JSON:

```json
{"sections": [{"section": "Results",
               "lines": [{"claim": "Thermophilic arrays are more ordered.",
                          "evidence": "Fig 2; stats: symmetry_index ~ species"}]}],
 "notes": ["Figure 3 is planned but not made."]}
```

**Write refuses what is not an outline.** A line over 25 words, two sentences
on one line, a heading that is not a section of the paper, notes smuggled in as
a section, evidence written into the claim text, a duplicated claim. One line
is the *idea* of a paragraph, never a draft of it: detail written here is
detail written twice, and a long line also breaks the lexical alignment that
says which paragraph implements which line. Everything else goes under
`## Notes` at the bottom, which is never parsed as a claim.

The diff is **added / removed / changed / kept**, matched on the claim rather
than the position — a reordering would otherwise report every line as both
added and removed. `--replace` is required whenever the outline already has
lines, and the file it replaces is copied to
`obsolete/notes/outline_<timestamp>.md` every time. **The outline already there
is never destroyed.**

Beside it, `diff.classified` says what each change *does to the paper*, which
is not the same question. `added / changed` is the engine's vocabulary and it
collapses the two events furthest apart in an outline revision: adding a line
adds a **claim the paper will now make**, while filling an empty bracket only
attaches **evidence to a claim the author already wrote**. Shown the raw diff
of 2 added lines and 6 filled brackets, the user's reply was "I dont
understand what this means" — the diff was accurate and unreadable. The
classes are `new_claims`, `claim_removed`, `claim_reworded`, `claim_moved`
(each `decides: true`) and `evidence_attached`, `evidence_changed` (which
decide nothing); `classified.line` is the one-sentence summary, and a
reworded claim is paired back out of the added/removed halves it arrives as
rather than reported as a deletion and an invention. A `new_claims` entry
carries the bundle's `why` and `source` verbatim — the engine did not decide
to propose the line and cannot invent the reason, so it lists the ones that
came without one in `new_claims_without_reason`.

The accepted section names and the word cap come back inside `prose.py
outline`'s own payload as a `contract` field, so the writer validates against
the table the reader reads with. Same problem `CAPTION_HEADING_RE` has in three
files, avoided here by not making the second copy.

## A run that stops half way can be picked up

```bash
manuscript.py run <project> --journal X --start --preset coauthor
manuscript.py run <project> --journal X --step draft-sections --item results     --state done --wrote drafts/source_text_r8/results.md
manuscript.py run <project> --journal X --finish
```

A full run is several agent calls over a lot of text and stops part-way for
reasons that have nothing to do with the paper. `status` reports an open run,
so the call every invocation already makes is the one that surfaces it.

- **Record a step when it finishes, not at the end.** A record written only at
  the end is the one that is missing when the run dies.
- **`--item` for anything a module does more than once.** `draft-sections`
  writes five files and can die after three.
- **`--wrote` stores a SHA-256 and every read re-checks it.** A recorded output
  that has since changed or gone is reported. A ledger believed over the folder
  is worse than no ledger.
- **`--finish` refuses while a module is still pending.** Mark the ones that
  never ran `skipped`, with a reason.

Two files, and item 42 put each where it belongs: `round_state.json` in the
journal folder is the record — the run lives under its `run` key, beside the
round and the scope it belongs to — and `reports/rN/run_log.md` is the same
events in order, append-only, because a state file is rewritten and "how far
did the run that died get" needs an answer nothing later overwrites. Keeping
it with the round's other reports is also what makes `round` retire it.
Neither replaces `drafts/log.md`, the project's prose account of every round.

## Clearing the context between the halves of a round

**Every inter-module handoff in this engine is a file.** `revise-prose
--from-comprehension` is given `reports/rN/comprehension_report.md`,
`final-check` reads back the reports the isolated modules wrote,
`respond-to-reviewers` writes from `edits_status.md`, and the run's own
progress is in `round_state.json:run` and the append-only
`reports/rN/run_log.md`. Nothing downstream of any module depends on a
message. That came from bias isolation and the resume requirement rather than
from token economy, but it is the property a compaction needs and it was
already paid for.

So the second half of a round can be entered by a context that has been
cleared. `handoff` is the payload it is entered with:

```bash
manuscript.py handoff <project> --journal X [--stage submission] [--json]
```

Built entirely from disk. It reads no transcript and takes no free text.
`resume_line`, `next`, `done`, `remaining`, `failed` and `stale_outputs` are
`run --json`'s own fields, reused rather than recomputed — a re-entry payload
built from a second source of truth is a second source of truth. On top of
those it names the round's scope and where that came from, the adherence level
and whether anybody was asked, the round's intent, the live source-text folder,
a state per section, the standing config, the outstanding flag count, this
round's reports, and **the part of the skill the next stage needs**, by file
and by heading.

**It never says "safe to compact".** It cannot see the conversation, so a
verdict about the conversation would be a confident wrong answer at exit 0.
What it prints instead is `not_recorded`: the *classes* of state it cannot
see — anything the user said that nobody wrote down, any question asked and
not answered, any reasoning not yet written to a report. A checklist that says
what it does not know is worth more than a gate that lies.

**Before a clear, the session's own knowledge goes on disk**: a standing
preference through `config --instruct`, a one-round decision through
`run --step <module> --note`, a flag answer through `flag-answers`, anything
about the paper in `drafts/log.md`. If it cannot be written down, the boundary
is not safe. The failure mode of a compaction is not a crash — it is a second
half of the round that quietly does not know something.

`plan` marks every boundary the round crosses, inside the line the user
approves, so the shape of the round and its clear points arrive in the same
sentence. `writing_config.yml:compaction` governs what happens at one:

| value | behaviour |
|---|---|
| `auto` | **the default.** Capture, record the step, call `handoff`, clear — announced in one line, not asked |
| `ask` | the same, with a yes/no at each boundary |
| `off` | no boundary is crossed; `handoff` still works when called by hand |

`auto` is a deliberate reversal of this toolkit's *offer, never run*: every
other dial governs something that writes to the project folder and **a
compaction writes nothing**. The cost of a wrong `auto` is tokens; the cost of
a wrong `ask` is a feature declined into uselessness.

Three things override the dial and are not settable, because each is state
that is not on disk: a module that has not reached a terminal state (the
boundary is *inside* a module — compact at module boundaries, resume inside
them), a non-empty `stale_outputs`, and a question standing to the user
unanswered.


## Instructions you give, and that stick

`writing_config.yml` holds the dials, and two lists that are yours: `style_refs`
and `custom_instructions`. An instruction given at any invocation persists into
every later round until `--forget` drops it, because the alternative is a user
reasonably expecting round three to still know the PI hates the word "novel".

```bash
manuscript.py config <project> --journal X --instruct "Keep the discussion under 900 words"
manuscript.py config <project> --journal X --set outline_adherence=4
manuscript.py config <project> --journal X --forget "novel"
```

- **Each instruction is stamped with the date it was given**, so a standing
  instruction is traceable to the round it started in.
- **Re-giving the same instruction is a no-op**, compared after the date stamp,
  rather than a second copy.
- **A dial only accepts its documented values.** A typo does not fail - it
  silently reverts that dial to its default and biases the run in a way nobody
  asked for - so it is refused on the way in, and a value hand-edited to
  nonsense is reported by `plan`.
- **The user's own words survive the round trip**, quote characters and `#`
  included: the delimiter is chosen to suit the text rather than the text
  mangled to suit the delimiter.
- **Comments and hand edits in the file are preserved.** Only the affected key's
  line or block is rewritten; the file is explicitly the user's to edit.

`plan` returns the dials and the standing instructions alongside the module
list, so a caller cannot start a run without them in hand.

## How heavy a run is

Two levers, and they answer different questions. The **preset** is how much work
this round wants; `skip_modules` is what you never want.

```bash
manuscript.py plan <project> --journal X --preset draft         # 6 agent calls
manuscript.py plan <project> --journal X --preset coauthor      # 9
manuscript.py plan <project> --journal X --preset submission    # 12
manuscript.py plan <project> --journal X --preset coauthor --drop citation-check
manuscript.py config <project> --journal X --skip citation-check   # every round
manuscript.py config <project> --journal X --forget citation-check # undone
```

- **The standing skip composes with the presets rather than replacing them**, so
  changing preset still means what it says, and `--add` for one round beats the
  standing list and reports that it did.
- **A module name that does not exist is refused on the way in.** A typo written
  into the config would be a standing preference that silently never applies.
- **Neither the list nor a dial can drop citation-check from a submission run**,
  and `reviewer_check: off` removes reviewer-check from the plan rather than
  spending an agent call on a module that will decline to do anything.
- **citation-check is in every preset that writes prose** — draft, coauthor,
  submission, revision — and is never offered as an `--add`. It is mechanical
  (every in-text key resolves, every entry is cited, no duplicates, the count
  is under the cap), so asking about it is a yes/no question with one right
  answer. `--skip` remains the way to say no: the user saying so once, rather
  than being asked every round.
- **`plan` names what the config left out**, separately from what the preset
  left out, so the line the user approves says who decided.

Skipping a *module* does not skip the engine layer. `completeness` runs the
whole countable audit - citekeys, float callouts, length, flags - in about two
seconds with no agent involved, so a `draft` run still tells the coauthor about
an uncited reference in the `.docx`. What a light run buys back is agent calls,
not arithmetic.

## The user's own draft, and the two drafting modes

`drafts/rough_draft.md` sits beside `references.bib` and is **optional** - an
empty one is a normal project and nothing reports it as missing. It is one of
exactly two files in a project the pipeline reads and never writes, and the
other is `data/analysis/analysis.md`. `source_text_dest()` must never rewrite
it forward: it belongs to the project rather than to a round.

Anything under a heading there becomes the **highest content source** for that
section. That turns on one question - *is there prose for this section* - and
the answer is the mode, derived per section and never configured:

| mode | when | what draft-sections does |
|---|---|---|
| `compose` | the section is empty: r1 with no block, a wiped section, or `--redraft` | write it from the outline line, the recorded facts and the captions |
| `edit` | prose exists: a rough-draft block, **or any round after the one that wrote it** | fix mechanics, insert citations and flags, apply what the reports asked for, change nothing else |

`compose` and `edit` are one mechanism and the rough draft is the special case
of it, not the feature. `plan` prints the mode per section; `agent-brief` puts
the same table in the drafter's prompt, because an isolated agent told a rule
and not which sections it applies to cannot follow it.

**Emptiness is content-tested, never existence-tested.** The stub is scaffolded
into every project, so comments and headings are stripped before the test -
otherwise every project reports a rough draft on the day it is created.

**A number the user wrote that nothing measured is kept AND flagged**, and the
flag names `drafts/rough_draft.md`. Deleting it throws away the author's
knowledge of their own experiment; keeping it silently breaks layer 0.

`source_weights: {rough_draft: ...}` takes `primary` (default) or `ignore` and
nothing else. `low` and `normal` are refused **with the reason**: a rough draft
that is *sort of* consulted is the worst of both modes - the engine composes
its own section while borrowing the user's phrasing.

## The retention invariant: "just makes edits", checked

`retention` is the postcondition behind edit mode. It runs after
`draft-sections` and before `assemble`, per edit-mode section, against a
baseline that depends on the round:

- **r1, a rough-draft section** - the block the user wrote.
- **r2 and later** - round N-1's archived copy of the section, which `round`
  already writes before renaming the source text forward. Nothing new has to
  be recorded for this to work.

  There are **two archive layouts and both are read forever**, resolved by
  `retired_section_path()` and by nothing else:
  `obsolete/drafts/r{N-1}/source_text_r{N-1}/<section>.md` is what `round`
  has written since 2026-09-16, and the flat
  `obsolete/drafts/r{N-1}/<section>.md` is what every project archived
  before then has. A migration would lose r1's baseline on the projects that
  already exist, so there is none.

Three gates and one instrument, and the split is the rule that a new check with
teeth and no track record breaks working rounds:

| invariant | on failure |
|---|---|
| every baseline sentence present, or listed under **Removed** with a reason | reject |
| the baseline's numbers are a **subset** of the section's | reject |
| citekeys and `**[FLAG: ...]**` are **additive only** | reject |
| growth past +25% of the baseline | reported, sentence by sentence |

`reports/rN/retention_report.md` carries **Kept** / **Edited** / **Removed** -
named for what it protects rather than for the rough draft, because from r2 on
it is protecting the engine's own corrected prose.

**A rejection is not a failure of the round.** `--restore` writes the baseline
through verbatim with the pass's flags appended, and that is a usable section.
The property this buys, as a checked postcondition rather than an instruction:
**r3 cannot silently drop a sentence r2 had.**

## The outline generates in r1 and checks afterwards

`outline_adherence` is a **1-5 scale asked every round** in the plan block, not
a dial set once. The three old words are three of the five levels, so an
existing config saying `strict` reads as 5 and is never rewritten; everything
downstream compares numbers.

The round's answer goes to `round_state.json`. `writing_config.yml` holds the
**standing preference** and moves only on `--adherence "4 always"` - a
per-round answer that rewrote the standing dial would ratchet, and one round
drafted at 1 would turn the claims ledger off for every round after it.
A round nobody was asked about is labelled `(not asked)` in the plan output and
in `drafts/log.md`.

**From r2 on the same digit means something different.** It is how strictly to
*report* a deviation, never how literally to *rewrite* a paragraph. A level 5
that regenerated `results.md` in r3 would discard every correction r2's checks
earned and the build would succeed at exit code 0. **Engine-owned is not the
same as disposable.** The plan block prints `ADVISORY this round` when a
section already has prose, and `--redraft` is the only thing that rebuilds one.

Level 1 softens the three adherence findings to `advisory` and leaves every
truth check an error - the same softening a frozen section gets. Three
consecutive level-1 rounds raise a `completeness` **gap**, never a block.

## An unfinished paper builds

`completeness` reports everything standing between the project and a finished
paper: outline lines with no evidence, sections still stubs, floats with no
caption or never cited, numbers with no recorded source, unresolved citekeys,
outstanding flags, unknown journal requirements, and edits still pending.

**Nothing here blocks a build.** A half-written outline is the normal state for
most of a project's life, and refusing a coauthor draft because the discussion
is not written yet would make the pipeline useless exactly when it is most
useful. So the verdict travels with the document instead of gating it: `assemble`
appends a block headed **`[FLAG: incomplete — PAPER NOT COMPLETE]`** when prose
is missing or a figure is mock data - and only then. The full list, author
details and journal fields included, goes to `reports/rN/outstanding.md`. The
block is generated and disappears by itself once no prose is missing.

**After r1 the planning files are proposals, not work.** `completeness` puts
each outline, captions or analysis difference under `source_review` with an id,
and `manuscript.py source-review --accept/--decline` records which ones the
round acts on; only the accepted ones reach the `draft-sections` brief. An
abstract over its limit is listed under `auto_fix` and cut by the round unless
`length_policy: over` or the abstract is frozen.

Exit code is `1` for "not finished yet", never `2` - an unfinished paper is a
normal state, not a refusal. The three real refusals are about integrity rather
than completeness: mock data, an unresolved citekey, a build that would
overwrite an edited `.docx`, and an empty abstract box. `submission-package`
does not refuse over flags - it writes all five files and reports itself
**NOT SENDABLE** while any remain, which is the same information without an
empty folder.

## The six assemble gates

All six exist because the failure they catch is **silent**. Gates 5 and 6 are
newer than the first four and answer failures found by auditing a shipped
build: **gate 5** refuses to overwrite a `manuscript_rN.docx` that has been
edited since this engine wrote it (compared by SHA-256; `--force` overwrites
and loses the edits), and **gate 6** refuses a build whose body sections have
prose in them and whose `# Abstract` box is empty - that is not an unfinished
paper, it is a paper whose sections were written and whose abstract was
skipped.

0. **A section the journal names with no file behind it.**
   `structure.section_order` holds the journal's own section names, and
   `assemble` resolves each onto the scaffold's files — *Experimental Section*
   onto `methods.md`, *Results and Discussion* onto `results.md` and
   `discussion.md` under one heading, *Acknowledgment* onto a generated end-
   matter section. A name that resolves to nothing silently drops that section
   and the build still succeeds. Measured on the real *Langmuir* order: nine
   of ten names matched no file, and a valid `manuscript_r1.docx` came out at
   exit code 0 holding the introduction alone while the summary line named all
   ten sections. Refused now at one missing section as well as at all of them,
   and the refusal names the section and the file it wanted.
1. **Mock data.** Any float whose `plan/floats/float_provenance.json` entry says
   `mock` refuses the build. `--allow-mock` overrides and stamps the filename
   `manuscript_rN_MOCK.docx`. A mock-data draft must never be mistakable for a
   real one once it leaves the folder.
2. **Unresolved citekeys.** citeproc renders an unresolved key into the document
   and warns only on stderr, exit code 0. Measured here on pandoc 3.10:
   `[@nope2020]` becomes `(nope2020?)` in the output with `rc=0`. Any
   "citation not found" warning fails the build, and the partial output is
   deleted.
3. **Citekey residue in the rendered text.** Belt and braces, and **not
   redundant** — measured: a citekey inside a code span reaches the `.docx` with
   *no* citeproc warning at all, so gate 2 cannot see it. Gate 3 re-extracts the
   built document and fails on `[@key]`, a bare `@key`, or citeproc's
   unresolved-key form.

A clean build also writes `drafts/references.ris` — the EndNote escape hatch —
and reports, rather than swallows, a missing `style.csl` (citations not in the
journal's style). A missing `reference.docx` is **built** rather than reported:
pandoc's default sets every heading in Word's theme, so a build without one
reaches the editor in Aptos Display at 20 pt in accent blue. One that already
exists is **kept** — spec §5.4.1, so a hand-edited reference document survives
the next build — but the build now measures it against `requirements.yml` and
names the fields it does not carry, because otherwise the only sign is
`format-check` reporting the margins as wrong with nothing to say which of the
two files is behind. Measured out of the file, never from its modification
time: OneDrive rewrites those on sync.

## What a build contains that no source file holds

The title, the byline and the abstract reach pandoc as **metadata**, so they
come out in the `Title`, `Author`, `AbstractTitle` and `Abstract` styles. Before
this, `title_abstract.md`'s own `# Title` label came through as a `Heading1`
with the real title as body text under it, and `format-check` asked for a
`Title` style no build could produce.

The **end matter** is generated from `plan/author_information/authors.md` and
`plan/author_information/affiliations.md`: Supporting Information,
Author Information (corresponding author and ORCIDs, read from the author
table), Author Contributions, Funding, Conflict of Interest, Data
Availability, Ethics and Acknowledgment. A section is written when the
journal's `section_order` names it or `structure.statement_sections` marks it
required; one that is required and has nothing behind it becomes a
`**[FLAG: author]**` block and a warning rather than an absence. Both files
are read for every statement, first with content wins, so a project written
before the two were split loses nothing.

Two of those statements are **written rather than copied**. Author
Contributions is built from the contribution matrix, in author-list order and
in CRediT's own wording; Funding is built from the grant table, unless prose
is written under the heading, which wins.

The **bibliography** is placed where `section_order` puts it, through a
`::: {#refs} :::` placeholder, rather than appended after the end matter and
the PAPER NOT COMPLETE block.

## Suggested reviewers are one list

The names live in `<source_text_rN>/suggested_reviewers.md`, beside
`ai_disclosure.md` and for the same reason: it is something the *author* writes
that is not a manuscript section. `submission-package` creates the stub and
never overwrites it — everything *in* the package is regenerated on every run,
so a list kept there would be retyped every round.

Where it comes out is `submission.suggested_reviewers_in`:

| value | what happens |
|---|---|
| `cover_letter` | the names go **in the letter**, and no standalone file is written |
| `separate` | the standalone `suggested_reviewers_rN.md`, rendered from the same source |
| `portal` | neither; the journal collects them in its own system |
| `unknown` | the standalone file |

The cover-letter case follows the AI-declaration rule (writing-engine 17.5):
when the journal wants it in the letter it goes in the letter and there is no
second file, because two files that must agree is how they come to disagree.
The old behaviour was that failure in miniature — the names sat in
`suggested_reviewers_rN.md` while a sentence in the letter said they *"accompany
this submission"*, so a journal that wanted them in the letter got a letter
pointing at a file it had not asked for.

`unknown` writes the standalone file, which is the safe default in both
directions: a list the journal did not want is a file nobody opens, and a list
folded into a letter the journal wanted separately is a list that never
arrives.

**The stub's worked example is inside an HTML comment, and the reader strips
comments before matching.** It did not at first, and returned the example
reviewer as a real one — the same defect as item 123 and one line from the same
cause.

## The name it is sent under

```bash
python tools/manuscript.py submission-names "<project>" --journal LANGMUIR --dry-run
python tools/manuscript.py submission-names "<project>" --journal LANGMUIR --name EXAMPLE_STUDY
```

`manuscript_r5.docx` is the right name for five rounds and the wrong name for
the one moment that matters. An editor's inbox holds forty of them, and `r5`
says something about this project's bookkeeping and nothing about which paper
it is. This writes the round's package into `{JOURNAL}/submission/upload/`
under the name it goes out as:

```
submission/
  manuscript_r5.docx            <- what the engine reads on r6
  cover_letter_r5.docx  .md
  title_page_r5.docx    .md
  checklist_r5.md
  upload/
    EXAMPLE_STUDY_manuscript.docx  <- what you drag into the portal
    EXAMPLE_STUDY_cover_letter.docx
    EXAMPLE_STUDY_title_page.docx
    EXAMPLE_STUDY_checklist.md
    README.md
```

**It is a separate command, and it copies.** The round suffix is not
decoration: `round` retires by round, `final-check` verifies against the
round's outputs, the rebuild guard compares a SHA against `manuscript_r5.docx`
*by name*, and ten call sites build that name from an integer. Renaming in
place would break every one of them at the moment the paper is being sent,
which is the worst possible moment. `upload/` retires with the round like
everything else under `submission/`, so what was actually sent stays
recoverable from `obsolete/drafts/rN/`.

The stem comes from `project.yml:short_name`, uppercased with separators
normalised. **A `short_name` that is only the project folder's name, slugged,
is refused** — `scaffold.py` fills it in that way when nobody supplies one, so
a project can carry a name no human ever chose, and this is the name on the
file an editor receives. Pass `--name` to settle it.

It also refuses an outstanding `**[FLAG: ...]**` — the same gate
`submission-package` applies, said at the moment the files would go out — and
a build made from mock data, because this command's whole job is producing
files that look ready to send.

**The `.md` beside each `.docx` is not in the set**, and the command says which
ones it left out. The markdown is the file you edit and the `.docx` is what is
uploaded; a folder called `upload/` holding both answers "which one do I send"
with two files whose contents agree until somebody edits one. Files left by a
previous run under a different stem are removed and named — two sets in the
folder named for what is uploaded is item 17 all over again.

Re-run it after any rebuild. The copies do not update themselves.

## Who wrote it, and what each of them did

Two files, and `authors` reads both.

`plan/author_information/authors.md` holds the **author list** - one row per
person, `#` for submission order, affiliation keys, ORCID, email - and under
it the **contribution matrix**: one row per author, one column per thing a
person can do on a paper, an `x` in every cell that applies. The columns are
the fourteen CRediT roles under plain-English headings, and a column of your
own is carried into the statement verbatim rather than dropped.

`plan/author_information/affiliations.md` holds the **affiliations**,
keyed (`A`, `B`) so one address is typed once and an author with two of them
writes `A,B`, and the **grant table**, plus the Acknowledgements, Competing
Interests and Ethics statements.

Both sit in `plan/author_information/` rather than loose in `plan/`, and both
are scaffolded on day one with their tables already laid out. `plan/authors.md`
and `plan/affiliations.md` are still read where they are and are moved for
nobody; `front_matter_candidates` resolves the folder first, then `plan/`, then
the source text.

### The signed competing-interest forms

`plan/author_information/conflict_statements/` is an inbox for the signed
disclosure forms — one file per author, named with that author's initials
(`JV.pdf`, `RWM_icmje.pdf`). Most journals will not send a paper out for review
until every one is in, and they come back over weeks, from different people,
through whatever system the journal uses. Nothing was tracking them, so what
stalled a submission was a signature nobody had noticed was missing.

`submission-package` reads the file **names** and nothing else:

```
  COI forms  1 of 3 returned in plan/author_information/conflict_statements/
  warning  2 of 3 authors have not returned a signed competing-interest form ...
```

A form is matched on the initials the filename starts with, then on a surname
appearing anywhere in it. One that resolves to nobody is reported as
`unmatched` rather than dropped — to a submission, a form filed under a name
the author list does not recognise is the same thing as a form that never
arrived.

**The forms themselves are never opened.** A signed disclosure is a PDF out of
a publisher's portal: there is no format to parse, no field that means the same
thing twice running, and a wrong reading of one would be a claim about
somebody's declared financial interests. Counting paperwork is a job that can
be done correctly; reading it is not.

#### The journal's own blank form

`conflict_statements/blank_form/` holds the *unsigned* form — the thing
everybody signs. A subfolder and not a reserved filename, because
`conflict_statements()` counts files: a blank sitting among the signed ones is
an author marked as having signed because somebody downloaded the form. A
directory is skipped by that reader for free, and it is also the honest shape,
since a journal can want an ICMJE form *and* its own declaration.

**The engine does not fetch it; the skill does.** Finding the right form on a
publisher's site is judgment and needs the web, so `writing-engine` goes and
gets it on the round where the Competing Interests statement first raises its
flag, and records the URL in `requirements.yml` as `submission.coi_form_url`.
That is the house rule — *if it has a right answer, it is Python; if it takes
judgment, it is markdown* — applied to a task where the engine could
technically fetch a URL and could not possibly decide which URL.

`conflict_form_warnings()` is **deliberately separate from**
`conflict_warnings()`. One says nobody has sent a form back; the other says
nobody was ever sent one. On a first round those look identical from the report
and **the fix is the opposite** — chase four people, or go and find one PDF.
`submission.coi_form: not_required` silences it permanently, because a journal
that collects disclosures in its own portal is a different state from nobody
having looked.

These are **warnings and never flags**. A flag means the manuscript cannot
answer something the journal asked, and a flagged package is refused as
sendable. The forms are not in the package at all — they go to the journal's
own portal, attached to the submission record — so an outstanding signature
does not make a finished paper unfinished. The Competing Interests **statement**
that gets printed in the paper is prose and stays under its heading in
`affiliations.md`; these files are the paperwork behind that sentence, not the
sentence.

```bash
python tools/manuscript.py authors "path/to/project"
```

It reports rather than edits. Who is credited with what is the user's call,
and a tool that quietly adds a role to a matrix is a tool that has written an
authorship claim nobody made. What it catches:

| finding | why it is blocking |
|---|---|
| a contribution credited to someone not in the author list | the statement would name a person the paper does not |
| an affiliation key nothing defines | the byline prints the letter instead of the address |
| an ORCID that is not an ORCID | a mistyped ORCID points at a different human being |
| a corresponding-author line naming nobody in the list | the journal writes to nobody |
| the same author twice | |

An empty matrix, an author with no row, an unused affiliation and a grant held
by a non-author are **gaps** rather than blocking: they are the normal state of
a project that is still being filled in. All of it also reaches
`completeness`, and therefore the PAPER NOT COMPLETE block inside the `.docx`,
under the area `authors`.

**The tables are read by column heading, never by position.** The rule that
used to tell the author list from the contributions table was "five cells or
more is an author row" - and a fifteen-column matrix row is nineteen cells
wide, so every row of it was read as an author whose name is the letter `x`.
Measured on a filled-in project, the byline came out
`Cyd Doyle, Ada Bell, Russell W. Mercer, concept, x` with `methods` numbered
as an affiliation. `tests/manuscript.py` pins it.

Three things a filled-in matrix gets right that a hand-written statement does
not: the statement follows the **`#` column**, not the order rows happen to be
in, so a first author cannot become third by a stray cut and paste; a row keyed
by **initials** (`RWM`) resolves to the author it belongs to; and only a real
tick counts - a cell holding a note is not a claim of credit.

## The check that reports instead of refusing

`format-check` reads the finished `.docx` back and holds it to
`requirements.yml`. It runs at the end of every `assemble` and is also a
standalone command.

What it looks at: font family, size, colour, line spacing and justification on
every style the document uses; all four margins and the page size; whether line
numbering is present when the journal never asked for it and whether the pages
are numbered; whether the document has a paragraph in `Title` and no label from
`title_abstract.md` wearing a heading's style; and whether each section the
journal lists — resolved through the same alias table `assemble` uses — is a
level-1 heading, in order.

It warns rather than refuses, because a font is repairable in a way a citekey
reaching an editor is not. Errors exit 1.

**Two attributes beat the value beside them, and both were found by opening the
result in Word rather than by reading the XML.** `w:rFonts/@asciiTheme` beats
`@ascii`; `w:color/@themeColor` beats `@val`. A styles.xml can name Times New
Roman and `000000` and still render in Aptos Display and accent blue, so both
are removed rather than overwritten, and `format-check` reads the attribute
that wins.

**Line numbering is opt-in.** It used to be unconditional, on the reasoning that
most journals require it. Measured: *Langmuir*'s guidelines never mention line
numbers and the group's own accepted *Langmuir* manuscript carries none. They go
on when the journal's guidelines say so, or on `--line-numbers`.

**Page numbering is the opposite default, for the opposite reason.** It is on
unless `text.page_numbers` says otherwise, as a centred `PAGE` field in a
generated `word/footer1.xml`. Line numbers are a journal-specific request and
putting them on unasked changes what the editor receives; a page number is on
every publisher's own Word template and none of the guidelines this toolkit has
been pointed at forbids one. A submission with no page numbers reads as a
document nobody set up. The number is a field rather than a typed character —
a literal `1` in a footer stays 1 for the whole document.

**Justification is a requirement like any other**: `text.justification` is
`justified` or `left`, and unsourced it stays at pandoc's ragged right and the
build says so. It is set on `Normal`, `BodyText`, `FirstParagraph` and the
bibliography and never written onto the headings, which inherit it — which is
what the group's accepted *Langmuir* paper does, where `Normal` carries
`jc=both` and not one of its 174 paragraphs carries a `jc` of its own.

**Margins are per side.** `text.margins_in` sets all four and
`text.margin_top_in` / `_bottom_` / `_left_` / `_right_` override one each, so a
journal asking for a wide binding edge gets one. Only the top margin was ever
read before.

## Rounds are snapshotted unconditionally

Byte-identical copies included. The instinct to skip unchanged files defeats the
whole point: the guarantee being bought is that `obsolete/figures/r3/` **is**
what r3 contained, and a sparse archive forces the question "was it unchanged,
or did the snapshot fail?".

What is snapshotted: the rendered `.png`/`.pdf`, the `.rds` flextables, the `.md`
source text, **and the generating `.R` scripts** — outputs alone are a record;
outputs plus the script are reproducible.

**The journal folder holds the live round and the standing files.** `round`
**moves** `submission/` (the manuscript and its package), `reports/rN/` and
that round's received edits into `obsolete/drafts/rN/`, so
`obsolete/drafts/rN/` is the complete record of one round and the journal
folder always answers "what goes to the journal right now". Two things
deliberately do not retire: `drafts/log.md`, which is the history and lives
one level up so every journal's rounds read as one sequence, and
`edits/edits_status.md`, which `final-check` re-verifies every round.

The round's prose goes into a **folder of its own** inside that record, so
the sections do not mix with the subfolders that retired beside them:

```
obsolete/drafts/r2/
  source_text_r2/      r2's prose, named for the folder it came out of
  submission/          manuscript_r2.docx and its package
  reports/             that round's module reports and run_log.md
  edits/               what came back, minus edits_status.md
  manifest.md  manifest.json
obsolete/drafts/LANGMUIR/   a retired journal, whole (`retire-journal`)
```

The folder is named for what the live folder **was called**, read off disk
and never rebuilt from the round number: a project that predates the label
archives as `source_text/`, and that is correct.

**`obsolete/` is a record and never an input.** Exactly one reader is
permitted to treat it as input - `retention_baseline()`, which holds round
N's prose against round N-1's, and which never puts a retired sentence back.
No module's `given` list may resolve to a path under it, because a drafting
pass handed a retired round's prose can revive an edit that was declared
spent, sincerely, since the text is genuinely this paper's.
`tests/portability.py` pins that.

Each round writes `manifest.md` (a SHA-256 per file and a "changed since
r(N-1)" column) and `manifest.json` beside it, so "did Figure 2 actually change
between r3 and r4" is answered by diffing two text files rather than opening
images. Every entry carries both `file` - the path relative to the round
folder - and `name`, its basename; the comparison is made on the path first
and the basename second, so it survives the source text moving into a
folder of its own. Without that, r3's `source_text_r3/introduction.md`
would never match r2's `introduction.md`, every row would read `-`, and the
column that answers "what actually changed" would still render and still
exit 0.

## The edit ledger

`drafts/edits/` is one inbox for coauthor returns, reviewer reports, and the
user's own marked-up file. `ingest` reads `.docx` through `docx_edits.py` and
PDFs through `pypdf`, never by eye.

**It sits beside `source_text/`, not inside the journal folder.** It belongs to
the paper rather than to the journal: a coauthor sent a manuscript returns it
to the folder they were pointed at last time, and nobody outside the project
knows or cares which journal this round is aimed at. A return dropped in
`drafts/IJROBP/edits/` after the paper moved to Langmuir is a file `ingest`
does not read and nothing reports as missing. `retire-journal` carried a whole
refusal path to walk the ledger across that boundary by hand; one inbox a level
up makes the boundary disappear. A project that already holds
`drafts/<JOURNAL>/edits/` keeps using it and is moved for nobody — `edits_dir`
resolves the old location first where it exists.

- **OneDrive sync-conflict copies** (`*-DESKTOP-XXXX.docx`) are excluded.
  Reading one alongside its original is one coauthor's edits counted twice and
  attributed to a phantom collaborator.
- **Attribution comes from the OOXML, not the filename.** A file named
  `manuscript_r4_JV.docx` routinely carries another author's revisions too,
  because it was forwarded. Crediting them all to JV would be a silent
  misattribution of authorship in the one record meant to prevent exactly that.
  The recorded author wins whenever it resolves to someone in `authors.md`, and
  the forwarding is reported.
- **An initial not in `authors.md`, or a filename that does not parse, is asked
  about — never guessed.**

`edits_status.md` is regenerated every round, and rows are matched on a
**fingerprint** of (source, normalised request text), not on position. So a
hand-marked `applied` survives regeneration, and a row whose source file has
left the inbox is retained rather than deleted — the inbox is not the record,
the ledger is.

**Every row carries an `authority`, and it is set from where the file came
from** (specs/edit-authority.md 1). `coauthor` is a return out of `edits/` or a
reviewer report: it **stands** until the paper is submitted, and the three
passes that could reach it — `revise-prose` and the two fix passes — are handed
the standing list in their briefs and told to raise a
`**[FLAG: edit-override]**` rather than rewrite. `own` is the user's own markup
of `submission/manuscript_rN.docx`, which `ingest` now reads as well: it used to
be *counted* for the round menu and recorded nowhere, so the one file that ranks
edits held one of the two classes. An `own` row from an earlier round is
**dropped** on the next ingest — applied, then spent — and the count is said out
loud. The column is optional on read, so a ledger written before it parses, and
a row without one reads as `coauthor`, which is the protected class: misreading
a colleague's sentence as spent costs their work, misreading a spent one as
standing costs one question.

A tracked **insertion** whose text reads as an instruction rather than as prose
— *"reword this"* typed into the body — comes back as a warning naming the row,
for a `**[FLAG: edit-intent]**`. `reads_as_instruction()` is deliberately narrow:
an instruction verb at the start **and** a word referring to the document, or a
leading *please*. "Add 5 mL of buffer" is a methods sentence in the imperative
and does not fire.

`response` refuses to render while any reviewer point is `pending` or
`conflict`, and refuses a `declined` point with no recorded reason: for a
reviewer point, that reason *is* the letter's argument.

## An entry nobody cites does not belong in the bibliography

`prose.py citekeys` reports it; `manuscript.py bib --prune` is what removes it.
The two read different scopes, and deliberately so:

- **`citekeys` reports against the five manuscript sections.** "Is the
  bibliography tight?" is a question about the paper.
- **`bib --prune` protects against the whole project.** "Is it safe to delete
  this entry?" is a different question, and an entry cited only from
  `plan/relevant_literature/`, a caption, or a coauthor's request in
  `edits_status.md` is still cited. Those are reported as kept, with the file
  that cites them named.

`--dry-run` prints what would go and writes nothing; that is what you show the
user before asking. The real run copies the old `references.bib` to
`obsolete/notes/` first and regenerates `references.ris` after. Entries are cut
by **brace counting**, not by a regex over the entry - a nested `{}` in a title
is normal, and a non-greedy match stops in the middle of the entry.

## Examples

```bash
python tools/manuscript.py status "Projects/my_paper" --json
python tools/manuscript.py init   "Projects/my_paper" --journal IJROBP
python tools/manuscript.py config "Projects/my_paper" --journal IJROBP --instruct "No hedging in the abstract"
python tools/manuscript.py plan   "Projects/my_paper" --journal IJROBP --preset coauthor
python tools/manuscript.py completeness "Projects/my_paper" --journal IJROBP
python tools/manuscript.py assemble "Projects/my_paper" --journal IJROBP
python tools/manuscript.py retention "Projects/my_paper" --journal IJROBP --restore
python tools/manuscript.py round  "Projects/my_paper" --journal IJROBP
python tools/manuscript.py bib    "Projects/my_paper" --prune --dry-run
python tools/manuscript.py ingest "Projects/my_paper" --journal IJROBP
python tests/manuscript.py                   # 229 checks
```

---

# prose.py

Every manuscript rule with a countable answer. Pure standard library, fully
offline. Re-measure: `python tests/prose.py` (183 checks, ~1s).

## Commands

| command | does |
|---|---|
| `density <files or project>` | the number-vomit budget per paragraph |
| `outline <project>` | outline lines vs. paragraphs, and every evidence bracket |
| `flags <files or project>` | collect `**[FLAG: ...]**` with locations. Reports only — nothing writes a `flags.md`, because the source text and the PAPER NOT COMPLETE block already carry the list (item 43) |
| `citekeys <project>` | in-text citekeys vs. `references.bib` |
| `captions <project>` | claim-first legends, float/text pairing, live-caption diffs |
| `crossrefs <project>` | every float and panel called out, in the required form and in order |
| `length <project>` | word and float counts against the journal's budget |
| `numbers <project>` | every number in the text against every recorded source |
| `voice <files or project>` | how the prose reads: length distribution, openers, lead rank, hedging, passives. Reports, never grades |
| `metaprose <files or project>` | text whose subject is the document itself, flagged and left alone |
| `ai_voice <files or project>` | the 15 countable AI-voice patterns. Report only, never a gate |
| `spelling <files or project>` | British spellings, **corrected** with `--fix`. The one command here that writes |

## `spelling` — the one check that fixes instead of reporting

Everything else in this engine reports. This one writes, and the reasons it is
allowed to are all of the same kind: the finding names an exact word at an
exact offset, the correction is a lookup rather than a judgment, there is no
threshold to calibrate, and every false positive can be listed by name.

```
python tools/prose.py spelling "Projects/my_paper"          # list them
python tools/prose.py spelling "Projects/my_paper" --fix    # correct them
```

A review draft can easily carry a dozen British forms — `centre`,
`internalised`, `signalling`, `tumours`, `diarrhoea`, `neutralisation` — and
until this check nothing in the toolkit looked at them.

**The map is a word list, not a set of suffix rules**, and that is the whole
of its safety. Written as rules, `-ise → -ize` fires on *surprise*, *comprise*
and *exercise*, and `-yse → -yze` fires on **`analyses`** — which is the plural
of *analysis* in both dialects, so the "fix" would turn a noun into a verb. The
`-yse` family therefore deliberately omits the third-person form and takes the
miss.

**Four things are never touched**, each of them a real failure if it is:

1. Anything `mask_excluded` blanks — code spans, links, citekeys, formulae.
2. **Quoted material.** A quotation is the other author's spelling.
3. **A capitalised word inside a capitalised run** — `Medical Research Centre`
   keeps its own spelling. Only the same sentence on the same line counts as a
   neighbour, or a heading two lines up protects the first word of every
   paragraph under it.
4. **`references.bib`**, refused at the door. A published title is quoted
   material with a DOI attached.

Case is the user's: `Colour → Color`, `COLOUR → COLOR`. The dial is
`writing_config.yml: dialect` (`us` | `uk`); `uk` runs nothing, because only
the British-to-American map is built and a dial that claims a direction it does
not have is a quiet lie. Module 1c runs `--fix` before any checker reads the
paper, and the drafting brief asks for American spelling so there is usually
nothing left to correct.

## Counting numbers the way a reader pays for them

The insight that makes the rule checkable: **not all numbers cost the same.**

- **Inline numbers** — values in the running sentence, outside parentheses.
  Expensive: each has to be read to parse the sentence.
- **Parenthetical clusters** — one grouped parenthetical, however many values it
  holds. Cheap: the eye skips it as one object. **One cluster = one unit.**

Budgets are per 100 words: `low` 2 inline / 3 clusters, `medium` 4 / 5, `high`
report-only. Two rules survive any setting: no paragraph past one-third numeric
tokens, and every inline number still needs its interpretation.

**The budget is per section, and it resolves in four steps.** One number applied
to five sections that are not alike was, on the one real run, too restrictive in
Methods and too loose in the abstract *at the same time*: 8 of 11 findings landed
on the section whose job is to be dull and complete, while an abstract at nearly
4x the same budget sat unremarked in the same list. So the defaults are
`methods: high` (unenforced, which is what the prose instruction always said),
`results: medium`, `introduction` and `discussion: low`, and
`title_abstract: {inline: 6, clusters: 2, basis: absolute}`. `basis: absolute`
exists for the abstract and only for the abstract — a rate over 250 words is
noise, and what matters there is the total, because the reader meets all of them
at once.

`density` takes the first of these that exists, and `--json` reports which one it
was as `budget_source`:

1. the section's entry in this project's `writing_config.yml` — the user said it
2. an `active` learned budget for `(research_type, section)` in `rules.yml`
3. an `active` learned budget for a **parent** research type
4. the built-in default

Order 1 above 2 is not decoration: a value typed into this project's config is a
statement about *this* paper and has to beat a lesson learned from a different
one. The scalar spelling `number_density: low` still forces one level on every
section, because an existing project's config cannot change meaning under a new
version of the tool.

**A finding can be advisory.** `--overrides reports/rN/overrides.md` reads the
paragraphs where a layer 1-3 rule required breaking the budget and the drafter
logged it; `--advisory <section>` names a section the user has taken over. Both
soften a budget finding to `advisory` and neither softens a truth check: a
hand-written paragraph that is half digits is still over the one-third cap.

**Proration scales the budget up and never below the base.** Measured: the
spec's own model sentence in §6 rule 1 is 47 words carrying two inline numbers,
so a strictly proportional budget of 0.94 flags the example the spec holds up as
correct. A paragraph shorter than 100 words still gets one complete comparison.

**What does not count**, and the exclusion list is load-bearing — without it the
check fires on every mention of `16S rRNA` and becomes noise the user learns to
ignore, which is worse than no check: cross-references, citation markers, dates,
chemical and biological nomenclature, instrument models, catalogue and grant
numbers, and anything spelled out rather than written in digits.

**The unit list is case-sensitive, and that is the whole trick.** `16S` and
`50 s` differ only in the case of one letter, so a case-folded list makes the
16S rRNA gene a quantity again. Same for `T4` vs. tesla and `G2` vs. g-force:
on a capital, the nomenclature reading wins.

## What each check proves

- **`outline`** validates every evidence bracket against the file that would
  have to contain that evidence: `Fig N` / `Table N` against `plan/captions.md`
  (a panel suffix like `Fig 4B` resolves to Figure 4), `stats: <term>` against
  `data/analysis/analysis.md`, a citekey against `references.bib`. An empty
  bracket is the gap, visible before any prose exists to disguise it.
  The paragraph-to-line alignment is **a hint, not a verdict** — it is greedy
  and one-to-one over the whole score matrix, because per-line matching lets one
  strong paragraph win three outline lines and report the rest as dropped.
- **`numbers`** checks against `data/analysis/analysis.md` first, then
  `methods_facts.yml` and `provenance.json`, and names which one matched.
  Rounding is allowed and has to be; recomputation is not, so a percent change
  the output never produced stays unmatched. Only the **values** of those files
  are read, never their comments — scanning a whole `methods_facts.yml` for
  digits matches its own instruction block, and then any number in the
  manuscript "verifies" against a template nobody filled in.
  `plan/captions.md` is deliberately **not** a source: a number in a caption
  came out of the same stats output, so accepting it would let the pipeline
  verify itself against its own earlier output.
- **`captions`** enforces the claim-first legend, pairs each float to the
  paragraphs that name it in either direction, and reports every difference
  between `plan/captions.md` and `source_text/live_captions.md` as a diff line
  with word counts — flagged as `claim_changed` when the bold assertion itself
  was edited.

- **`crossrefs`** answers the four questions a copy editor asks about floats:
  is every panel the legend defines pointed at by a sentence, is every panel
  the text cites actually defined, is the callout in the form this journal
  wants, and is every float first mentioned in the order it is numbered.
  Figures, tables, and their supplementary runs are four independent
  sequences. `float_never_cited` is deliberately *not* reported here - that is
  `captions`' finding, and a rule with two homes is reported twice and fixed in
  one of them.
- **`length`** counts what a journal would count - prose in `source_text/`
  without headings, HTML comments, fenced code or `**[FLAG: ...]**` blocks -
  per section, for the abstract on its own, and for the body and the whole
  file. It says what it counted, because what a limit *includes* varies by
  journal and is sourced (`text.word_limit_counts`) rather than assumed. Main
  text figures and tables are counted against their caps; supplementary ones
  are not, which is the point of having somewhere to move things to.

## Going over the limit is a question, not a defect

`length --policy` carries the user's answer so the question is asked once
rather than every round:

| policy | means | severity of the finding |
|---|---|---|
| `ask` | stop and ask - the default | `decision` |
| `over` | carry the overage for now, keeping all the information | `info` |
| `shorten` | cut, and consolidate what will not fit into the SI | `warning` |

Under every policy the overage stays in `over[]` and on `completeness`'s
outstanding list. An accepted overage is a thing to resolve before submission,
not a thing to forget.

## One scanner for float callouts, because two disagreed

`float_mentions()` is shared by `captions` and `crossrefs`. The scanner it
replaced was local to `captions` and could not see a plural callout at all:
measured, `(Figures 1 and 2)` and `(Figs. 1-3)` each returned **zero** matches,
so a float cited only that way was reported as never cited - and that report
travelled into the `.docx` in the PAPER NOT COMPLETE block. The shared scanner
reads plurals, comma lists, ranges (a float cited only inside `Figs. 1-3` is
cited), attached panel letters in either case, and the supplementary `S`
prefix. A *singular* keyword deliberately does not start a list, which is what
keeps `Figure 1 and 3 mM salt` from citing a Figure 3.

## Two contracts, each in three places

`CAPTION_HEADING_RE` appears in `prose.py`, `idea.py`, and
`plan/theme/read_captions.R`. `FLOAT_DIR_RE` — the float folder grammar, which
decides that `Fig01`, `Fig1` and `Fig01_yield_by_catalyst` are all Figure 1 —
appears in `scaffold.py`, `idea.py`, and the same `.R` file, and
`tests/scaffold.py` pins it the same way. A folder one parser cannot read is
invisible to the preview and the Word floats, and nothing reports it.

As for the caption grammar: It is reproduced rather than approximated on
purpose: a block one parser cannot see must be invisible to all of them, or the
two halves of the pipeline disagree about what the float set is.
`tests/prose.py` reads the regex straight out of the `.R` file and asserts it
matches, allowing only the `[0-9]` / `\d` spelling difference that R's default
regex engine forces.

## Examples

```bash
python tools/prose.py density "Projects/my_paper" --config "Projects/my_paper/drafts/IJROBP/writing_config.yml"
python tools/prose.py outline  "Projects/my_paper" --adherence medium --json
python tools/prose.py citekeys "Projects/my_paper" --max-refs 50
python tools/prose.py captions "Projects/my_paper" --cap 350
python tools/prose.py crossrefs "Projects/my_paper" --style parenthetical
python tools/prose.py length   "Projects/my_paper" --limit total=3500 --limit abstract=250
python tools/prose.py numbers  "Projects/my_paper"
python tools/prose.py flags    "Projects/my_paper"
python tools/prose.py voice    "Projects/my_paper" --per-paragraph --json
python tools/prose.py metaprose "Projects/my_paper" --insert
python tools/prose.py density  "Projects/my_paper"     --rules writing_guides/learned/rules.yml --research-type surface_science
python tests/prose.py                        # 183 checks, offline
```

---

# learn.py

What the user's edits teach, banked so the *next* paper's first draft is better.
Pure standard library, fully offline. Re-measure: `python tests/learn.py`
(155 checks, ~2s). Spec: `specs/writing-engine-prose.md` §5.

The problem it exists for was measured, not imagined: the user edits r1, the
engine regenerates r2 from `plan/outline.md`, and the edits are gone — and the
next *project* starts from the same brief that produced the prose the user just
spent an afternoon fixing. Every correction is paid for once and banked never.

**Apply and learn are separate steps and both are required.** Applying without
learning is what the toolkit did before this file. Learning without applying
would leave the current paper inconsistent with the lesson just taken from it.

The registry lives in the **toolkit** (`writing_guides/learned/`), not in a
project, because its whole purpose is to cross projects.

## Commands

| command | does |
|---|---|
| `extract` | aligned before/after pairs for a section the user edited, at paragraph and sentence granularity, plus moves, splits and merges |
| `classify` | the mechanical evidence behind each edit's class, and the three classes that evidence decides on its own |
| `observe` | record one observation; promotes the rule when it earns it |
| `promote <id>` | force a candidate active, deliberately and with a reason |
| `retire <id>` | retire a rule — kept, never deleted |
| `resolve <id> --against <id>` | write down a contradiction's outcome: demote, supersede, or leave both in conflict |
| `conflicts [id]` | every active rule whose scope overlaps |
| `merge <file>` | union another registry by id — the shared-folder case |
| `stats` | observations per research type, types too thin to learn from, rules that merely restate the digest |
| `budgets` | what each section's number budget resolves to, and why |
| `brief` | the whole ladder, layers 0–4, as one ordered document |
| `types` | the controlled vocabulary of research types; adding one needs a reason |
| `frozen <project>` | which sections the user has taken over, inferred by hash |

`observe`, `resolve` and `frozen` are not in the spec's command list and are what
make the rest work: an observation has to reach the registry somehow, §5.6's
three outcomes have to be written down somewhere, and §4.1's hash belongs where
the tool that consumes it can reach it.

## The precedence ladder, and what may never be learned

A lower-numbered layer beats a higher-numbered one, and `brief` emits them in
that order:

| layer | contents |
|---|---|
| **0** | invariants — the recorded-value rule, citekeys, flag blocks, and the two absolutes of the number rule |
| **1** | the user's edits from the previous round |
| **2** | the ten rules [MK] plus the topic/stress-position rule [GS] |
| **3** | learned rules for this research type |
| **4** | presentation: the density budget, the digest's `## At revision` prohibitions |

Layer 0 is the amendment to the user's own ordering, and it is the point of the
whole design: a layer-3 rule is *induced from prose edits*, and no amount of
evidence from prose editing can license writing a number that was never
measured. **`learn.py` refuses to create a `kind: numeric` record for either
absolute** — the one-third numeric cap and the interpretation rule — because a
fraction cap looks exactly like a budget, and that is the single most likely
place for the loop to quietly eat a layer-0 rule. The refusal is a test, not a
comment.

## One observation is never a rule

| status | requires |
|---|---|
| `candidate` | 1 observation. **Applied to nothing.** Listed for the user |
| `active` | 2 observations in different sections or different rounds |
| `active`, scope `universal` | evidence from two different research types |
| `formatting` | 3, not 2 — it is learned at low weight |

Two paragraphs of one section in one round are two observations and **one** piece
of independent evidence: an afternoon spent editing one section is not two
observations. A registry that learns from n=1 is wrong in a way that is very
expensive to notice, because the wrongness arrives as slightly worse first drafts
on an unrelated paper months later. `promote <id> --because "..."` overrides the
threshold deliberately and on the record.

**Only two classes can become rules.** A `factual` edit (`400 °C` → `450 °C`) is
a data discrepancy on the recorded-value path; a `citation` edit routes to the
author-edit ingest; a deleted paragraph is an outline event. Those three are
decided mechanically — did the numeric multiset change, did the citekey multiset
change, did a whole paragraph come or go — and what is left is `stylistic`,
`structural` or `formatting`, which is judgment and comes back `undecided` with
the evidence rather than a class this file invented.

One deliberate exception, and it is narrow: an edit that *adds* numbers to a
Methods section is evidence about how many numbers that kind of paper's Methods
carries, which is a fact about the section rather than a claim about taste. A
`factual` edit may contribute to a `kind: numeric` record **and to nothing else**.

## A budget is learned from both directions

A learned budget is the 75th percentile of the observed counts, rounded to 0.5 —
not the maximum, which would learn from the single worst paragraph. Overrides
push it up; edits that *remove* numbers from a Discussion push it down. If the
mechanism only ever loosened it would be a slow amnesty rather than learning, and
even so the evidence arriving is one-sided, so `budgets` prints each learned
value beside the built-in default and the drift between them. A budget that has
moved more than 2×, or one that came into existence where the default was
unenforced, wants the user's eye rather than another observation.

## Research type, and why the vocabulary is closed

Layer 3 is scoped by research type, so the type has to be stable. The failure
mode to design against is **type proliferation**: a type invented per project
scopes every rule to a population of one, no rule ever reaches two observations,
and nothing is ever learned — and *nothing errors while that happens*. Hence
`writing_guides/learned/research_types.yml`, a closed list extended only with a
reason, and `stats` naming any type with one project in it. `parents` is how a
rule generalizes one step: a rule scoped to `life_science` reaches
`biochemistry`, `structural_bio` and `clinical`.

## Frozen sections

`frozen <project>` compares each section against the hash the engine recorded in
`source_text/.provenance/sections.json` and returns one of four states: **fresh**
(no entry), **engine-owned** (hash matches — may be redrafted), **frozen** (hash
differs — the user has taken it over), **wiped** (entry present, file gone — the
existing "empty `source_text/` to force a redraft" gesture, which has to keep
working). Hashing normalizes trailing whitespace, line endings and runs of three
or more blank lines, and nothing else: a changed word is a change.

Section granularity, never paragraph. A paragraph-level hash would let the engine
keep writing around the user's edits, which is the behaviour that makes a tool
untrustworthy. `frozen --record <section> --by revise-prose` writes the hash of
record after a module wrote or revised a section — and the revision pass updating
it is the regression this design is most likely to have, because without it a
revised section is frozen against itself and the engine stops touching prose it
wrote.

**Nothing in the engine called that subcommand for the first six months of its
life** (item 88), so the ledger did not exist in any project, every section took
the `fresh` branch, `load_frozen` returned an empty list, and every guard that
says a frozen section is skipped subtracted nothing. The writer now runs where
the writing is already recorded: `manuscript.py run --step <module> --state done
--wrote <path>` records the hash for every `--wrote` path that is a section of
the live source text. It is not in each module's brief on purpose — a guarantee
a module makes on its own behalf is a guarantee nothing verifies, and a module
that forgot the call would silently hand its section to the next pass. The
subcommand stays, for repair and for a project the engine did not draft.

## The registry is a file people edit

`rules.yml` is read and written by a small YAML reader here rather than by a
dependency, and it is strict where it matters: a malformed registry **fails
loudly** rather than reading as an empty one, because the dangerous outcome is
not the error, it is drafting on with no layer 3 and nothing saying so. A record
with no `id`, `kind` or `status` is refused for the same reason.

The toolkit lives in a shared folder, so two people can learn into one file:
`merge` is a union by id — same id and same evidence is a no-op, same id and
different evidence merges the evidence and re-evaluates the threshold, and the
same id holding a different rule is renumbered with a note saying why. Do not
`git init` inside the shared folder to solve this.

## Examples

```bash
python tools/learn.py frozen "Projects/my_paper"
python tools/learn.py classify --project "Projects/my_paper" --section methods --json
python tools/learn.py observe --rule "Open the abstract with the material system" \
    --kind stylistic --research-type surface_science --section title_abstract \
    --project "Surface Example" --round r1 --paragraph 1 \
    --before "Temperature-programmed desorption was used to..." \
    --after "Pd nanoparticles on TiO2(110) decompose..."
python tools/learn.py brief --research-type surface_science --section results \
    --layer1 "drafts/Langmuir/reports/r2/layer1.md" --out brief.md
python tools/learn.py brief --explain "Pd nanoparticles decompose first." \
    --research-type surface_science --section title_abstract
python tools/learn.py budgets --research-type surface_science
python tools/learn.py stats
python tests/learn.py                        # 155 checks, offline
```

---

# graphic_figure.py

PowerPoint panel art inside a figure folder. Some panels are not plots — a
reaction scheme, an apparatus diagram, a mechanism, a sample-prep flow — and
the honest answer is not a drawing API but the editing surface the user already
has. A graphic panel is **one PowerPoint slide**, rendered to `.png` at the
journal's pixel size and composed by the same script that draws the plotted
panels beside it.

Standard library only. Spec: `specs/setup-project-directory.md` §5.6.
Re-measure: `python tests/graphic_figure.py` (40 checks, offline) or `--render`
(50, drives real PowerPoint and real R).

## Commands

```bash
python tools/graphic_figure.py status "<project>" [--json]
python tools/graphic_figure.py add    "<project>" --figure 2 [--panel A]
                                      [--width single|double|half|<inches>]
                                      [--aspect 0.75] [--supplementary] [--dry-run]
python tools/graphic_figure.py render "<project>" [--figure 2] [--force]
```

`status` lists every float folder holding panel art, with each source reported
as current, stale, or not rendered, plus the render backend this machine
actually has. `add` creates one `.pptx` and the `panels.R` that renders it.
`render` drives the render without a full pipeline run — `Rscript
plan/render_all.R` does the same thing as part of the normal loop.

## The layout

```
plan/figures/Fig02/
    panels.R              GENERATED  renders every *_source.pptx
    figure.R              yours      composes the panels
    panelA_source.pptx    YOURS      open it in PowerPoint
    panelA_source.png     GENERATED
    .render_state.json    GENERATED  the SHA-256 that answers "stale?"
    figure.png / .pdf     GENERATED  by save_float()
```

A figure that is one whole graphic uses `figure_source.pptx` instead. The two
shapes do not mix and the engine refuses to mix them: a folder holding both
renders one of them into nothing.

## Rules that are not preferences

- **The `.pptx` is written once and never overwritten.** It is the file you
  edit, and it is the only thing in the folder nothing else can reproduce.
  `add` refuses rather than regenerating; deleting it is your call.
- **Staleness is the `.pptx`'s SHA-256, never its mtime.** OneDrive rewrites
  modification times on sync. `tools::sha256sum` is base R and produces the
  same digest as Python's `hashlib`, so the R side and this side cannot
  disagree about what is stale.
- **Three backends, then stop and say so** — PowerPoint COM, LibreOffice
  headless, then a refusal naming the manual export. A render that silently
  produces nothing, or quietly produces the wrong size, is worse than one that
  asks for thirty seconds of work.
- **The pixel size is not decided here.** DPI resolves through
  `theme_journal.R` and a sourced `journal_target.yml`. This engine fixes the
  slide's *inches* — which is what fixes the aspect ratio the composed figure
  preserves — and `status` reports the pixel size the render actually produced.

## Mixing graphics and plots

Nothing has to exist yet. `image_panel()` draws a dashed grey placeholder for
art still being made, so a figure composes and renders from day one.

```r
p_a <- graphic_panel("panelA_source.png", title = "Reaction scheme", tag = "A")
p_b <- image_panel(float_file("micrograph.png"), title = "Film", tag = "B")
p_c <- panel_title(ggplot(d, aes(temp_c, yield_pct)) + geom_point(),
                   title = "Yield", tag = "C")

save_float((p_a | p_b) / p_c, height = 5.2)
```

`graphic_panel()` rather than a bare `image_panel()` when the panel has a
title: an aspect-locked image ignores the height patchwork hands it, so a title
drawn *inside* the panel floats over its neighbour. And state `height =` as
soon as the layout has more than one row — `save_float()` derives a height from
the panel *count* and assumes a single row.

## Examples

```bash
# a scheme as panel A of Figure 2
python tools/graphic_figure.py add "Projects/my_paper" --figure 2 --panel A

# a figure that is one whole graphic, full width
python tools/graphic_figure.py add "Projects/my_paper" --figure 3 --width double

# what needs re-rendering
python tools/graphic_figure.py status "Projects/my_paper"
```

# install_skills.py

Makes the skills findable from any directory. Everything else in this toolkit
is about doing the work; this is about the agent knowing the work has rules.

A harness that indexes skills reads them from its own personal directory, so a
session started in the paper's own folder never sees `writing-engine` — and
that failure is silent, because an agent that never heard of a skill does not
report one missing. It just writes the paper.

## Commands

| Command | What it does |
|---|---|
| `install` | write a pointer per skill, or bring the pointers up to date |
| `status` | installed / current / stale / missing / not ours; exit 1 if anything needs installing |
| `uninstall` | remove the pointers this tool wrote, and nothing else |

`--dest DIR` overrides the destination (the default is one harness's personal
skills directory). `--dry-run` on `install` and `uninstall` reports and writes
nothing. `--force` on `install` overwrites a same-named skill this tool did not
write.

## Pointers, not copies

Each installed file is the source skill's YAML frontmatter copied verbatim,
followed by a few lines naming the real `SKILL.md`, the absolute toolkit root,
and an instruction not to change directory.

The frontmatter is duplicated because that is what a harness reads to decide a
skill is relevant. Nothing else is: a copied body would be a second source of
truth, and the drifted copy is the one that gets read. Editing a skill
therefore needs no re-install — only moving the toolkit or changing a
`description:` does, and `status` says so.

## What it will not do

A file without this tool's marker comment was written by someone else. `install`
refuses it and exits 2 rather than overwriting; `uninstall` leaves it alone and
removes only files the tool wrote. `uninstall` deletes from the user's home
directory, so what it may delete is decided by something the tool wrote itself,
never by a name it recognises.

## Examples

```bash
python tools/install_skills.py install --dry-run
python tools/install_skills.py install
python tools/install_skills.py status --json
python tools/install_skills.py install --dest ~/.config/agent/skills
python tools/install_skills.py uninstall
python tests/install.py                      # 58 checks, offline
```
