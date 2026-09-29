# The Outline Stage

Proposing or revising the paragraph plan, and the literature the introduction is grounded in.

Part of the `writing-engine` skill. **`SKILL.md` is the spine and is read on
every invocation; this file is read when the round reaches this stage**, and
`manuscript.py handoff --stage outline` is what names it. Everything the spine
says still applies here - the `<tools>` resolution, the module isolation
contract, and **Things that are wrong to do here** are stated there once and
are not repeated in this file.

A `§N` in this file is a module number. `SKILL.md`'s **What to read, and
when** table says which file each module's section is in; the others beside
this one are `drafting.md`, `prose-rules.md`, `revision.md`,
`submission.md`.

---

## When there is no outline

`plan/outline.md` is the claims ledger, and this skill was specified to run
after it exists. In practice it often does not: the data are in, the floats are
made, and the outline is still the scaffolded stub. **That is not a reason to
refuse, and it is not a reason to draft without one** — the outline is what
makes every later check mean anything, so the answer is to build one first.

```bash
python <tools>/manuscript.py outline "<project>"
```

With no `--write`, this reports the state (`missing` / `empty` / `present`) and
an inventory of everything an outline could be built from — which sections of
`plan/README.md` say something, the float set with its claim subtitles, the
headed blocks in the stats output, the bibliography, whether any prose already
exists. It also reports `can_draft`, and **if that is false, say so and stop
proposing**: an outline built from nothing is a fabricated paper, and it reads
exactly like a good one.

The flow is five steps, and none of them is optional:

1. **Confirm there is really none.** An outline missing from `plan/` is not
   the same as an outline that does not exist, and the folder cannot tell you
   which you have. Say what you found and ask:

   > There's no outline in this project — `plan/outline.md` is still a stub.
   > Before I propose one: do you already have an outline somewhere else — a
   > file, a doc, notes from a meeting? Point me at it and I'll read it in.

   Wait. **Do not skip this because the scaffold looks empty**; a scaffold
   looks identical whether the outline was never written or is sitting in the
   user's notes folder.
2. **If they point at one, adopt it — do not retype it.**

   ```bash
   python <tools>/manuscript.py outline "<project>" --from ~/notes/plan.md --dry-run
   python <tools>/manuscript.py outline "<project>" --from ~/notes/plan.md
   ```

   The engine reads that file with the **same grammar** `prose.py` reads
   `plan/outline.md` with, checks its evidence against this project's floats,
   stats and bibliography, and reports what adopting it would change before
   anything is written. Two answers matter:

   - **it parses** — show the user the lines and the diff, then write. An
     adopted outline is theirs and gets **no** proposed banner.
   - **nothing in it parses as outline lines** — the engine refuses, and that
     refusal is not the end. A meeting note or a paragraph of prose is often
     exactly the right *material*; read it, and offer to propose an outline
     **from** it in step 3.
3. **Propose.** Only once they have confirmed there is no outline anywhere.
   Build the lines yourself — this is the judgment half. One line per
   paragraph of the finished paper, in order, each the claim that paragraph
   makes plus the evidence behind it. **Nothing may be claimed that the
   inventory did not show you.** A paragraph you think the paper needs but has
   no evidence for gets its line with an *empty* bracket, which is the gap made
   visible; it never gets a plausible bracket.
4. **Show it and ask again.** Run the write with `--dry-run` and show the user
   what it does to their paper — `diff.classified`, new claims first, each
   with its reason and source. See "Say what each change does to the paper"
   below; the raw added/removed/changed diff is not the thing to lead with.
   This is what they are agreeing to.
5. **Write it with `--proposed`.** The banner comes up *after* they have
   answered on the claims, not before — it is bookkeeping, and putting the
   mechanism in front of the decision is item 37.

```bash
python <tools>/manuscript.py outline "<project>" --write proposal.json --proposed --dry-run
python <tools>/manuscript.py outline "<project>" --write proposal.json --proposed
```

### `--proposed`, and why an outline you wrote says so on its face

**Every outline whose lines came from you carries the banner.** `--proposed`
stamps a blockquote at the top of `plan/outline.md`:

> **PROPOSED OUTLINE - not approved yet.** Claude drafted these lines from …
> on <date> because the project had no outline. Every one of them is a guess
> about the paper you meant to write.

It is invisible to every parser — blockquote lines are neither `## Section`
headings nor `- claim [evidence]` bullets — and visible to every reader,
which is the entire point. A claims ledger that was guessed reads exactly like
one that was authored, and the file is what the user opens and what a coauthor
is sent.

While the banner is there, `status` reports `outline.proposed: true`,
`completeness` carries a **gap** (never a block — the proposal is a usable
ledger), and that gap is on the author's list in `reports/rN/outstanding.md`. **Say it out loud in every verdict you report off a proposed
outline**: the checks are real, and what they are checked against is not yet
the user's.

The banner comes off when the user says the lines are theirs — not before,
and never on your own judgment that they have seen it:

```bash
python <tools>/manuscript.py outline "<project>" --accept
```

`--accept` drops the banner and touches nothing else, so lines the user
edited by hand in the meantime survive it. If they want changes instead, that
is `--write ... --replace` with their corrections, and the banner is
re-stamped only if the new lines are again yours.

The bundle is JSON:

```json
{
  "sections": [
    {"section": "Introduction",
     "lines": [{"claim": "Cryo-ET has resolved arrays in mesophiles but not above 60 C.",
                "evidence": "@jensen2019arrays"}]},
    {"section": "Results",
     "lines": [{"claim": "Thermophilic arrays are more ordered than mesophilic ones.",
                "evidence": "Fig 2; stats: symmetry_index ~ species"}]}
  ],
  "notes": ["Ask GJ whether the 60 C cut-off is the right one."]
}
```

**The engine refuses a bundle that is not an outline**, and warns about one
that is merely untidy. Refused: a heading that is not a section of the paper,
notes smuggled in as a section, evidence written into the claim text, a
duplicate claim — those make a line the reader cannot parse. Warned: a line
over 25 words, a line with two sentences in it. Fix the refusals; pass the
warnings on to the user and let them decide.

### outline.md stays minimal — the advice, not a gate

**One line is the *idea* of a paragraph, never a draft of it.** Under 25 words,
one sentence, the claim and its bracket. Detail written into the outline is
detail written twice, and the paragraph is the copy anybody reads, so within
two rounds the two disagree and nobody knows which is current.

Everything that does not fit that shape goes in the **`## Notes` block at the
bottom**, which is not parsed and never becomes a paragraph: open questions,
what to check with a coauthor, a figure that is planned but not made. Use it
freely — it is what keeps the lines above short.

`prose.py outline` reports an over-detailed line as `over_detailed`, and the
writer reports it as a warning. **It is never a refusal, on either path.** The
outline is the user's; say the line runs long, offer to trim it, and write what
they ask for.

## Changing an outline that no longer fits

An outline written before the data came in is routinely wrong afterwards, and
**"the outline is wrong" is a first-class finding, not a failure of the
draft.** When the outline and the evidence disagree, propose the change.

```bash
python <tools>/manuscript.py outline "<project>" --write revised.json \
    --replace --dry-run          # what would change
python <tools>/manuscript.py outline "<project>" --write revised.json --replace
```

The dry run reports the diff as **added / removed / changed / kept**, matched
on the claim rather than the position, so a reordering is not reported as
wholesale replacement. That vocabulary is the engine's, and it is accurate —
it is not what you say to the user. **Show the user that diff and get their
agreement before applying it.** Never revise an outline in the same breath as
drafting from it — a claims ledger that quietly rewrote itself to match the
prose is no longer a check on anything.

### Say what each change does to the paper, before anything else (item 37)

`--dry-run` returns `diff.classified` alongside the raw diff, and **that is
what you show**. It has already sorted every proposed change into the classes
that matter, because the two commonest ones are not the same kind of thing at
all:

| class | what it means to the user | needs a decision |
|---|---|---|
| `new_claims` | a paragraph the paper did not previously plan to make | **yes — this is the one** |
| `claim_removed` | a paragraph the paper will no longer make | yes |
| `claim_reworded` | the same claim, said differently | yes |
| `claim_moved` | the same claim, in a different section | yes |
| `evidence_attached` | an empty bracket filled on a claim they already wrote | no |
| `evidence_changed` | an existing claim pointed at different evidence | no |

`classified.line` is a one-sentence summary in those terms; lead with it.
Each `new_claims` entry carries `why` and `source` — the one-line reason it
is being proposed and where it came from — and **a new claim shown without
those is not a question the user can answer**. If `new_claims_without_reason`
is non-empty, work out the reason before you ask, or drop the line.

This was measured: a proposal of 2 added lines and 6 filled brackets was put
to the user as a raw added/removed/changed/unchanged diff plus a question
about the `--proposed` banner, and the reply was *"I dont understand what this
means"*. The diff was accurate and still unreadable. **The banner is
bookkeeping; the new claim is the decision** — do not mention `--proposed`
until you have said what is happening to their paper.

**Keep asking, every time.** The user confirmed they want this question — it
does not become an automatic apply once the classes are legible.

`--replace` is required whenever the outline already has lines, and the file it
replaces is copied to `obsolete/notes/outline_<timestamp>.md` every time. The
old outline is never destroyed.

## 0. outline

**Runs first in every drafting preset.** `plan/outline.md` has two regions and
they are labelled in the file:

- `# Structure` — the paragraph plan and the project's claims ledger. One line
  per paragraph, the claim then its evidence.
- `# Notes — anything, in any order` — a working surface. Findings as they
  come off the instrument, half-thoughts, open questions. **Nothing there is
  ever treated as a paragraph**, so the user can write freely.

```bash
python <tools>/manuscript.py outline "<project>" --json
```

**The notes region is a primary source.** It is in `outline_sources`
alongside `plan/README.md`, the captions and the stats output, so `can_draft`
is true on a project whose entire intellectual content is ten findings under
`# Notes`. It used to report *"Nothing to build an outline from"* on exactly
that project, because the inventory did not list the one file the user had
been invited to write in.

**Three places an idea can live, read with equal weight**: `plan/README.md`,
`# Structure` and `# Notes`. None of them is the wrong one — put an idea
wherever it lands. What `# Structure` alone decides is the order and the
paragraph plan of the paper.

**Complete an incomplete outline as part of the round, rather than waiting to
be asked.** Build the missing lines from the notes plus the rest of the
inventory, write them with the `--proposed` banner that already exists — so
the lines are Claude's until the user accepts them — and **show the diff
before anything else in the round runs**. An outline that is already complete
is left alone.

**A note that becomes a paragraph line MOVES.** Take it out of `# Notes` when
you put it in `# Structure`. Copying counts it in both regions and turns the
working surface into an archive.

**Scoping a round** is a plan-time rule rather than an outline rule, and it
lives in `SKILL.md` under **Then state the plan and wait**: a scope governs
every stage of the round and every later module reads it off
`round_state.json`, so it belongs where the round is planned rather than where
one module is described.

## 0b. literature-landscape

**Runs before draft-sections, every drafting round.** Everything
draft-sections was given used to be a file *this project* wrote — the outline,
`plan/README.md`, the captions, the stats output, `methods_facts.yml`, the
`.bib` — so the introduction came out grounded almost entirely in this group's
own prior work, because that was the only literature the module had.

```bash
python <tools>/manuscript.py landscape "<project>" --scaffold --json
```

That gives you the subject terms, taken from the title and the outline rather
than from anything anybody retypes, and tells you which of the three questions
are still unanswered. Then search:

```bash
python <tools>/scholar.py search "<term> <term>" --limit 20 --json
python <tools>/scholar.py related <DOI-or-PMID> --json
python <tools>/scholar.py cited-by <DOI-or-PMID> --limit 20 --json
python <tools>/scholar.py verify --doi <DOI> --json      # or --pmid / --title
python <tools>/pubmed.py sections <PMID> --json          # full text, PubMed only
```

**Search through `scholar.py`, not `pubmed.py`.** One `search` merges PubMed,
Crossref, OpenAlex and arXiv and reports which index each row came from;
`pubmed.py search` asks one index of the six. This is not a preference. PubMed
does not index *Surface Science*, *Applied Surface Science*,
*J. Vac. Sci. Technol.* or *Vacuum* **at all**, so for this group's subject a
PubMed-only sweep returns nothing and reads exactly like an empty field.
`pubmed.py` keeps two jobs it alone can do: `sections` and `pdf`, the only route
to full text.

`related` and `cited-by` are how you find what a key paper led to — they answer
for a DOI, so they reach the surface-science literature that has no PMID.
Both try OpenAlex first, then Semantic Scholar.

arXiv is in the search merge and last in the verify cascade, so a preprint is
found but never outranks its own published version. Anything that verifies as a
preprint comes back with a `PREPRINT` flag: it is a real document, not a
peer-reviewed one. Cite it as a preprint or find the version of record — do not
pass the flag through silently.

Write `plan/literature_landscape.md`. **Three questions, and no others** — a
fourth heading turns it into a second `plan/README.md`, and two files that say
the same thing disagree within two rounds:

1. **What the field currently does with this material or method, and what it
   is good for.** The uses and the benefits.
2. **What has already been published on this specific question.** If the
   answer is "nothing", say so — that is the gap.
3. **What the most recent work says.** Recency matters. An introduction citing
   only this group and a 2011 review is the visible symptom of not having
   asked.

One line per paper: the claim it supports, then the identifier and the date it
was verified — `[PMID 33456789, verified 2026-09-03]`, or
`[doi:10.1016/j.susc.2005.01.038, verified 2026-09-03]` for a paper with no
PMID, or `[arXiv:2401.01234, verified 2026-09-03, PREPRINT]` for one that has
no journal version yet. **Every one goes through `scholar.py verify` before it lands there.**
Re-run `landscape` when you are done; `can_draft` is false while any question
has no verified reference behind it.

**The group's own prior work stays and stays prominent.** This adds the field
around it; it does not trade one for the other.

On a later round the file is refreshed, not rebuilt: check the recency
question is still current and move on.

## Outline adherence

After drafting, before assembling, map every paragraph to an outline line.
`prose.py outline` gives the counts, the evidence-bracket verdicts, and a
one-to-one lexical alignment — **the alignment is a hint, not a verdict.**

**It is a 1-5 scale and it is asked EVERY ROUND**, in the plan block, not a
dial set once (spec 19). The three old words are three of the five levels, so
a config saying `outline_adherence: strict` still reads and means 5.

| n | name | what it means |
|---|---|---|
| 1 | `none` | the outline does not steer this round; its findings go advisory and every truth check still errors |
| 2 | `loose` | a checklist - every line must appear somewhere; order is the drafter's |
| 3 | `medium` | the outline sets the argument and the order; every deviation is reported |
| 4 | `tight` | one paragraph per line, in the outline's order; the only permitted addition is a bridging sentence |
| 5 | `strict` | one line, one paragraph, in order, nothing added - predictable, and in past attempts painful to read |

Ask it as one line of the plan block, never as a prompt of its own. The round's
answer goes to `round_state.json`; `writing_config.yml` moves only if the user
answers `4 always`. A round nobody was asked about is labelled `(not asked)` in
the plan output and in `drafts/log.md`, because a deviation report is
uninterpretable without knowing whether the level was chosen or assumed.

```bash
python <tools>/manuscript.py plan "<project>" --journal X --adherence 4
```

**THE OUTLINE GENERATES IN r1 AND CHECKS AFTERWARDS** (spec 21), and this is
the part to get right. From r2 on the same digit means something different: it
is how strictly to **report** a deviation, never how literally to **rewrite**
a paragraph. A level 5 that regenerated `results.md` in r3 would discard every
correction r2's checks earned - the resolved flags, the fixed numbers, the
citations the self-resolution pass sourced - and the build would succeed at
exit code 0 with nothing recording that it happened. **Engine-owned is not the
same as disposable.**

So: no level, and no dial, recomposes a section that has prose. The plan block
prints `ADVISORY this round` when that is the case, and `--redraft <section>`
is the only thing that rebuilds one. `--redraft all` names each user-edited
section before discarding it - "all" is not a description of what is being
lost.

The one thing the outline still writes after r1 is **additive**: an outline
line no paragraph implements becomes one new paragraph at the outline's
position, its neighbours byte-identical, reported as *added from outline line
N*. In a frozen section not even that - it stays advisory and is offered back
with `--redraft`.

**Either the draft or the outline can be wrong, and you must not assume it is
the draft.** Offer three resolutions: revise the draft, update `plan/outline.md`
to match the better draft, or accept the deviation and record it in
`drafts/log.md`.
Rigid outline adherence produces painful prose — which is why "update the
outline" is a first-class option, not a grudging one.

**A frozen section's uncovered line is advisory, once.** On a section the user
has taken over, a `dropped_point` is listed as *uncovered — section frozen* and
nothing is written to fix it. Truth checks do not soften the same way: a number
absent from the stats output is wrong whoever typed it, and an unresolvable
citekey still breaks the build. What softens is every check whose remedy is
"write more prose".

**And there is a fourth resolution, for a point the paper is deliberately not
going to make.** Advisory-once is still a report every round, and a line
deleted on purpose comes back forever otherwise. `**[FLAG]**` can only say
*absent, someone must fill it*; this says *absent on purpose, stop asking*:

```markdown
- [waived: superseded by the XPS control] Report the sputter-clean sequence
```

```bash
python <tools>/manuscript.py config "<project>" --journal X \
  --waive-outline methods:14 --because "superseded by the XPS control"
```

Not drafted, not reported as uncovered, not counted in adherence — but still in
the file, with its reason, where somebody can disagree with it in six months.
**The reason is required**, and a `[waived]` written without one is reported
rather than honoured: an unexplained waiver is indistinguishable from an
accidental deletion, which is the failure the marker exists to prevent.
