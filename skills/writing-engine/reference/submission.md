# The Submission Stage

Building the document and holding it to the journal's own rules.

Part of the `writing-engine` skill. **`SKILL.md` is the spine and is read on
every invocation; this file is read when the round reaches this stage**, and
`manuscript.py handoff --stage submission` is what names it. Everything the spine
says still applies here - the `<tools>` resolution, the module isolation
contract, and **Things that are wrong to do here** are stated there once and
are not repeated in this file.

A `§N` in this file is a module number. `SKILL.md`'s **What to read, and
when** table says which file each module's section is in; the others beside
this one are `drafting.md`, `outline.md`, `prose-rules.md`, `revision.md`.

---

## Journal requirements — the most tempting thing here to invent

Runs when a journal folder is created, and on `--refresh-requirements`. Web
search and fetch the journal's author guidelines, then fill
`journal_requirements/requirements.yml` and write the prose version alongside it.

**Every field is either sourced with its URL, or left `unknown`.** No inference
from a similar journal. No plausible margin. Guidelines are formulaic, which is
exactly why an invented value reads as correct. Unknown fields go at the top of
`requirements.md` under **"Could not determine — verify before submission"**,
and `assemble` reports how many are still unknown on every build.

If the guidelines are behind a login or an unfetchable PDF, say so and ask the
user to paste the relevant section. Do not proceed on a guess.

**Read every source you list all the way through, or say which part you did
not.** A checklist read to its halfway point is a rule set with its second
half missing, and nothing downstream re-reads it. Any source you could not
finish goes in `capture.partly_read:` — one line each, naming what was not
read — and `submission-package` keeps the package **NOT SENDABLE** while that
list is non-empty. Checklists are where the rules that decide the build tend
to sit: what anonymization removes (institutions and **ethics board
identifiers**), what form the supplement takes, and what it may contain.

**Five fields the build now reads, each sourced or `unknown`:**

- `text.page_break_before` — a list of section names, `every_section`, or
  `none`. `unknown` is **not** `none`: it puts a break before every top-level
  section of the manuscript and the supplement, and `requirements.md` gets an
  *Engine Defaults Applied* block saying so.
- `text.first_line_indent` — `none` or a length (`0.5in`, `1.27cm`). `unknown`
  indents BodyText 0.5 in and nothing else, and says so in the same block. An
  indent claim from anywhere but a journal page is not a source: record what
  you checked and leave it `unknown`.
- `supplementary.file_format` — `pdf`, `docx` or `either`. `pdf` makes the
  build write `supplementary_info_rN.pdf` beside the `.docx`.
- `supplementary.large_tables` — `separate` exports each large supplementary
  table as a `.csv` beside it.
- `supplementary.content_restrictions` — the journal's words, verbatim, on
  what a supplement may not hold. Module 1e reads the supplement against it.

**`references.style_name` and `style.csl` must agree.** `assemble` reads the
citation form the CSL renders — superscript, bracket or parenthesis, numeric
or author-date — and compares it with the form the style name implies. A
community CSL for a journal can render `(1)` where the journal's own guide says
AMA, which is superscript. A mismatch, or no `style.csl` for a named style, is
reported on every build and keeps the package NOT SENDABLE; the build names the
canonical CSL for the style.

When the figure geometry is known, write it into
`plan/theme/journal_target.yml` — `theme_journal.R` prefers that file over its
preset table, so the figures re-render at the journal's real column widths.
Leave a field blank there rather than guessing; a blank keeps the preset.

**Three figure fields decide what gets uploaded, and all three are easy to
walk past.** `figures.placement` (`embedded` or `separate`),
`figures.file_naming` — the journal's own example of an upload's file name,
copied verbatim, `Figure 1.tif` — and `figures.file_format`. `manuscript.py
figure-files` checks the uploads against those and, per §4.3, checks nothing
when they are `unknown`: `plan/figures/Fig02_symmetry/figure.png` is the right
name in the project and possibly the wrong one in the portal, and only the
guidelines page settles it. Copy the pattern; do not normalise it.

**Look for the publisher's own Word template while you are in the guidelines,
and record its URL.** Where one exists it is a better source than any value you
could capture, because it is the file the editorial office expects the styles
to come from. Most publishers do not let a script download it — ACS's link
sits behind a challenge page that returns 403 to anything but a browser — so
the honest move is to record the link, tell the user where to drop the file,
and carry on generating `reference.docx` in the meantime. **Never say a
template was used when it was not.**

**Two fields in `text:` decide what the document looks like, and both are
easy to get wrong in the same direction.**

- `line_numbers` — leave `unknown` unless the guidelines actually say so. It
  is tempting to record `continuous` as a conservative default because
  reviewers like line numbers, and that is how a *Langmuir* submission ended
  up carrying them: neither the guidelines nor the checklist mentions them,
  and the group's own accepted *Langmuir* manuscript has none. An unsourced
  value that changes what the editor receives is not a conservative one. The
  build now leaves them off unless the field is sourced.
- `font_family` / `font_size_pt` / `line_spacing` / `margins_in` — `unknown`
  is fine and common. `reference-doc` falls back to Times New Roman 12 pt,
  double, 1 in. and says on every build which values were defaults.

**Capture the graphical abstract's rules while you are there**, as a
`toc_graphic:` block — whether one is required, **what this journal calls it**,
its size in inches, DPI by color mode, accepted formats, font family and minimum
point size, any required label text, where it goes, whether it may repeat a
manuscript figure, whether it must be original unpublished artwork, and what
content is prohibited. Module 10 does nothing at all without these, and refuses
to scaffold on `required: unknown` rather than create a folder the user would
then trust. Several publishers put these in a **separate** document from the
main author guidelines — ACS's is `toc_abstract_graphics_guidelines.pdf` — so
the main page not mentioning a graphical abstract is not evidence that none is
wanted.

## 2. evidence-check

Per claim: is it supported by (a) a cited paper, (b) a float in this project,
(c) the stats output, or (d) nothing? **(d) is the finding.**

Distinguish *unsupported* from *reasonable framing* — an introduction sentence
setting up context is not a claim needing a p-value; a mechanistic assertion is.

**Denied `plan/README.md`'s narrative section**, so it cannot rationalize a
claim from the story we wanted to tell. Read the outline as the ledger rather
than re-deriving claims from finished prose: you are checking a *stated* intent.

## 3. stats-check — the blind one

**This agent's context contains only:** the section text,
`data/analysis/analysis.md` and `data/data_contract.md`. It is **not** given
`plan/README.md`,
`plan/outline.md`, the hypothesis, or the caption claim lines.

An agent that knows what we hoped to find reads an ambiguous result as
favourable. Withholding the hypothesis is what makes its verdict worth having,
and **nothing visibly fails if you leak it** — so build the context from the
list above and nothing else.

**"Its own context" means a cold start, not a fresh heading.** The engine
builds this one for you — `agent-brief --module stats-check` emits the prompt
with the four denied inputs left out, and `tests/manuscript.py` asserts that
none of them can reach the module through its given list or its prompt text.
What the engine cannot see is what *you* add on top of it. Three things all
count as leaking, and the third is the easy one to do by accident:

- summarising the project for the module before handing it the files;
- letting it inherit this conversation, which by then contains the hypothesis,
  the outline and every claim the draft is trying to make;
- **quoting your own earlier reasoning back to it** — "the expectation is that
  the low-salt condition is the best" is the hypothesis, however it is phrased.

Hand over the brief's `prompt` and nothing else — no preamble, no summary,
and never `subagent_type: "fork"`. In any harness, set how much of this
conversation the child inherits to none, by name, as the brief's `spawn` field
says, and never rely on the default. If the harness cannot spawn a sub-agent
with a chosen context, run it as a separate invocation with that prompt; if
that is not possible either, skip it and say the check did not run. A
`stats_report.md` written by an agent that has read the hypothesis is worse
than none, because it will be trusted.

What it looks for:

- Numbers in the text that do not match the output (`prose.py numbers` finds
  the mismatches; judging them is this module's).
- Significance claimed from a non-significant test; "trend toward" on p = 0.4.
- A test reported without its n, or with the wrong n for that comparison.
- Multiple comparisons reported without the correction the output shows.
- Effect sizes omitted where the output has them; "clinically meaningful"
  carrying a large p-value with no effect size.
- Directional language on a difference whose CI crosses zero.
- **Asymmetric hedging** — favourable results stated flatly, unfavourable ones
  hedged. It can see the asymmetry precisely because it does not know which
  direction was hoped for.

Output a table of `location | text as written | what the stats say | severity`,
severity one of `error` (contradicts the output), `overstatement`,
`imprecision`, `omission`.

## 3r. attribution-check — the blind one, on every paper kind

**Runs on every paper kind as of 2026-09-20, and BESIDE `stats-check` rather
than instead of it** (`specs/citation-integrity-2026-09-20.md` §6.2, decided
by the user). It used to be review-only, and that gate is what let a member's
paper ship with citation errors an outside reader then found: a research
paper's Introduction and Discussion are made of cited claims, and the only
check that reached them was evidence-check, which asks whether a citation is
*present* — precisely the check that mistakes presence for support.

The two passes answer different questions and neither substitutes for the
other. `stats-check` asks whether **this project's** numbers are stated
correctly; this one asks whether **somebody else's paper** says what this
sentence claims it says. On a review `stats-check` still goes out of scope
with `analysis`, because a review has no statistics of its own.

**Scoped by section, not by paper kind.** It reads the sections whose
sentences are about other people's work — Introduction, Discussion, and every
body section a review declares. Results and Methods cite this project's own
data and are `stats-check`'s ground; running an attribution pass over them
spends network calls proving something no outside source can answer. The
scope is a property of what a section *is*, which is why it survives a paper
kind nobody has invented yet, and `agent-brief` enumerates the sections for
you rather than leaving it to be remembered.

Same argument as `stats-check`, transplanted, and with a stronger case behind
it: a research paper has one hypothesis and a handful of tests, while **a
review has a claim in nearly every sentence**, so the same contamination
operates a hundred times per section and never once announces itself.

### It says when it could not run — read this before reading the table

**A pass with no retrieved text for any pair DID NOT RUN, and says so in a
different sentence from a pass that found nothing wrong.** This is item 80's
rule applied to a module instead of to a subprocess, and it is the single
line that would have changed the outcome above.

`agent-brief --module attribution-check` carries the answer before the agent
is ever spawned — `inputs.header`, `inputs.did_not_run`, and the same line in
`plan`:

```
DID NOT RUN  attribution-check - no retrieved source text for any of 34
             citations. `data/corpus/papers/` holds 0 records. Run
             `scholar.py corpus-fill "<project>"` or the review corpus pass.
```

When that line is non-empty: **print it, print no table, and record the
module as `skipped`** — `run --step attribution-check --state skipped`. It
must not appear among the modules that ran. On a partial run the report's
**first line** is *n checked, m not retrieved, of N pairs*; a module that
checked 3 of 34 says so there, not in the last column of a forty-row table.

### Giving it something to read on a folder nobody scaffolded

The pass reads `data/corpus/papers/<citekey>.md`, and until 2026-09-20 the
only writer of those files was `review.py` — so a paper written to somebody
else's template, in a folder this engine never scaffolded, had no input at
all. That is the ordinary way this toolkit meets a new user, not an edge case.

```bash
python <tools>/scholar.py corpus-fill "<project>" --json
```

One record per bibliography entry that does not have one, in the format
`review.py` writes, at the tier retrieval actually reached: full text where
PMC has it, the abstract as the floor, `title` where nothing was retrievable.
It **refuses on a project that carries `data/corpus/protocol.md`** and says
why — a systematic review's tiers are a claim about a screening procedure,
and a convenience fetch must not be able to raise one.

An abstract-tier record is the normal outcome in half of this user's field,
not a failure: measured on a real chemistry topic, zero of 25 candidates
carried a PMC identifier. The record's own `## Limits of this record` section
is what stops the checking agent treating an abstract as a paper.

**This agent's context contains only:** one paragraph of the draft, and for
each citekey in it the retrieved text held in that paper's record —
`data/corpus/papers/<citekey>.md` — including the `read_tier:` line and the
`## Limits of this record` section the engine wrote from it. It is **denied**
what the paper argues: `plan/README.md`, `plan/outline.md`,
`source_text/title_abstract.md`, `data/corpus/protocol.md`,
`data/corpus/synthesis.md`, the rest of the draft, every report, and this
conversation.

**The thesis is the load-bearing denial.** An agent that knows what the paper
argues reads an ambiguous abstract as agreeing with it, and it does so
sincerely. On a research paper the hypothesis is the thesis, and the denial
is the same one `stats-check` runs on.

Note that `data/corpus/` is never given whole — only `papers/` inside it.
A given *directory* quietly reverses a denial, which is exactly how
`data_dir` once reversed `stats-check`'s one load-bearing denial, and why it
no longer exists.

Verdicts, per (sentence, source):

| verdict | means |
|---|---|
| `supports` | the retrieved text says this |
| `partially` | it says something narrower, or with conditions the sentence drops |
| `does not` | it does not say this |
| `not retrieved` | there is no retrieved text — the record is `title` tier, or empty |
| **`out of tier`** | the sentence may well be true, and the record does not license it |
| **`contradicts`** | the source states the **opposite**, or states the same thing of a **different entity** |

`out of tier` is the finding no other module in this toolkit can produce, and
it is the reason the module exists rather than being a citation checker with
better manners.

**`contradicts` is separated from `does not` on purpose.** *The source is
silent* and *the source disagrees* are opposite problems for a writer and were
the same row in this table until 2026-09-20.

**Entity substitution is never support.** A term appearing in the source does
not count as support when the source attributes it to a different protein,
receptor, cell line, material or condition than the sentence does. The
measured shape: a claim about A, cited to a paper that demonstrates the
property for B and merely *names* A. That is `contradicts` when the source
assigns the property elsewhere, and **the line you quote must be the one that
assigns it**.

### A `contradicts` verdict is OFFERED as a flag — it never writes one

`NOT SENDABLE` counts `**[FLAG: ...]**` markers and nothing else, so a verdict
that never touches the draft can never block — and this module reports and
never rewrites, which is not up for revision. So the skill lists the
`contradicts` verdicts, one line each **with the quoted source line**, and
asks:

> `@zhao2015` — the sentence says CMG2 binds collagen IV; the source shows it
> for TEM8 and names CMG2 once, in the introduction. Write a
> `**[FLAG: citation-claim]**` at that sentence?

On a yes, the flag goes in at the sentence, where `flag-resolver` already
knows how to walk it and `submission-package` already knows how to refuse to
send it. On a no it stays in the report and the package goes. **The offer is
the user's decision made once per pair, not a dial**, and nothing else in
`specs/citation-integrity-2026-09-20.md` blocks a build.

**Scale, honestly.** Two hundred references across ten sections is not one
agent call. The pass batches by paragraph and records each as a `run --item`,
so an interrupted pass resumes on the existing ledger.

```yaml
attribution_check: sample     # off | sample | full
```

`sample` at r1, `full` in the `submission` preset. **A sample is stated, and
its selection is deterministic** — how many sentences, chosen how, out of how
many — because *checked* and *spot-checked* are different claims about a
paper, and a sample chosen by the checker is a sample nobody can reproduce.

It reports and never rewrites. A wrong attribution is fixed in the source text
by a person who decides whether the sentence or the citation was wrong.

The cheap front end is `review.py tier`, which is deterministic and costs
nothing.

**What each of the three passes proves, in one sentence each — say which is
which in the report, because they are read as one number otherwise:**

| pass | what it proves | what it cannot see |
|---|---|---|
| `scholar.py check-refs` | the **entry** names a real indexed paper, and whether its byline, year and kind match the record | the sentence the citation is attached to |
| `review.py tier` | the retrieved record **licenses** a sentence of this specificity | whether the record actually says it |
| **attribution-check** | the source **says this**, per (sentence, source) | anything about the paper's own numbers — that is `stats-check` |

`check-refs` is the one that grew teeth on 2026-09-20: a byline is compared as
an ordered **list** rather than as a set intersection, so a fabricated author
and a dropped first author are named instead of carried by one correct
surname, and a year that is one out is a **note** instead of silence. A
`summary` carrying `byline_differences` or `year_notes` is not a clean
reference list, whatever the `clean` count says — those entries are no longer
counted in it.

```bash
python <tools>/review.py tier "<project>" --json
```

## 4. assemble

```bash
python <tools>/manuscript.py assemble "<project>" --journal IJROBP \
    --preset coauthor
```

`--preset` is only recorded, in the run history the engine keeps for itself
(below); the build does not change with it. Pass the preset this run is
actually part of.

The only module that produces Word. It carries four gates, and all four are
there because the failure they catch is silent:

0. **A section the journal names with no file behind it.**
   `structure.section_order` is the journal's own vocabulary — *Experimental
   Section*, *Results and Discussion*, *Acknowledgment* — and `assemble`
   resolves each name onto the scaffold's files. A name that resolves to
   nothing takes its section out of the document, and the build otherwise
   succeeds: measured on the real *Langmuir* order, nine of ten names resolved
   to no file and a valid `.docx` came out holding the introduction alone at
   exit code 0. Refused now, whether it is one section or all of them. Fix
   `structure.section_order`, or write the file it names.
1. **Mock data.** If any float's provenance says mock, it refuses unless
   `--allow-mock`, and then stamps the filename `_MOCK`. A mock-data draft must
   never be mistakable for a real one once it leaves the folder. **This is not
   an error to work around.**
2. **Unresolved citekeys.** citeproc renders an unresolved key into the document
   and warns only on stderr — that is a citekey reaching an editor. Any
   "citation not found" warning fails the build and the partial output is
   deleted.
3. **Citekey residue in the rendered text.** Measured, not assumed: a key inside
   a code span reaches the `.docx` with *no* citeproc warning at all. Gate 3
   catches what gate 2 cannot.

Every build also writes `drafts/references.ris` — the EndNote escape hatch.

Report its warnings to the user rather than swallowing them. A missing
`style.csl` means the citations are not in the journal's style.

**And it keeps the toolkit's own defect list.** A build that notices something
about *the engine* — a heading it had to supply because the section order was
written in slugs, a citekey that got as far as gate 3, a round the marker
never opened — appends it to `system-changes.md` at the top of the toolkit and
prints one line saying so:

```
  logged   1 engine issue logged to system-changes.md: 1 new (item 8).
           That is the system to fix, not this draft.
```

Pass that line on to the user as what it is: a problem with the tooling, to be
looked at later, and **not** something for them to fix in the draft. It is
printed whether the build succeeded or refused, because every item in that
file so far came out of a run that exited 0 and looked finished. A second
build that sees the same thing says *seen again* and adds nothing.

Only engine misbehavior goes there. A missing float, an empty `authors.md`,
58 outstanding items are the draft's problems and are already reported where
they belong. If **you** notice an engine fault during a run — something the
engine did that it cannot justify, not something the paper needs — file it in
the same shape:

```bash
python <tools>/manuscript.py log-issue "<project>" --stage writing \
    --journal IJROBP --round 1 \
    --code a_short_slug --detail "what happened, GENERICALLY" \
    --example "what happened on THIS project" \
    --title "one line" --wanted "what it should do" \
    --verify "how the next run answers 'is it still there?'"
```

**`--stage` is not optional in practice, even though argparse allows it to
be.** Unstated, the record infers `writing` from the journal and round — which
is right here and wrong everywhere else, and an inferred value is marked as
inferred in the record so a maintainer can tell the counted ones from the
guessed ones. Every skill files with its own stage; this one is `writing`.

**`--detail` is generic and `--example` is where the specifics go, and this is
enforced rather than advised.** `system-changes.md` is read by everyone who has
the toolkit — so an item's description has to be a statement about the
*engine*, true of every project it could happen on. (Until 2026-09-21 a
redacted copy of every item was also pushed to a reports repository. That
machinery is gone; the rule is not, because the file itself still travels with
the toolkit.) Write *"the drafter dropped a row from the
table"*, not *"the drafter dropped sample ZQx7"*.

A sample id, a composition or an oxidation state in `--detail`, `--title`,
`--wanted` or `--verify` is **refused**, and the refusal names the field and
quotes the token. Put it in `--example` instead: that field is never linted,
which is exactly what it is for. A measurement or a ratio is reported rather
than refused — those have real false positives, and blocking a defect from
being filed because its explanation contains a number would be worse than the
leak. `--example` is optional and most items do not need one.

`--list-codes` prints the codes the engine already logs by itself; reuse one
if it fits. The `--verify` is refused if it is missing, because an item the
next run cannot check is an item nobody can ever close.

**If you fixed it in the same session, say so with `--fixed`** — on its own to
mark an item already in the file, or beside `--code` to file and mark in one
gesture. `--detail` then says *what you changed*, and it is required: a status
that records a claim with no evidence under it cannot be audited by the next
reader, which is the only thing that file is for. **Never edit
`system-changes.md` by hand to mark something fixed** — one stray blank line in
it stopped every engine write for days once (item 44). And `--fixed` cannot
write `VERIFIED FIXED`, whatever it is asked: a fix moves an item to
`FIX ATTEMPTED` and it stays in the open list until a run of the kind that
exposed it finishes without seeing it.

**What it builds, beyond concatenating the sections.** The title, the byline
and the abstract go to pandoc as metadata, so they come out in `Title`,
`Author` and `Abstract` rather than as the form's own `# Title` label with the
real title as body text under it. Each body section is retitled to the
journal's name for it. The end matter — Supporting Information, Author
Information, Funding, Conflict of Interest, Data Availability,
Acknowledgment — is *generated* from `plan/author_information/authors.md` and
`plan/author_information/affiliations.md` for every statement the
journal's order names or `structure.statement_sections` marks required; one the
journal requires and neither file has anything for becomes a
`**[FLAG: author]**` in the document and a warning. Author Contributions is
written from the contribution matrix and Funding from the grant table — see
*The author files* below. The bibliography is placed
where the order puts it rather than appended after everything.

**It also carries a check that reports rather than refuses.** Every gate above
reads the document's words; `format-check` reads how it is *set* — font, size,
colour, spacing, justification, all four margins, line and page numbering,
whether the title is in `Title`, and whether each section the journal lists is
a level-1 heading in the journal's order. Those findings come back in
`res["format"]` and in the warnings.

Take the errors seriously even though they do not stop the build. They are
what an editor sees first, and they are invisible in every other report: a
manuscript once shipped with 20 pt Aptos Display headings in Word's accent
blue over a Times New Roman body, and it passed every check that existed.

```bash
python <tools>/manuscript.py format-check "<project>" --journal IJROBP
```

**The supplement is built, and it has a source file.** `assemble` writes
`submission/supplementary_info_rN.docx` whenever there is something to build
it from: `<source_text_rN>/supplementary.md` (the supplement's methods,
results and tables, written like any section) and the S-numbered caption
blocks of `live_captions.md`. Each supplementary figure with a render in
`plan/figures/` is embedded above its caption; one whose caption
`supplementary.md` already carries is not added twice, so a supplement
written as one document in order builds as it is. Its citations resolve
against the same `references.bib` and the journal's CSL, into a reference
list of its own, and it carries no author in its file properties.

The S-numbered captions **leave the main manuscript** when a supplement is
built, unless `supplementary.captions_in_main_file` says they stay. They
never leave it when there is nothing to build a supplement from; a supplement
that fails to build says **SUPPLEMENT NOT BUILT**, and that line is the one to
act on before anything is sent. A supplement edited since
the engine wrote it is not overwritten (the manuscript's gate 5, applied to
the second file); `manuscript.py supplementary` rebuilds it alone, for a
round whose manuscript is not being rebuilt. When the text cites a
supplementary float and there is nothing to build from, the build says so.

**The supplement takes the journal's form and the journal's rules.** It gets
the same page breaks and the same plain callouts as the manuscript. When
`supplementary.file_format` is `pdf`, the build also writes
`supplementary_info_rN.pdf` — through LibreOffice, else Word — and that PDF is
what the package lists; when neither can run, it says **SUPPLEMENT PDF DID NOT
RUN** and the package stays NOT SENDABLE. When `supplementary.large_tables` is
`separate`, each table over 25 rows is also exported as a `.csv`. When
`supplementary.content_restrictions` is set, the build names every paragraph
and sentence in the supplement that reads as interpretation or advice, and
module 1e judges them — and says whether an analysis the main text leans on is
*key analysis* the rule keeps out of a supplement. A supplement labelled `S1`
on a journal whose scheme is `E1` is a gap in `completeness`.

### Double-blind review — the build leaves the byline out, on purpose

When `requirements.yml` records `anonymization.required: true`, the build is
**blinded** and says so in one line. What that means concretely:

- **No byline, no affiliations, no corresponding author, and no author in the
  file properties.** All four come from the same pandoc metadata field, which
  is why leaving it out takes the name out of the body AND out of
  `docProps/core.xml` at once. Both matter: the second is the one nobody
  thinks to look at, and Word shows it in the Info pane.
- **The PAPER NOT COMPLETE block is redacted** with the journal's own filler
  from `anonymization.filler`. It is GENERATED, which makes it the part of a
  blinded file nobody proof-reads — it said *"<name> is corresponding and has
  no email"* inside a manuscript built for double-blind review.
- **The byline goes on the separate title page**, which `submission-package`
  writes and which reviewers do not see. `assemble` does not build a second
  one; it names the file and says plainly when it does not exist yet.
- **Self-citations are NOT masked, and the candidates are named.** The build
  summary lists every reference sharing a surname with the byline, and quotes
  the journal's own rule from `references.anonymize_self_citations`. A shared
  surname is not a shared person: masking a stranger's paper destroys a
  citation as surely as leaving one's own unmasked breaks the blinding, so the
  engine names what to look at and stops. **It is deliberately not written
  into the manuscript** — a flag inside a blinded file listing the authors'
  own papers is the disclosure the blinding exists to prevent.
- **A statement in the blinded file names no identifier, and neither does its
  FLAG.** Ethics approval reads *"approved by the XXXX institutional review
  board"*, with the journal's filler for the board, the institution and the
  protocol number. Its FLAG may ask only for non-identifying facts — whether
  consent was waived — never for the protocol number, which is the detail the
  journal requires removed. The build redacts byline names inside statements
  and FLAGs, and `format-check` reports any FLAG that asks for an institution,
  an ethics identifier or a trial ID. **The identifiers go on the title page**,
  which gains an ethics row on a blinded paper for exactly that.
- **A required statement with no slot in the journal's order gets no heading
  of its own.** Ethics goes where it is conventionally stated — the end of the
  first Methods subsection, with the patient cohort. Any other statement the
  order has no place for is named in the build summary and left out: add it to
  `structure.section_order` where the journal puts it, or set its
  `statement_sections` entry to `none` if it belongs on the title page.
  `format-check` reports a top-level heading the order does not list.

**Front matter follows the journal's order.** The running title goes directly
under the title (it is set as the document's subtitle), then the abstract,
unless `structure.section_order` names the abstract separately and puts the
running title after it. Keywords follow the abstract.

**The title page carries the journal's own list.** Every entry in
`structure.title_page_statements` becomes a heading, verbatim, filled from the
author files, marked *carried above* where the byline block already supplies
it, and flagged where neither is true. Nothing is dropped and nothing is
invented: a title page is a checklist an editor screens on, and an element
that is silently absent is the failure worth designing against. Before this,
the page held the same five headings for every journal and the
conflict-of-interest, funding, data-sharing and clinical-trial statements went
into `statements_rN.md`, which no journal asks for.

## An unfinished paper still builds, and says so

```bash
python <tools>/manuscript.py completeness "<project>" --journal IJROBP
```

Nothing in this pipeline refuses to run because the paper is unfinished — an
outline with empty brackets, a section still a stub, a float with no caption, no
statistics yet. Those are the normal state for most of a project, and they are
what you draft *around*.

What must never happen is an unfinished draft leaving the folder **looking**
finished. So the verdict travels with the document instead of gating it: when
prose is missing or a figure is mock data, `assemble` appends a block to the
end of the `.docx` headed **`[FLAG: incomplete — PAPER NOT COMPLETE]`**, naming
those parts and the file to open for each. Everything else outstanding goes to
`reports/rN/outstanding.md`, not into the paper (see *What Goes in the .docx*
below). The block is generated, and it disappears from the build by itself once
no prose is missing.

Say the whole list in your own summary — how many items are outstanding, which
of them only the user can close, and that the full list is in
`outstanding.md`. The exit code is `1` for "not complete
yet", not `2`: this is a normal state, not a refusal.

The real refusals are about integrity, not completeness: a section the journal
names with nothing behind it, mock data, an unresolved citekey, and a flag
reaching `submission-package`.

## How It Reads — Measured on Every Build, and Gating Nothing

Under that verdict the same report carries a second block, answering a
different question: not *is this paper finished* but *does it read well*.
`readability`, `voice` and `metaprose` run over the drafted sections in
reading order, and what they measure comes back in `res["reading"]` — the
findings rolled up by rule with one example and a location each, then a row
per section giving words, sentences, sentence length mean and **sd**, opener
length mean and sd, and the passive share.

It is there because it was not. All three engines were built and tested, and
no build had ever called one of them (item 81) — so every check a build ran
asked whether the paper was *correct*, and none asked whether it was
*readable*, which is the dimension the reader judges first.

**None of it blocks anything, and none of it may.** It never enters the
outstanding list, never moves the verdict, and never reaches the PAPER NOT
COMPLETE block inside the `.docx`. A gate on a prose measurement makes the
drafter optimize the metric, and the sd is the standing example: a change
that lowers mean sentence length while collapsing the spread has made the
prose worse.

So what you do with it is **read it out** — a line or two in the round
summary, naming the rule that fires most and the section whose numbers sit
furthest from the rest. Paragraph openers of 38, 47 and 38 words, against 67%
passive, were what made one real introduction read as three flat starts, and
every one of those numbers was measurable before anybody noticed. If the user
wants the prose changed, that is `revise-prose` (module 1c), which is handed
`voice --json` for this reason.

**A crashed engine says so.** `DID NOT RUN  prose.py <cmd>` is not the same
line as a clean pass, and everything printed under it is a narrower claim
than it looks.

## 5. citation-check — also the standalone entry point

```bash
python <tools>/scholar.py check-refs "<project>/drafts/references.bib" --json
python <tools>/prose.py citekeys "<project>" --max-refs <journal cap> --json
```

The engine proves each entry points at a real indexed paper and surfaces
retractions and expressions of concern. It cascades across PubMed, Crossref,
OpenAlex and Europe PMC, so a chemistry or surface-science bibliography no
longer reports `not_found` for entries that are perfectly real — the summary's
`by_source` says which index answered for each. Check `incomplete` before
reading a `not_found` as a verdict: it counts entries where a source failed. `prose.py citekeys` proves every in-text
key resolves to an entry, every entry is cited at least once, there are no
duplicates, and the count is within the cap.

### The entry can be real and still be wrong — byline, year, and the pair

Three findings that came back `[OK]` at confidence 1.0 with no flag and no
note until 2026-09-20, measured on a real member's bibliography
(`specs/citation-integrity-2026-09-20.md` §2). Read them off `check-refs`:

- **A cited author who is not on the record is a flag**, named:
  `Cited author "X" is not on the record: it lists A, B, C.` The comparison is
  an ordered **list** now, not a set intersection, so one correct surname no
  longer carries a whole byline — which is the signature of generated text.
- **A wrong first author is a flag**, because it is what breaks a reader's
  ability to find the paper at all. An **abbreviated** byline is only a note:
  it is ordinary practice in several styles and must never read as an
  accusation.
- **A year that is one out is a note**, not silence. The tolerance exists to
  absorb electronic-ahead-of-print dating; an ordinary wrong year was being
  swallowed with it, and on the measured bibliography the wrong year is what
  made two entries render as the same citation.

`summary.byline_differences` and `summary.year_notes` count them, and an entry
carrying only notes is **no longer counted in `summary.clean`**. Report those
two numbers whenever either is non-zero — seven quiet notes inside forty
printed records is exactly how these were missed the first time.

**Nothing here is corrected for you and nothing here refuses.** The record can
be the wrong record; the decision stays the user's, exactly as it does for a
database entry or a preprint.

### Two entries that render as the same citation

```
rule: author_year_collision      severity: warning
```

Computed from the **entry fields**, never from the key, so two entries whose
keys differ in every character still collide when both render as
*"Duval et al., 2011"*. A **warning**: in a numbered style it changes
nothing, and the writer may have a style that disambiguates. It names the
citing paragraphs so the cost is visible rather than asserted.

Fix the year first where a year is wrong — correcting it often dissolves the
collision as a side effect, which is exactly why nobody went looking for this
check. **The two defects were hiding each other.**

### Right fact, wrong source

Where the matched record is typed as a **review**, `check-refs` adds a note:
if the sentence attributes a *finding*, the primary source may be wanted
instead; if it attributes a synthesis, a definition or the state of the
field, a review is the right citation. **A note, never a flag and never a
substitution** — only the writer knows which of those the sentence is doing.

With a sentence in hand, `verify --claim "<sentence>"` goes one step further
and names up to three candidates from the review's own reference list whose
titles share the sentence's distinctive terms. Semantic Scholar alone answers
that, its keyless tier is intermittent, and a failure is reported as a failure
— never as "the review cites nothing".

### What kind of source each reference is — report it, never remove it

`check-refs` now classifies every entry before it verifies anything, and a
record that is not a paper **never reaches the cascade**. Read three keys:

- **`status: "not_a_paper"`** — a database record, a dataset, a book, a
  thesis. It was not sent to any index, because no literature index answers
  questions about it. This is not `not_found` and must never be reported as
  one: `not_found` is the word this engine reserves for a citation that may be
  invented.
- **`source_class`** — `{class, peer_reviewed, why, evidence}`. `peer_reviewed`
  is `true`, `false` or **`null`**, and `null` means *the entry does not say*,
  which is a different claim from *it was not peer reviewed*. Do not collapse
  them when you write the report.
- **`summary.non_peer_reviewed` / `summary.by_class`** — the counts.

**Say what each one is and stop there.** A review citing a UniProt accession
and a methods section citing a PDB entry are both doing their job. The report
line is *"one reference is a database record rather than a peer-reviewed
paper — `uniprot2026antxr2`, UniProtKB P58335. That may be exactly what you
meant."* Removing it is the user's decision and is never proposed by default.

`scholar.py source-types "<project>/drafts/references.bib" --json` is the same
classification alone, offline and instant, when the kinds are all you want.

### Later papers that may disagree — signals, not verdicts

**On a submission round, and on any round where the user asks.** A retraction
is the publisher withdrawing a paper, and the cascade has always caught it. A
*contradiction* is a later paper disagreeing, and no index has a field for it:

```bash
python <tools>/scholar.py disputed <DOI> --limit 50 --json
```

It reads the titles of the citing papers and matches them against a tiered
phrase list — `asserted` for a title that exists to argue (*Comment on …*,
*Reply to …*, *Failure to replicate …*), `contextual` for one that might
(*revisited*, *re-evaluation*). It costs one network call per reference, so run
it over the references the paper's argument actually rests on rather than over
all 48.

**Three rules, and the whole design is in them:**

- **Report it under a heading that says what it is**: *"Later papers that may
  disagree — signals, not verdicts."* Never write a sentence that reads as
  *this citation is wrong*.
- **`signals: []` is not a clean bill of health.** It means nothing in the
  titles that were read said so. Say that, in those words.
- **It ships uncalibrated** (`"calibrated": false`), the same way `review.py
  echo` does. A hit is a paper to go and read.

If a signal turns out to be real — the citing paper genuinely overturns what
the manuscript claims — that is a `**[FLAG: contested-citation]**` in the
sentence that leans on it, for the user to resolve. It is not a deletion.

Every uncited entry gets resolved rather than noted: `manuscript.py bib
--prune --dry-run`, show the list, and prune once the user agrees. See
"Every reference has to be cited".

Denied the manuscript's argument entirely — it sees citations and metadata.

**This module is not skippable on a submission run.** It is the mandatory
reconciliation pass, and `plan` will add it back if you drop it.

**And it is never offered on any run.** It is in `draft`, `coauthor`,
`submission` and `revision` already. Run it, and say nothing unless there is
something to act on — see "Never offer citation-check as a choice" above.

## 6. reviewer-check

**Ask how hard to be. Every run, not once.** The user asked for this
explicitly on 2026-09-03 and the complaint behind it was that they were never
asked at all: `reviewer_check` in `writing_config.yml` starts at `light` from
the scaffold, and a default nobody chose is not the user's answer. It is also
the dial most worth asking about, because the two settings do genuinely
different jobs and one of them is uncomfortable on purpose.

Put it in the same breath as the plan line, with the level you are proposing
already chosen — this is a confirmation, not an interview:

> Running **coauthor** on LANGMUIR r2 … → reviewer-check at **light**.
> light = the journal's own rules: word counts, caption lengths, figure
> resolution and type size, missing statements, reference style. Everything it
> finds is fixable.
> heavy = an adversarial referee report, written the way you will actually
> receive one — is the control adequate, does the discussion overreach, would
> I ask for another experiment.
> Say **heavy** and I'll run that instead. Go?

**The default you propose comes from the preset, not from the dial:**

- `draft`, `coauthor` — **light**. A draft a coauthor is about to read should
  have been held to the journal's rules first, and that is the cheap half.
- `submission`, and any round before a PI or a journal sees it — **heavy**.
  This is the last point at which an adversarial read is still free.

The recorded dial is what you offer; the user's answer for this run wins over
it. If they change the standing preference rather than just this round, record
it:

```bash
python <tools>/manuscript.py config "<project>" --journal LANGMUIR --set reviewer_check=heavy
```

**`off` takes it out of the plan entirely** rather than running it so it can
decline — so if the user wants it back for one round, `--add reviewer-check`.
`draft` does not include it either; if the user is expecting a reviewer's read
on a draft build, say so and offer `--add reviewer-check` rather than letting
them assume it ran.

**Its context is the manuscript, and should be nothing else.** A referee gets
the paper and the journal's rules — not the outline, not `plan/README.md`, not
the hypothesis, and not a summary of this conversation. That matters most for
`heavy`, where the whole question is whether the argument stands up to someone
who has not been living inside it. If the harness cannot give the module its
own context, run it as a separate invocation; if that is not possible either,
skip it and say so rather than running an adversarial read that has already
been told the answer. Same rule as `stats-check`, and it fails the same silent
way: a reviewer report written by an agent that knew the hypothesis reads
exactly like one that did not.

**Report everything it can see, and say what it could not.** The report ends
with what was *not* checkable and why — no floats in the project, so nothing
about figure resolution or axis type size; no stats output, so nothing about
the numbers; captions absent, so no caption lengths. A short report on a thin
project is correct; a short report that does not say why is indistinguishable
from a clean one.

**Light** — the mechanical reviewer, reading the built `.docx` against
`requirements.yml`: word counts by section, caption length, figure resolution
and dimensions, reference style, missing required statements, table format. Most
of the fix list is auto-applicable.

**Two float checks belong in this report every time, even though `final-check`
runs them again.** A referee's first act is to look for Figure 1 in the text,
and a submission portal's first act is to reject a file called `figure.png`.
Neither is caught by reading the `.docx` alone, so run both engines and paste
what they say:

```bash
python <tools>/prose.py crossrefs "<project>" --style <figures.citation_style> --table-style <tables.citation_style>
python <tools>/manuscript.py figure-files "<project>" --journal LANGMUIR
```

- **Every float and panel called out, in order.** `crossrefs` reports a float
  no sentence points at, a panel the legend defines and nobody cites, a panel
  the text cites and the legend never defines, a float first mentioned out of
  numeric order, and a gap in the numbering. `captions` adds the two that face
  the other way — a caption nothing in the source text refers to, and a
  sentence pointing at a float that has no caption block. All five are the
  reviewer's own reading, done mechanically.
- **The figure files.** `figure-files` reports a caption for a figure nobody
  drew, a float folder nobody captioned, a folder that rendered nothing, and —
  when `figures.placement: separate` — an upload missing from
  `{JOURNAL}/submission/figures/`, named this project's way rather than the
  journal's (`figures.file_naming`), or in a file type the journal does not
  take (`figures.file_format`). On a project whose floats are managed
  elsewhere it still checks the uploads, against the figures the text cites:
  somebody else's float set is still this paper's upload. The naming and format halves
  are checked only when those fields are sourced, and the report **says so
  when they are not**: `Fig02_symmetry.png` is the right name in
  `plan/figures/` and possibly the wrong one in an upload form, and nothing
  but the journal's own page can settle which.

**Type size inside a figure is the one it must never skip.** It is the most
commonly failed rule on a submitted figure and it is countable, so it is a
finding with a number rather than an impression: measure the rendered image at
the width the journal will print it (`figures.width_single_in` /
`width_double_in`) and report every axis label, tick, panel letter and legend
entry below the journal's floor, with the point size it works out to. Where
the journal states no floor, say which one you used and that it is not
sourced — ACS's own graphical-abstract guidance says 6 pt absolute minimum and
8 pt preferred, which is a reasonable reference point to name out loud but is
not *Langmuir*'s figure rule until somebody reads it there.

Anything inside a figure's own raster — type too small at print width, an
unreadable panel letter, a resolution below `dpi_raster` — is flagged as
**needs the figure script edited**, named by the `.R` script that draws it. No
amount of docx surgery fixes it, and a finding that does not name the script
is a finding nobody can act on.

**Heavy** — the adversarial reviewer. Reads the whole manuscript as a skeptical
referee: is the control adequate, does the discussion overreach, is there an
obvious confounder, would I ask for another experiment, is the novelty claim
defensible against the cited literature. Writes a referee report in the form the
user will eventually receive one. Worth running before the PI sees it, and
expensive enough that it should never be a default.

## 7. final-check

Verify the run honoured `writing_config.yml` — outline adherence at the
requested level, number density within budget, voice and hedging as configured,
custom instructions applied. Reconcile the other reports into one list of what
changed and what is still open. Then the two enforcement duties:

- **Re-verify every `applied` item in `edits_status.md` is still present.** Not
  just the ones from last round — every one, every round. If a rewrite would
  remove or reverse one, that is a conflict and it stops.
- **The caption/text pair check.** A figure asserting a difference while the
  text reports a null result is what a reviewer finds first. Disagreement is an
  error, not a warning.
- **The float and panel callouts.** `prose.py crossrefs` with the journal's
  callout style: every float and panel called out, in the form the journal
  wants, in the order they are numbered. This is the last point at which
  renumbering is cheap.
- **The figure files.** `manuscript.py figure-files --journal X`: every
  captioned figure drawn and rendered, every drawn figure captioned, and every
  upload named and formatted the way the journal asked. `reviewer-check` ran
  this too; it is here as well because `reviewer-check` can be dialled `off`
  and this step cannot, and because a figure renumbered since then has moved
  its file.
- **The length budget.** `prose.py length` against the sourced limits. If the
  paper is over and `length_policy` is still `ask`, this is where you ask.
- **The abstract's named claim.** `prose.py claim` — its content words are in
  the sections it cites, and the abstract carries a sentence bearing it. Both
  mechanical; a finding, not a failure.
- **The overrides.** Read `reports/rN/overrides.md`. Every layer-4 rule that
  yielded should have a row with a reason, and a density finding on a
  paragraph with a row is advisory rather than a warning. Repeated overrides
  of the same rule in the same research type are a rule candidate, not an
  annoyance — say so.
- **The two discretion lists.** `reports/rN/prose_report.md` must carry both
  Changed and Left alone; `reports/rN/learned_rules.md` must carry both
  Applied and Waived. **An empty "Left alone" list on a long section is
  suspect**, and so is a section with several learned rules in scope, all
  applied and none waived — that is the signature of a checklist rather than a
  judgment. Say so in the summary; it is a report, not a gate.
- **The before/after `voice` diff** per revised section — the first time this
  toolkit can show that a prose change happened at all. Watch the standard
  deviation as closely as the mean: a pass that lowered mean sentence length
  while collapsing the spread has flattened the prose, which is worse than
  what the drafter wrote. **This covers every rewriting pass in the round,
  not only 1c** — name `revise-prose --from-comprehension` (3.6) and
  `--from-quality` (3.64) as their own rows where they ran. 3.6 runs
  automatically, so this diff is the only place its effect is visible, and
  a 3.6 row that collapsed the spread is the flattening ratchet actually
  happening — say so plainly.

## 8. submission-package

Everything that is not the manuscript, **all of it generated from the two
author files plus `requirements.yml`** — never re-asked, never retyped: title
page in the journal's exact format, cover letter, CRediT contributions in the
form the journal wants, COI/funding/ethics/data-availability statements,
suggested reviewers with conflicts noted, and the final checklist.

### The Figure Files, When the Journal Wants Them Separately

On a `figures.placement: separate` journal the package also writes **every
rendered figure, main and supplementary, into `{JOURNAL}/submission/figures/`**
— inside `submission/`, so it retires with the round. `figure-files --export`
does the same on its own. Each file is the render converted without
resampling: a figure keeps the DPI it was drawn at, and one below
`figures.dpi_raster` is reported as something to re-render, never scaled up.
The type is `figures.file_format` when it is sourced; with it `unknown` the
export writes TIFF and **says every time** that the choice was not the
journal's. The name is `figures.file_naming`'s pattern, or `Figure_1.tif`
when there is none. A render already in an accepted format is copied; a
conversion needs Pillow, and without it that figure is reported DID NOT RUN
and nothing is written in its place. `submission-names` carries the files
into `upload/` under the names they were exported with.

A project whose floats are managed elsewhere exports nothing — there is no
float set here to export from — and **say so to the user**: the figure files
have to be put in `submission/figures/` by hand, and `figure-files` then
checks them against the figures the text cites.

### Suggested reviewers: one list, rendered where the journal wants it

The names live in **`<source_text_rN>/suggested_reviewers.md`**, beside
`ai_disclosure.md` and for the same reason — it is something the author writes
that is not a manuscript section, and the package files are regenerated and
overwritten on every run, so a list kept there would be retyped every round.
`submission-package` creates the stub and never overwrites it.

Where it comes out is `submission.suggested_reviewers_in` in
`requirements.yml`:

| value | what happens |
|---|---|
| `cover_letter` | the names are written **into the letter** and **no standalone file is produced** — the AI-declaration rule (§17.5) applied to the other thing a letter can carry |
| `separate` | the standalone `suggested_reviewers_rN.md` is written, rendered from the same source |
| `portal` | neither: the journal collects them in its own submission system. The file is still yours to keep the list in |
| `unknown` | the standalone file, which is the safe default both ways — a list the journal did not want is a file nobody opens, and a list folded into a letter the journal wanted separately is a list that never arrives |

Read the journal's page and record it, like any other requirement. **Do not
maintain two lists.** If you find names in a package file and different names
in the source, the source is the one the user edits and the package file was
about to be overwritten anyway.

Run `manuscript.py authors "<project>"` first and clear what it reports. It is
the only check in the pipeline whose findings cannot be repaired after
publication — a missing figure is an email, a missing coauthor is a correction
notice.

**Author order is the `#` column of the author list, not the order the rows
are in** — reordering by cut and paste is how a first author accidentally
becomes third, so the engine sorts on the number and ignores row order.

**Gate: this module refuses to run while any `**[FLAG: ...]**` remains.** Flags
are how the pipeline says "I did not make this up"; a submission still carrying
one means something got skipped.

When a coauthor's returned `.docx` shows they did substantially more than their
row of the contribution matrix claims, report it. Do not auto-edit it —
authorship credit is the user's call, and a tool that silently adds a role has
written an authorship claim nobody made.

### The signed COI forms — pass it on, do not act on it

The command also reports on `plan/author_information/conflict_statements/`,
where the signed competing-interest forms go, one file per author named with
that author's initials:

```
  COI forms  1 of 3 returned in plan/author_information/conflict_statements/
```

**Say who is outstanding and move on.** Most journals will not send a paper out
for review until every form is in, and they arrive over weeks from people who
are not watching this — so a submission stalls on a signature nobody was
tracking. Naming the missing ones once, when the package is built, is the whole
intervention.

Three things not to do with it:

- **Do not treat it as a gate.** It is a warning, never a flag. The forms are
  not in the package — they go to the journal's own portal, attached to the
  submission record — so an outstanding signature does not make a finished
  paper unfinished, and `sendable` is unaffected. Report both facts in one
  breath: the package is sendable, two forms are not back yet.
- **Do not open a form.** The engine reads file names and nothing else, and so
  do you. A signed disclosure is a PDF out of a publisher's portal; there is no
  field in it that means the same thing twice running, and a wrong reading
  would be a claim about somebody's declared financial interests.
- **Do not confuse it with the statement.** The Competing Interests sentence
  printed in the paper is prose under its heading in `affiliations.md`, and
  `submission-package` builds the statement from there as it always did. These
  files are the paperwork behind that sentence.

An `unmatched` file is reported like a missing one, and it means what it says:
a form filed under a name the author list does not recognise. Offer to rename
it to the author's initials; do not guess whose it is.

### Go and get the blank form — the first time the statement is flagged

**On the round where the Competing Interests statement first raises a
`**[FLAG: author]**`, fetch the journal's own disclosure form.** Not when the
package is built and everybody is waiting on it — then. On a first round there
are no signed forms and there is no blank one either, and those two look
identical from the report while the fix for each is the opposite: chase four
people, or go and find one PDF.

What to do, once:

1. **Read the journal's author guidelines** for what they require — most
   publishers link an ICMJE disclosure form, their own declaration, or both.
   Some collect it in the submission portal and publish no form at all.
2. **Download it into
   `plan/author_information/conflict_statements/blank_form/`.** Keep the
   publisher's own filename. More than one file is fine — a journal wanting an
   ICMJE form *and* its own supplementary declaration wants both.
3. **Record where it came from** in `journal_requirements/requirements.yml`:
   `submission.coi_form: required` and `submission.coi_form_url: <the URL>`.
   Same rule as every other requirement — the URL is what makes it sourced
   rather than assumed.
4. **If there is no form to find, say so and set
   `submission.coi_form: not_required`.** A journal that collects disclosures
   in its portal is a different situation from nobody having looked, and
   leaving the folder quietly empty makes them read alike. Nothing asks again
   once that is recorded.

`submission-package` warns while the folder is empty and a form is owed:
*"nothing for the authors to sign."* That warning is aimed at you on the first
round, not at the user on the last one. **Do not download a form into the
folder above it** — a blank sitting among the signed ones is counted as a
signed one.

## 8c. submission-names — the file an editor actually receives

```bash
python <tools>/manuscript.py submission-names "<project>" --journal LANGMUIR --dry-run
python <tools>/manuscript.py submission-names "<project>" --journal LANGMUIR --name EXAMPLE_STUDY
```

`manuscript_r5.docx` is the right name for five rounds and the wrong name for
the one moment that matters: an editor's inbox holds forty of them, and `r5`
says something about this project's bookkeeping and nothing about which paper
it is. This writes the set under the name it is sent as —
`EXAMPLE_STUDY_manuscript.docx`, `EXAMPLE_STUDY_title_page.docx` — into
`{JOURNAL}/submission/upload/`.

**Run it when the user says it is time to submit, and not before.** It is a
separate command for a reason: the round suffix is load-bearing everywhere
else, so the originals stay exactly where they are and this copies beside
them. `upload/` retires with the round, so what was actually sent stays
recoverable from `obsolete/drafts/rN/`.

Three refusals, and each one wants a different answer from you:

| It says | What to do |
|---|---|
| the package still has flags | Clear them. This is the same gate `submission-package` applies, said at the moment the files would go out. |
| `short_name` is the folder name slugged | **Ask the user for two or three words.** `scaffold.py` fills `short_name` from the folder name when nobody supplies one, so a project can carry a name no human chose — and this is the name on the file an editor receives. Re-run with `--name`, and offer to write the answer into `project.yml` so the next round does not ask. |
| the build is from mock data | Not a naming problem. Go back and build from `data/raw/`. |

The `.md` beside each `.docx` is **not** in the set, and the command says so:
it is the file you edit, the `.docx` is what is uploaded. A folder called
`upload/` holding both answers "which one do I send" with two files.

**Re-run it after any rebuild.** The copies do not update themselves, and a
stale `EXAMPLE_STUDY_manuscript.docx` beside a fresh `manuscript_r5.docx` is the
one failure this folder can produce. Files from a previous run under a
different name are removed and named in the output.

## 8b. ai-disclosure — the engine drafts it, the author confirms it

```bash
python <tools>/manuscript.py ai-disclosure "<project>" --journal IJROBP
python <tools>/manuscript.py ai-disclosure "<project>" --journal IJROBP --draft
python <tools>/manuscript.py ai-disclosure "<project>" --journal IJROBP --from-ledger
```

Journals want this three genuinely different ways, and one journal can want more
than one: a statement **in the paper** (a named section, sometimes inside
Methods or the Acknowledgments), a declaration **in the submission** (the cover
letter, a separate form, or a portal checkbox), or an explicit statement that
**neither** is needed. `requirements.yml`'s `ai_disclosure` block records which,
and like every other requirement it is `unknown` until read and **`unknown`
refuses rather than guessing** — a wrongly-worded declaration is worse than an
absent one, because it is a false statement about how the paper was written.

**Decided 2026-09-21: the engine drafts it, the authors confirm it.** The
reasoning that an engine must not compose its own disclosure still holds for a
statement that asserts itself *finished*; it does not hold for a *draft*,
because `submission-package` already refuses on any remaining flag — a filled
draft under a flag carries exactly the same requirement to be read and
confirmed that a blank stub did, for less work from the author. What you do:

1. **Run the command with no flags first.** While `ai_disclosure.required` is
   still `unknown`, it scaffolds blanks — three `**[FLAG: author]**` lines
   under the journal's own `scope` and `exempt` text — and `--draft` refuses,
   because drafting against a requirement nobody has read is answering a
   question nobody asked. Read the journal's policy and record `required`,
   `in_paper` and `in_submission` in `requirements.yml` with the URL.
2. **Once it is determined, run `--draft`.** It writes this toolkit's default
   statement into `ai_disclosure.md`, under ONE `**[FLAG: author]**` that
   means "confirm this, don't compose it." `init` also calls this
   automatically once the block is no longer `unknown`, so a project reaching
   this stage usually already has the draft waiting. **It never overwrites a
   stub the author has written into or already confirmed** — the flag gone,
   or the blanks touched, both read as theirs.
3. **Offer `--from-ledger`, never run it unasked.** It *appends* — never
   replaces — what the engine *factually did*, out of the run ledger, naming
   the round every line came from. It writes nothing the ledger does not
   contain, and on a project with no ledger it writes nothing at all.
4. Say plainly that the declaration is theirs to check against what actually
   happened on this project — a paper whose figures were hand-drawn, or whose
   analysis was entirely their own — and that the group's standard wording,
   once it exists, belongs in `config --instruct` so this stops being a
   decision every round.

**The gate is unchanged by any of this.** `submission-package` refuses while
any flag remains, whether the file under it is three blanks or a filled draft
— the flag gates on its own presence, not on whether the text inside it is
empty. And the checklist row **fails on `unknown`**: "nobody looked" and "not
required" are the two things that row exists to keep apart, so a journal that
genuinely requires none gets a ticked row carrying the date and the URL.

When the journal wants it in the cover letter, it goes **in the letter** and no
separate file is written — two files that must agree is how they come to
disagree.

## 10. toc-graphic — the graphical abstract

```bash
python <tools>/toc_graphic.py status   "<project>" --journal LANGMUIR --json
python <tools>/toc_graphic.py scaffold "<project>" --journal LANGMUIR --suggestion sug.json
python <tools>/toc_graphic.py examples "<project>" --journal LANGMUIR --json
python <tools>/toc_graphic.py render   "<project>" --journal LANGMUIR
python <tools>/toc_graphic.py wire     "<project>" --journal LANGMUIR
```

Most journals require one, they all size it differently, and it is the one
float nobody has a script for — so it gets made at midnight in PowerPoint and
pasted in at the wrong dimensions. Spec §7.2.

**Run `status` first, every time.** It answers whether the journal even wants
one, and does nothing at all if it does not.

**The engine does nothing on a guess.** `toc_graphic.required: unknown` refuses
to scaffold, exactly as every other unsourced requirement is refused (§4.3). If
you do not know, go and read the guidelines — do not create a folder the user
will then trust.

**The graphic itself is a PowerPoint slide the user opens and rearranges, and
the `.pptx` is written once and never overwritten** — not by a second
`scaffold`, not by `--refresh`, and not by `render`, which exports a `.png`
from it and leaves the file byte-for-byte as they left it (pinned by
`tests/toc_graphic.py`). The same holds for drawn panels inside an ordinary
figure; those go through `create-graphic-figure`.

#### What you decide, and what the engine decides

The split is the usual one. **The engine lays out and writes the OOXML. You
decide what the picture shows** — that is judgment, and it is the hard part of a
graphical abstract.

Read the title, the abstract, the outline's claims and `plan/captions.md`, then
write a suggestion:

```json
{
  "title": "Support composition sets particle dispersion",
  "stages": [
    {"label": "precursor film",      "caption": "cast on the support"},
    {"label": "H2, 400 °C",          "caption": "reductive decomposition"},
    {"label": "dispersed particles", "caption": "turnover measured"}
  ],
  "footnote": ""
}
```

Three rules for the suggestion, and they come from the journals' own guidance:

- **It must show what the paper is about without giving specific results.** A
  graphical abstract is not a results figure.
- **Text is for labelling only** — compounds, arrows, diagrams. Long phrases are
  named as a failure in ACS's guidance and they are what makes a graphic
  unreadable at 3.25 inches.
- **Never propose reusing a manuscript figure.** Several journals forbid it
  outright, and the artwork usually has to be original and unpublished.

Without a suggestion the engine writes the layout with `[TK: ...]` labels.
That is the right output when you genuinely do not know yet — a `[TK]` is
visible, and `status` reports it as outstanding until it is filled.

#### Then hand it to the user

The `.pptx` is **theirs from the moment it exists and nothing here ever
overwrites it** — the same rule `plan/outline.md` gets. Say that out loud when
you hand it over, because the whole design depends on the user believing it and
editing the file freely.

`--refresh` rewrites the generated README, the examples index and the `.R`
script from the current requirements. It never touches the `.pptx`.

`scaffold` also populates `examples/` on the way past when a guidance PDF is
already saved, so the folder arrives useful on the first run rather than the
second. It cannot be failed by that step.

#### `examples/` — the journal's own graphics, extracted

```bash
python <tools>/toc_graphic.py examples "<project>" --journal LANGMUIR --json
```

**`examples/` holds real images**, so the user can open the folder and see what
the journal expects. They are never used in the paper — most journals require
original unpublished artwork — and the index says so.

They come out of **the journal's own author-guidance PDF**, which is the one
source that is unambiguously the journal's to hand out. ACS's *Guidelines for
Table of Contents/Abstract Graphics* gives its whole second page to six of its
own examples in a GOOD / POOR table with a sentence on each. Fetch that document
into `examples/` (or `journal_requirements/sources/`) and the engine does the
rest. **Do not download published graphical abstracts** — those stay links.

**Your half is `examples/labels.json`, and it is data, not prose**, so that the
engine stays the only writer of `README.md` and a re-run cannot wipe your work:

```json
{
  "source": "page 2, \"Examples of Good and Poor TOC/Abstract Graphics\"",
  "columns": {"1": "good", "2": "poor"},
  "cells": [
    {"row": 1, "column": 1, "verdict": "good", "slug": "balance-of-image-and-text",
     "quote": "This graphic has a good balance of images and description.",
     "note": "Three images side by side that read as one graphic."}
  ],
  "links": [
    {"title": "…", "journal": "*Chem. Rev.* 2020", "doi": "10.1021/…",
     "why": "the framework-to-catalyst story told as one picture"}
  ]
}
```

- **`quote` is verbatim** from the guidance document. It is the journal's verdict
  and paraphrasing it loses the authority that makes it worth reading.
- **`slug` becomes the filename** — `good_1_balance-of-image-and-text.jpg` — so
  write it as the lesson. The folder listing should be readable on its own.
- **Every DOI in `links` goes through `scholar.py verify` first**, like every
  other citation this pipeline writes. A link you could not verify is not
  written.

**You map cells to verdicts. You do not decide which image is in which cell.**
The engine reads that off the page geometry — the guidance page's own column
rule and its aligned row tops — and this is not a place to be helpful. Measured
on the real ACS document, the *emptiest* graphic on the page is its **good**
"simple and appealing" example, and a tidy, perfectly legible panel of
histograms is its **poor** "uninteresting and not informative" one. Judged by
eye those two swap. If a run's output looks wrong to you, **check `labels.json`
row and column numbers against the document — do not reassign images to cells
to match your reading of them.** A folder labelled backwards teaches the
opposite of the journal's standard while looking authoritative.

Run `status` after, and read what the run reports:

- **Dropped images are page furniture** — mastheads and column rules — and each
  is listed with its size, its document, and every rule it failed. If something
  that looks like a real example appears in that list, say so rather than
  adjusting a threshold: the filters clear the known examples by 1.7×, so a
  genuine example landing there means the document is shaped differently and
  §7.2.3.2 needs revisiting.
- **`pypdf` missing, or no guidance PDF** are both normal answers. The folder is
  still written and the run says which happened. Relay it; do not go hunting for
  another PDF reader.
- **A file the tool did not write is never overwritten.** Anything the user
  drops in `examples/` is theirs, and `.examples_state.json` is what makes that
  distinction possible.

#### Rendering, and the case where nothing can

`render` runs the generated `.R` script, which tries PowerPoint via COM, then
LibreOffice, and then **stops and tells the user to export the PNG from
PowerPoint themselves**. That third path is a supported outcome, not a failure —
relay it plainly rather than treating it as an error, and do not go looking for
another converter. What must never happen is a wrong-sized image reaching the
editor.

#### Wiring, and why later rounds take care of themselves

`wire` writes a delimited block into `source_text/toc_graphic.md`:

```markdown
<!-- BEGIN GENERATED: toc-graphic -->
![For Table of Contents Only](../../plan/toc_graphic/toc_graphic.png){width=3.25in}
<!-- END GENERATED: toc-graphic -->
```

Everything outside the markers is the user's prose and is never touched.
Everything inside is derived, so a re-render or a change of journal flows
through on the next round with no second mechanism. It refuses to wire before a
`.png` exists, because a block pointing at a missing file builds a `.docx` with
a broken image and pandoc does that without complaining.

If the user deletes the markers, that is an opt-out. Say so once; do not put
them back every round.

#### The check that earns its place

**Type size.** Every text run below the journal's minimum is reported with its
point size. It is the most commonly failed rule on a graphical abstract and it
is countable, so it is the engine's job rather than an eye's. A run that
declares no size is read at PowerPoint's 18 pt default and said to be
undeclared — assuming the journal's preferred size there would hide the failure
the check exists to catch.

Staleness is the other one, and it is the reason this module exists at all: the
user improves the slide, nothing re-renders, and the **older** image goes to the
journal. `completeness` carries that into `reports/rN/outstanding.md` — the
author's list, not the `.docx`. It is a content hash, never a modification time — OneDrive
rewrites mtimes on sync.


## The author files

Two files, and they are the only thing in a submission that cannot be repaired
after publication. A missing figure is an email; a missing coauthor is a
correction notice.

`plan/author_information/authors.md` — the **author list** (`#` for submission
order, name, affiliation keys, ORCID, email; the corresponding author on its
own line), and under it the **contribution matrix**: one row per author, one
column per thing a person can do on a paper, an `x` in every cell that
applies. The columns are the fourteen CRediT roles under plain-English
headings. **The user may add columns** — a heading the engine does not
recognise is carried into the statement verbatim rather than dropped — and
they may add rows for every coauthor.

`plan/author_information/affiliations.md` — the **affiliations**, keyed
`A`, `B`, so an address is typed once and an author with two of them writes
`A,B`; the **grant table** (funder, award number, who holds it); and the
Acknowledgements, Competing Interests and Ethics statements.

```bash
python <tools>/manuscript.py authors "<project>" --json
```

It returns everything both files hold, the byline it would build, the two
statements it would write, and every defect. Read it before `assemble` on any
round a coauthor will see, and always before `submission-package`.

**Report; never edit.** Who is credited with what is the user's decision. Fill
in a name they gave you, fix a key they mistyped, add a row for a coauthor they
named — that is transcription. Deciding that somebody deserves *Formal
analysis* is not, and a tool that quietly adds a role has written an authorship
claim nobody made. When you can see from a returned `.docx` that a coauthor did
more than their row claims, say so and let them decide.

**What the matrix buys, beyond not retyping the statement into a submission
form.** The statement comes out in author-list order rather than the order rows
were added, so a first author cannot become third by a cut and paste. Column
headings become CRediT's own wording, which is what the journal asks for. A row
keyed by initials resolves to the author it belongs to. And the pipeline can
tell you that an author has no role marked, which is a question worth asking
out loud before submission rather than after.

Both files are also read for every end-matter statement, first with content
wins — so a project written before they were split keeps everything it had in
`authors.md`.

**Both live in `plan/`, and both start blank.** They are not source text: the
`drafts/source_text_rN/` folder is renamed forward on every round, and an
author list belongs to the project rather than to a round — held there, one
paper would carry eight copies of a byline that is meant to be identical.
Projects written before the move keep theirs where it is, and the engine reads
whichever it finds and reports the path it read.

**They ship blank, and they should have been filled in before you were
called.** Neither is needed to draft, to check or to build a `.docx`, so an
empty one never blocks anything — but empty is a starting state, not a resting
one, and `setup-project-directory` offers them as a day-one job for exactly
that reason. When you find them empty, you are the backstop.

### Ask for them; do not flag them and wait

You are the **backstop** here, not the intended route:
`setup-project-directory` puts the author list on its day-one list precisely
so this never comes up mid-round. Ask without commentary — a user who skipped
it had a reason, and a lecture about it costs more than the question.

**If a file is missing entirely, create it from the answers.** Do not report it
absent and stop — that is the failure the flag machinery already has one end of
and this has the other.

Ask in **small questions with defaults**, the way `setup-project-directory`
asks its six. Ask them **early in the round rather than at the point of need** —
an author list is not a drafting decision, and stopping between the results and
the discussion to ask who is corresponding interrupts the one part of the round
that benefits from continuity. One short block of questions, then draft:

- Who is on this paper, and in what order?
- Where is each of them? (One address typed once; the rest reference it.)
- Who is corresponding, and at which address?
- Who ran the experiments? Who analysed? Who wrote the first draft? Who
  supervised?
- Is there a grant behind this, and who holds it?
- ORCID for anyone who has one — optional, and most journals only need the
  corresponding author's.

Fill the matrix from what comes back. *"Ada ran the experiments and made the
figures"* sets three cells and needs no follow-up. Anything the user does not
answer stays blank, blank stays reported by `completeness` under `authors`, and
**the Report-never-edit rule above still holds**: transcribe what they said,
and never infer a role they did not claim.

## Captions and the claim-first legend

The house rule, from the user's former PI:

> **Start every legend with a subtitle that asserts the claim the data
> supports.** Tell the reader what to think. The rest is descriptive.

`source_text/live_captions.md` is the journal-adapted copy of
`plan/captions.md`. Generate it from the same `read_captions.R` grammar
everything else uses — nothing else parses that file.

**Report every change from the original as a diff line**
(`Figure 3 — shortened 68 → 40 words (IJROBP cap); panel keys moved to
supplementary`). `prose.py captions` computes them. **If a trim would drop a
claim rather than detail, stop and ask.** A caption is where the claim lives.

## Why citekeys, and what happens when a coauthor deletes a sentence

`[@jensen2019arrays]` is a **build-time token**, not manuscript text. citeproc
substitutes it during assemble, so the `.docx` an editor receives contains
`[7]` or `(Jensen et al., 2019)` as plain, already-rendered text, with a
reference list ordered by the journal's CSL.

**Numbers are never stored — they are derived, every render, from scratch.** So
a coauthor deleting a cited sentence cannot break the numbering: the ingest maps
the deletion back to the markdown paragraph, removes the sentence with its
citekey, and the next assemble renumbers the whole document. There is no
numbering state to drift.

**Never hand-edit a citation number, in any file, for any reason.** A patched
number is correct until the next assemble and wrong forever after.

What does need catching, and by whom:

| what the coauthor did | who catches it |
|---|---|
| Deleted the only sentence citing a paper | `prose.py citekeys` uncited-entry rule — ask whether to drop it or re-cite |
| Deleted the sentence, the claim survives elsewhere | evidence-check |
| Deleted just the bracketed marker | raise `**[FLAG: citation]**` on that sentence; never silently drop the key |
| Typed a new reference by hand in Word | verify it against PubMed and add it to the `.bib` — otherwise the design quietly loses coauthor citations |

## Every float, every panel, called out and in order

```bash
python <tools>/prose.py crossrefs "<project>"     --style parenthetical --table-style parenthetical --json
```

Take `--style` and `--table-style` from `figures.citation_style` and
`tables.citation_style` in `requirements.yml`. **If either is `unknown`, pass
`unknown`** — do not infer a callout style from a similar journal, the same way
you would not invent a margin. The check still runs; it just does not judge the
form.

Four questions, and you act on the answers rather than reporting them:

| finding | what to do |
|---|---|
| `panel_never_cited` | the legend defines a panel no sentence points at. Add the panel letter to the sentence that discusses it, or ask whether the panel earns its place. |
| `panel_not_declared` | the text cites a panel the legend never defines. One of the two is wrong; ask which. |
| `reference_not_parenthetical` | this journal wants `(Fig. 2A)`, and the sentence says "Figure 2A shows". Rewriting is usually a small edit; make it and say you did. |
| `float_out_of_order` | a float is first mentioned before a lower-numbered one. **Propose renumbering the floats, not reordering the argument.** The prose is the paper; the numbers are labels on it. Renumbering means `plan/captions.md`, the `.R` scripts that name the files, and every callout in the text — say so before starting, and re-run `crossrefs` after. |
| `float_number_gap` | there is no Figure 4. Either a float was dropped and the rest need renumbering, or one is missing from `plan/captions.md`. |

Run it before `assemble` and again after ingesting edits: a coauthor who moves
a paragraph moves the first mention with it.

## The journal's length budget, and who decides

```bash
python <tools>/prose.py length "<project>"     --limit total=3500 --limit abstract=250 --limit figures_max=6     --policy "<writing_config.yml:length_policy>" --json
```

Limits come from `requirements.yml` — `text.word_limit_*`,
`figures.max_count`, `tables.max_count`. Pass only the ones that are sourced.
`completeness` does all of this for you when it is given `--journal`.

**The abstract is the exception: it is fixed, not asked about.** An abstract
over its limit has one answer, so `completeness` lists it under `auto_fix` and
the `draft-sections` brief tells the drafter to cut it to the limit, keeping
every number, citekey and flag. If `draft-sections` is not in this round's
modules, cut it yourself in session under the same rules, then re-run
`prose.py length`. Two things turn it back into a question: `length_policy:
over` (the user said *carry it*) and a frozen `title_abstract` (the user said
*this is mine*).

**Going over in the body is a question for the user, not a defect to fix on
your own initiative.** When `length_policy` is `ask` and the draft is over,
stop and put the choice to them plainly, with the numbers:

> The discussion is 1,240 words against IJROBP's 1,000. Two ways to go:
> **carry it** — keep everything for now and cut before submission, or
> **shorten** — cut now, and move the mechanism paragraph and Figure 5 into the
> supplement. Which?

Then record the answer so they are not asked again:

```bash
python <tools>/manuscript.py config "<project>" --journal IJROBP     --set length_policy=over
```

Two things to hold on to:

- **Offer the SI consolidation first when the answer is shorten.** A
  supplementary figure is information kept; a cut sentence is information gone.
  Moving a float to the SI renumbers it as `Figure S<n>` — that is a
  `crossrefs` change too, so re-run it.
- **An accepted overage is still outstanding.** It stays on `completeness`'s
  list and in `reports/rN/outstanding.md`, which is right: it has to be
  resolved before submission, and the record is what stops it being forgotten
  in round six. It is **not** in the `.docx` — see below.

## What Goes in the .docx, and What Goes to the Author

The `[FLAG: incomplete — PAPER NOT COMPLETE]` block at the end of the built
`.docx` appears **only when prose is missing** — a section still a stub, an
outlined section with nothing written (r1), a section deferred by the round's
scope — **or a figure is built from mock data.** Everything else
`completeness` finds (author details, unknown guideline fields, the
graphical-abstract question, label schemes, `manages` caveats) is the
author's to-do list: it goes to `reports/rN/outstanding.md` and the terminal,
never into the paper. A draft goes to coauthors before those details are
settled, and they are the people who will settle them.

`complete` and the NOT SENDABLE gate are unchanged: an item that is not in the
`.docx` still blocks the package. When you report a build, say where the full
list is.

**The supplementary label scheme is fixed by `assemble`.** Where
`supplementary.naming` says `Table E1` and the text says `Table S1`, the build
relabels every callout run in the source text (`Figs. S1 and S2` → `Figs. E1
and E2`), outside comments, and names each file it changed in its warnings.

## Every reference has to be cited

```bash
python <tools>/manuscript.py bib "<project>"                   # report
python <tools>/manuscript.py bib "<project>" --prune --dry-run # what would go
python <tools>/manuscript.py bib "<project>" --prune           # do it
```

An entry in `references.bib` that nothing cites is removed. **Show the
`--dry-run` list and get agreement before pruning** — the same flow as
revising an outline, and for the same reason.

The engine will not remove an entry that is cited anywhere in the project, even
when no manuscript section cites it: a citekey in a planning note, a caption, or
a coauthor's request is still a citation, and those are reported as kept with
the file that cites them. `prose.py citekeys` reports the narrower question,
"is the bibliography tight" — the two disagreeing is normal and the report says
which is which.

The old `references.bib` goes to `obsolete/notes/` and `references.ris` is
regenerated. Do not hand-edit the `.bib` to do this; a regex over an entry cuts
through the middle of a title with a nested brace.
