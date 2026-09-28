# The Drafting Stage

Writing the sections and the three passes over them.

Part of the `writing-engine` skill. **`SKILL.md` is the spine and is read on
every invocation; this file is read when the round reaches this stage**, and
`manuscript.py handoff --stage drafting` is what names it. Everything the spine
says still applies here - the `<tools>` resolution, the module isolation
contract, and **Things that are wrong to do here** are stated there once and
are not repeated in this file.

A `§N` in this file is a module number. `SKILL.md`'s **What to read, and
when** table says which file each module's section is in; the others beside
this one are `outline.md`, `prose-rules.md`, `revision.md`, `submission.md`.

---

## Telling the drafter what you want

Free-text instructions are accepted **at any invocation** — "keep the discussion
under 900 words", "don't use the word novel, the PI hates it", "write for
clinicians, not crystallographers". They are not one-off: they persist into
every later round until dropped.

```bash
python <tools>/manuscript.py config "<project>" --journal IJROBP            # show everything
python <tools>/manuscript.py config "<project>" --journal IJROBP \
    --instruct "Keep the discussion under 900 words" \
    --set outline_adherence=loose \
    --style-ref "10.1021/jacs.3c01234" \
    --waive "results ¶3"
python <tools>/manuscript.py config "<project>" --journal IJROBP --forget "novel"
```

**An instruction can name where the outline lives.** "Use the outline in
`notes/aim2.md`", "the paragraph plan is in the grant, section C" — that is an
instruction, and it is answered with `outline --from <path>`, never by
proposing one. Record it too, so the next round does not ask again:

```bash
python <tools>/manuscript.py config "<project>" --journal IJROBP \
    --instruct "The outline lives in notes/aim2.md, not plan/outline.md"
```

If the file they name is not reachable, or has moved, **ask for the path**;
do not silently fall back to proposing an outline. "There isn't one" and "I
couldn't find the one you meant" lead to opposite actions, and only the second
one is yours to resolve.

**Record them; do not just remember them.** An instruction held in the
conversation is gone next session, and the user will reasonably expect the third
round to still know the PI hates "novel". `config` stamps each with the date it
was given, refuses a dial value that is not one of the documented options (a
typo silently reverts that dial and biases the run in a way nobody asked for),
and dedupes, so re-giving the same instruction is a no-op rather than a second
copy.

Every instruction on the list goes into the drafting brief verbatim, and
`draft-sections` must report any it could not honour rather than dropping it
silently. If an instruction contradicts a house rule — a request to state a
number the analysis never produced, say — do not follow it and do not ignore it:
say which rule it collides with and ask.

**The dialect is in the brief, and it is `us` unless the config says
otherwise.** `writing_config.yml: dialect` is in the plan's `config` block.
Write *center*, *color*, *analyzed*, *characterized*, *sulfur*, *signaling* —
American spelling throughout, in every section and in every caption. It is
in the brief rather than left to the correcting pass because a pass that
converts after the fact is a pass that has to be right about every word; a
draft written in the right dialect gives it nothing to do.

The pass still runs, and it runs *before* any checker reads the paper:
`prose.py spelling --fix` is part of module 1c. It never touches a quotation,
a proper noun, a code span or `references.bib`, so a British spelling inside
quoted material is correct behaviour and not a miss.

**`prose.py italics --fix` runs beside it, and only half of it writes.** The
Latin phrases that are italic in every house — *in vivo*, *in vitro*, *in
situ*, *de novo* and the rest — are a lookup with no threshold and every false
positive nameable, so they are applied here, before anything reviews anything.
The other two halves are not lookups and are never written: a **gene symbol**
is a judgment about what the sentence means (`ANTXR2` the gene is italic,
`CMG2` the protein is roman, and they are the same molecule), and a term from
the **journal set** is italic in one house and roman in the next. Both reach
you with their sentence. `requirements.yml`'s `typography:` block supersedes
the house in **both** directions — `italic_terms` turns a disputed term on,
`roman_terms` takes a universal one off for a house like ACS — and the report
names which rule it applied.

**Two more reading checks reach this module**, both report-only and both
naming a sentence you can act on:

- **`entity_forms`** — the abstract's name for a thing against the body's. The
  abstract is read alone, quoted alone and indexed alone, so a qualifier the
  body carries and the abstract drops is the abstract making a broader claim
  than the paper supports. It does **not** claim the abstract is wrong; it
  says the two differ and you decide.
- **`abbreviations`** — defined nowhere, defined only in the body, or used
  before it is expanded *in that region*. The house rule is the user's:
  *define it in the abstract, then define it again at its first occurrence in
  the main text*, because each is read alone. `requirements.yml`'s
  `abbreviations_in_abstract` supersedes it.

**Two abbreviation findings deliberately do NOT reach you**:
`never_abbreviated` and `defined_then_spelled_out` rest on an uncalibrated
threshold and go to the user in the round summary. Chasing them would have the
drafter coin abbreviations the field does not use, and swap every spelled-out
term for its short form — which is technically consistent and worse to read.
Same reasoning as the densities.

**`style_refs` are structural only.** Extract a *fingerprint* — mean sentence
length, paragraph count per section, voice and person, hedging density,
subheading use, how numbers are introduced, how the introduction funnels — and
add that to the brief. **Copying phrasing from another paper is plagiarism**, so
take measurements, never text, and never quote the reference paper into the
draft. Where a style reference conflicts with the house digest, the digest wins
and the conflict is reported.

## 0c. learn-from-edits — the round begins by reading what you changed

**Runs before draft-sections, every round.** The problem it exists for: you
edit r1, the engine regenerates r2 from `plan/outline.md`, and your edits are
gone. Worse, the next *project* starts from the same brief that produced the
prose you just spent an afternoon fixing. Every correction was paid for once
and banked never.

**Two steps, and both are required.** Applying without learning is what this
toolkit did before — the fix is paid for once. Learning without applying leaves
the current paper inconsistent with the lesson just taken out of it.

#### The freeze comes first — which sections are yours now

```bash
python <tools>/learn.py frozen "<project>" --json
```

Four states, and the plan line already printed them before you were asked to
go:

| state | means | what happens |
|---|---|---|
| `fresh` | no entry, or an empty stub | drafted normally |
| `engine-owned` | the hash matches what the engine last wrote | may be redrafted and revised |
| `frozen` | the hash differs — **you edited it** | draft-sections and revise-prose both skip it, and this module learns from it |
| `wiped` | there is an entry but no file | drafted normally — the existing "empty `source_text/` to force a redraft" gesture, which keeps working |

**Section granularity, never paragraph.** A paragraph-level hash would let the
engine keep writing around your edits, which is the behaviour that makes a tool
untrustworthy. Hashing normalizes trailing whitespace, line endings and runs of
blank lines, and nothing else — a changed word is a change.

**`--redraft <section>` on `plan` or `run --start` hands one back**, for that
invocation only, never sticky. It reports what it will discard, because what is
being thrown away is your prose. The lesson in it is banked first either way,
which is the only reason the order of these modules matters.

#### Extract and classify

```bash
python <tools>/learn.py extract  "<project>" --section methods --json
python <tools>/learn.py classify "<project>" --section methods --json
```

`extract` is mechanical and produces aligned before/after pairs at sentence and
paragraph granularity. `classify` gives you the evidence; **the judgment is
yours**, and getting this filter wrong is how the registry fills with garbage.

| class | example | becomes a rule? | what you do instead |
|---|---|---|---|
| `factual` | `400 °C` → `450 °C`; a corrected instrument model | **never** | the *recorded source* is wrong. Surface it as a data discrepancy, on the recorded-value path |
| `content` | a claim added, a paragraph deleted outright | **never** | an outline event: a new outline line, or a §4.4 waiver candidate — offer `config --waive-outline` |
| `citation` | a citekey swapped or added | **never** | route it to the citation ingest |
| `stylistic` | a 44-word sentence split in two; passive → active; a hedge removed | **yes** | apply and observe |
| `structural` | two paragraphs merged; a topic sentence added | **yes** | apply and observe |
| `formatting` | whitespace, a term's capitalization | at low weight | observe; it needs three observations, not two |

Three of those six are decided **mechanically and not by you**: whether the
numeric multiset changed (→ `factual`), whether the citekey multiset changed
(→ `citation`), whether outline coverage changed (→ `content`). `classify`
reports all three. Only an edit that changed none of them is yours to judge.

**The `factual` exception, and it is the only one.** An edit that *adds* four
numbers to a Methods section is evidence about how many numbers this kind of
paper's Methods carries — a numeric fact about the section, not a claim about
anyone's taste. It may contribute to a `kind: numeric` budget record and to
**nothing else**. A `factual` edit can never produce a prose rule.

#### Apply — layer 1, this paper

Propagate each `stylistic` or `structural` lesson to the **engine-owned**
sections of this manuscript, as the highest style authority under layer 0.

- It may **not** write to a frozen section. Your own prose is never "improved"
  by a lesson induced from it.
- It runs before draft-sections, so a section drafted this round is drafted
  with the lesson in its brief rather than patched afterwards.
- A lesson propagated into an already-drafted section is subject to the same
  preservation invariants module 1c is (§1c below) — take a snapshot first.

The lesson enters the brief **quoting your own edit**, because a brief that
says "the user prefers shorter sentences" is a paraphrase a drafter can satisfy
vacuously:

```markdown
## Layer 1 — from your edits to r1
- You split every sentence over ~35 words in `methods`. Four instances.
  e.g. before: "The chamber was evacuated to 2×10⁻⁹ Torr using a turbo…"
       after:  "The chamber was evacuated to 2×10⁻⁹ Torr. A turbo pump…"
- You removed "notably" and "importantly" wherever they opened a sentence.
```

#### Observe — layer 3, every future paper

```bash
python <tools>/learn.py observe --rule "Open the abstract with the material \
system, not the technique." --kind stylistic \
  --research-type surface_science --section title_abstract \
  --project "<name>" --round r1 --paragraph 4
```

**One observation is never a rule.** A first observation is a `candidate`,
listed for the user and applied to nothing. Two independent ones — different
sections or different rounds — promote it to `active`; `formatting` needs
three. A rule earns `universal` scope only by showing up in two *different*
research types.

**Never observe an edit from a round that was over the journal's word cap.** A
section cut to fit `requirements.yml` looks exactly like a preference for
shorter prose. Pass `--length-forced` and the evidence is applied to this paper
and excluded from induction. The same goes for a round with an unresolved
`**[FLAG]**` in that section.

Contradictions are surfaced by `learn.py conflicts <id>` and resolved by
`learn.py resolve` with one of three outcomes — narrow both, supersede the
older, or mark both `conflict` and apply neither. **Never auto-resolve.** The
question to ask is: *does the contradiction have a reason a scientist in each
field would recognize?* "In cryo-EM the method is the contribution; in surface
science the material is" is a reason. "The user wrote it differently the second
time" is not — that is a supersede.

#### The research type, asked once

Layer 3 is scoped by it, so it has to be stable, and the failure to design
against is type proliferation: a type invented per project scopes every rule to
a population of one, no rule ever reaches two observations, and **nothing
errors while nothing is learned**.

On the first invocation in a project, propose one from the project's own
content — the title, `methods_facts.yml`, the outline, the journal — and **ask
the user to confirm it**:

> This looks like **Surface science / UHV** (from the UHV chamber, TPD, and
> `Surface Science` as the target). Learned drafting rules for that type will
> be used. Correct? Otherwise pick one, or add a new type.

```bash
python <tools>/learn.py types --list --json
python <tools>/manuscript.py config "<project>" --journal X \
  --research-type surface_science
```

It is cached in `project.yml` and never re-asked, exactly like
`target_journal`. Adding a type needs a reason and is reported.

#### What this module writes

`reports/rN/learned.md` — the edits it read, how it classified each, what it
applied to which sections, and what it observed into the registry with the
resulting status. A rule that promoted this round is named, because the user
should see a rule become active before it shapes a draft rather than after.

## 0c bis. The brief the drafter is handed

**`draft-sections`' brief is `learn.py brief`, not the raw digest.** This is
the single most consequential wiring in this spec, and it is one command:

```bash
python <tools>/learn.py brief --research-type surface_science \
  --section results --round r2 --layer1 "<reports/rN/layer1.md>" \
  --config "<journal>/writing_config.yml"
```

It emits the whole precedence ladder as one ordered document:

| layer | contents | authority |
|---|---|---|
| **0** | **Invariants** — the recorded-value rule, citekey resolution, the flag convention, the two absolutes of the number-vomit rule | absolute; nothing overrides these |
| **1** | **the user's edits from the previous round**, quoted | highest style authority |
| **2** | **the ten rules** — universal to research papers, and **generative** | yields only to layer 1 |
| **3** | **learned rules for this research type**, each with its evidence | yields to 1 and 2 |
| **4** | **presentation and prohibitions** — the density budget, the digest's `## At revision` half | yields to 1, 2 and 3 |

**Layer 0 is the amendment and it is the whole point.** What is called "the
prohibitions and numeric values" holds two different kinds of rule, and only
one of them is style. *Presentation* — the density budget, magnitude in felt
units, every prohibition in the digest — is style and goes to layer 4, where it
is overridable with a reason on the record. *Truth* — no number that is not in
`analysis.md`, no methods value not in `methods_facts.yml`, every
unresolved value a `**[FLAG]**`, every citekey resolving — is not style at all
and goes to layer 0. A layer-3 rule is induced from prose edits, and **no
amount of evidence from prose editing can license writing a number that was
never measured.**

An absent layer is omitted rather than emitted as an empty heading. Cold start
— empty registry, no research type, no previous round — is layers 0, 2 and 4,
and it has to work without any of this having ever run.

**A layer-4 rule that yields is logged**, in `reports/rN/overrides.md`:

```markdown
| section | ¶ | layer-4 rule | overridden by | why |
|---|---|---|---|---|
| results | 3 | number_density: low (2 inline) | L2-07 results-as-Q-and-A | the question needs both conditions' values to be answerable |
```

`prose.py density` reads that file and goes **advisory** on a paragraph with a
logged override. That is the mechanism that makes the prohibitions "not too
restrictive" — not by loosening them globally, but by making them locally
yieldable with a reason. Repeated overrides of the same rule in the same
research type are evidence the rule is wrong for that research type, and they
feed the registry as budget candidates.

**`brief --explain "<sentence>"`** reverses it: which layer and which rule id
shaped a given sentence. Without it a registry of thirty learned rules is
unauditable and the user cannot tell a learned rule from a hallucinated one.

## 1. draft-sections

**Your brief is `learn.py brief` (§0c bis), not the raw digest.** It carries
the whole precedence ladder in order, and the digest reaches you through it —
its **`## At drafting` half only**. The `## At revision` half is module 1c's
and you never see it, because its rules are *editing* rules and there is
nothing to cut at draft time. A drafter told to produce already-cut prose does
not cut; it **compresses**, packing maximum content per sentence, and that is
the 40-word citation-stuffed sentence. The rule set was causing the failure it
exists to prevent.

Where two layers conflict the lower-numbered one wins. If a layer-4 rule
yields, do what layers 1–3 require and log the override in
`reports/rN/overrides.md` with a one-line reason. **Layer 0 never yields**; if
a layer-1 lesson would require breaking it, do not apply the lesson — report
it.

**Skip every frozen section** (§0c). A section the user has taken over is not
redrafted, and its outline lines are not reported as uncovered.

Then read `plan/literature_landscape.md` (§0b — it is an input to this module, not a
reference you may skip), `plan/outline.md`, `plan/README.md`,
`plan/captions.md`, `data/analysis/analysis.md`,
`data/methods_facts.yml`, and `data/analysis/provenance.json`.

**The outline is the claims ledger.** One line per paragraph: the claim, then
its evidence bracket — **one sentence per intended paragraph, before drafting
starts.** That is what makes it the drafting spine rather than a list of
topics, and it removes most of the drafter's room to sprawl. Read it first:

```bash
python <tools>/prose.py outline "<project>" --adherence medium --json
```

Accepted bracket contents: `Fig N` / `Table N` (must be a block in
`captions.md`), `stats: <term>` (must appear in the stats output), a PMID or a
citekey, or `background`. Anything else, or an empty bracket, is the gap — and
it is visible now, before any prose exists to disguise it.

**`background` no longer means "no citation needed".** It means *cited from
`plan/literature_landscape.md`, rather than from a result in this paper*, and
a `background` bracket with no PMID or citekey beside it is reported as
`background_uncited`. The introduction's background lines cite from the
landscape. That is the whole point of §0: a claim about the state of the field
written from the drafter's own memory is the least trustworthy prose in this
pipeline, and it used to be the only prose in it that no check looked at.

**A half-written outline does not stop the run.** Most of a project's life is
spent half-finished, and being refused a draft because the discussion has no
lines yet would make this useless exactly when it is most useful. So: draft
every line that *does* resolve, leave the rest alone, and never invent a
paragraph to fill a gap the outline has not filled. That rule is right against
a **finished** outline; the outline step (§0) is what runs first so it is
being applied to one, and the notes region is where the material for the
missing lines comes from. An outline line with an
empty bracket gets its paragraph written **only** if the evidence exists
elsewhere; otherwise it becomes a `**[FLAG: data]**` where that paragraph would
have gone. Say plainly in your summary which sections you drafted and which you
could not.

**Methods are rendered, not recalled.** Turn recorded fields into prose and
**never supply a value that is not in `methods_facts.yml`,
`instrument_metadata.json` or `provenance.json`**. A missing field becomes
`**[FLAG: author — acquisition.total_dose_e_per_A2 not recorded]**`. A
plausible-sounding invented dose is the worst thing this pipeline could produce,
because it is unfalsifiable from inside the project and would go to print.

**Run the harvest before you believe any acquisition value is unrecorded, and
run it before drafting rather than after:**

```bash
python <tools>/scaffold.py harvest "<project>" [--under "<image folder>"] --json
```

Most instruments write their own settings into every file they save, so
`data/instrument_metadata.json` answers what a `[FLAG: author]` would
otherwise ask a person for. **Measured on one real project: 158 of 158 image
files carried an acquisition block, and the round still shipped three author
flags for values that were on disk** — one of which had *two* answers, because
two microscopes had been used, which is exactly what a person writing from
memory gets wrong. That is item 22's rule failing in practice: a flag that
survives into the manuscript is for a question only a person can answer, and
"what voltage was this image taken at" is not one when the image says.

The harvested block is **not commented out** — a `prefill` default is somebody
else's typical number and has to be confirmed; a harvested value is what this
instrument wrote about these exposures. Where it reports two values for one
setting, **write both with their file counts**: the disagreement is the fact,
and naming one microscope on a project that used two is false.

**Citations: only references verified in this session through `scholar.py`.**
A PMID, a DOI or an arXiv id — whichever the paper actually has. The rule used
to say "PMIDs, through `pubmed.py`", and that wording quietly excluded the
literature this group publishes in: a *Surface Science* paper has no PMID, so
obeying it literally meant leaving the field's own work uncited. Verify through
`scholar.py`, which asks all six indexes, and record whatever identifier came
back. Never a remembered reference, never a bare "(refs)". In-text citations are semantic
citekeys (`[@jensen2019arrays]`), never numbers — see "Why citekeys" below. A
claim you cannot source becomes `**[FLAG: citation — <the claim>]**`.

**Every paragraph leads with its main idea in the first sentence.** The one
non-negotiable structural rule.

**Write it so somebody reads it** — see "Hook the reader" below. This is a
house rule, not a stylistic nicety, and it applies to every section.

**Subheadings take the journal's capitalization, and it is almost always title
case.** The level-1 headings come from `structure.section_order` and are the
journal's own words; the `##` subheadings inside a section file are yours, and
they are what a copy editor marks up first. ACS, Wiley, Elsevier and Nature all
set section headings in title case — *Framework Coverage and Crystallite Size
Before Decomposition*, not *Framework coverage and crystallite size before
decomposition*. Capitalize every word except articles, coordinating
conjunctions and prepositions of four letters or fewer, and always the first
and last word. Where the guidelines say something different, they win; where
they say nothing — which is usual, because it is a copy-editing matter —
follow the publisher's published style and be **consistent across the whole
paper**, which is the part an editor actually notices.

**The abstract is written last, and it is never left empty.** Draft the four
body files first — `introduction`, `methods`, `results`, `discussion` — and
write `title_abstract.md` after them, *from* them. `SECTION_FILES` lists
`title_abstract` first because it is first in the manuscript, and an agent
working that list in order writes the abstract before the results it is
supposed to summarize exist. Use this order instead:

    introduction → methods → results → discussion → title_abstract

Whatever exists gets written: the purpose, the system, the approach, and every
result that is on the page. What is genuinely not there yet becomes **one**
`**[FLAG: data]**` at the **end** of the abstract naming what is missing — the
half-abstract first, the flag after it, so a coauthor reads a paper's opening
rather than a note about its absence. **An empty `# Abstract` box fails the
build**, and that is deliberate: it is not an unfinished paper, it is a paper
whose sections were written and whose abstract was skipped.

**A separate agent writes the abstract, and its only context is the
manuscript.** The user's instruction, and it is the reasoning that makes
stats-check blind. An agent that has read `plan/README.md`, the hypothesis and
this conversation writes the abstract of the paper we *meant* to write. Give
the abstract agent:

- the finished `source_text/*.md`,
- `plan/captions.md`,
- `requirements.yml`'s abstract type and word cap,

and **nothing else from the project directory** — no README narrative, no
outline, no idea-generation notes, no data folder. An abstract that claims
something no section says is then structurally impossible rather than merely
discouraged, and the difference between "the paper says this" and "the project
hoped this" becomes visible at the one place every reader starts.

**That list does not widen. What the abstract agent owes is one more output.**
Nothing required it to decide *which* of the available claims is the one, and
an agent that has not committed to a single claim has only one way left to
signal importance: state more results. That is the measured shape of the
abstract this rule was written against — 22 inline numbers against a budget of
5.88, six of eight sentences over 25 words, a 55-word opener. The number
density there is a symptom, and a per-section budget will not fix it alone.

So the abstract agent also writes two headings into
`reports/rN/prose_report.md` — one module writes both files, and the
take-home is a fact about the abstract's prose, so it folded in rather than
keeping a four-line file of its own (item 43):

```markdown
## Take-home
Pd nanoparticles on TiO₂(110) decompose below the temperature at which the
support reduces, so the reduction is a consequence and not the cause.

## Supported by
source_text/results.md ¶4, source_text/discussion.md ¶2
```

```bash
python <tools>/prose.py claim "<project>" --report "<reports/rN/prose_report.md>"
```

Two conditions, and **neither is a style judgment**: the claim sentence's
content words appear in the sections it cites, and the abstract contains a
sentence carrying that claim. This is "one central contribution" with the only
enforcement that rule can honestly have — asking the agent to name the
contribution rather than grading its prose. An absent `## Take-home` is a
finding, never a build failure.

**Notes under either heading are fine — write the report the way a person
writes a report.** `claim` reads the first *sentence* of the first paragraph
under `## Take-home`, and under `## Supported by` only the lines that are
nothing but a locator: a path, or a section name, either with an optional
`¶N`, comma-joined or one per line. A prose line under `## Supported by`
("results.md is still a stub, so nothing else is cited") is reported as
ignored and cites nothing — it does *not* become a citation of the files it
happens to name. HTML comments are stripped before any of this. This is item
38: three rewrites of a correct abstract, every failure about the file's
punctuation rather than about the abstract.

**One flag is one paragraph.** `**[FLAG: ...]**` opening in one paragraph and
closing three paragraphs later is not bold in the built `.docx` — markdown
strong emphasis does not cross a blank line, so pandoc passes the `**` through
as literal asterisks and the flag stops being the unmissable thing it exists
to be. Measured on a real build: six paragraphs carrying `**` and zero bold
runs, including the whole Abstract and the whole Conclusions. If the
explanation is long, the flag is its first line and the rest is ordinary prose
underneath, or an HTML comment.

Then check yourself before handing off:

```bash
python <tools>/prose.py density "<project>" --config "<journal>/writing_config.yml"
python <tools>/prose.py numbers "<project>"
python <tools>/prose.py captions "<project>" --cap <journal cap>
```

## 1c. revise-prose — the pass that owns the prohibitions

**A standalone subagent, and the third blind module.** `stats-check` is blind
so it cannot see the hypothesis it is meant to test independently;
`reviewer-check` is blind so it reads what a reviewer reads. `revise-prose` is
blind so it reads what a **reader** reads.

| given | denied |
|---|---|
| `source_text/*.md` | `plan/outline.md`, `plan/README.md`, the hypothesis |
| the digest's **`## At revision`** section only | `data/`, the stats output |
| layers 0–3 of the brief, as constraints it may not violate | `references.bib`, `plan/captions.md` |
| `writing_config.yml` | this conversation, and every report but the one below |
| **`reports/rN/reading.json`** — the findings | **every density**, and `ai_voice.json` |

The denials are what make it safe to let it rewrite. Given the outline it will
re-argue the science; given the stats output it will add numbers; given the
README it will restore the paper we meant to write. Denied all of it, the only
thing it can act on is how the prose reads.

**It skips every frozen section.** Your prose is not "polished" for you.

**It revises the supplement with the round.** On a whole-paper pass
`supplementary.md` is in the pass's writes, snapshotted and checked like any
section, and every term, label and callout change made in the main text is
made there in the same pass. A supplement carried over untouched is how a
paper comes to contradict itself across its two files.

**Three rules in its brief outrank the style rules.** The author's recorded
decisions in `custom_instructions` are hard constraints, checked in a
`## Decisions Checked` section of the report. A paragraph is classified
before its claim is moved to the front: one that *opens* a new question keeps
reasoning order (writing_rules.md, rule 7). And a coauthor's requests are
worked first, with their inserted sentences frozen (revision.md, *A
colleague's requests come before the engine's own goals*).

#### Run this first — it is why the draft comes back readable

```bash
python <tools>/manuscript.py reading-findings "<project>" --journal "<journal>"
```

**This is the change that makes readability a fix rather than a report**
(`specs/writing-engine-prose.md` §12). It runs `prose.py readability` and
`metaprose` over the freshly drafted sections and parks their findings as
`reports/rN/reading.json`, which 1c is then given. 1c runs **before every
checker**, so an abbreviation used before it was defined, a `this` with no noun
behind it and a paragraph whose point is in its last sentence are gone before
`comprehension-check` ever reads the paper — and what 1d then reports is what a
reader could not follow for reasons no engine can count.

It is stdlib, offline and deterministic: one subprocess, no agent call. Run it
even at `prose_polish: light`, because a fix pass handed a file nobody wrote is
item 80 wearing a different hat.

> **1c is given the findings and not the numbers, and the split is structural.**
> A readability finding names a sentence, so it can be fixed. A density —
> passive share, mean sentence length and its spread — names nothing, so the
> only way to act on one is to hunt the construct and eliminate it, which is
> metric optimization by definition. The densities go to **you**, in the round
> summary. A pass that cannot see the number cannot chase it, which is a
> stronger guarantee than telling it not to.
>
> AI-voice findings are withheld for a different reason: they are pattern
> matches, module 1e adjudicates them, and 3.64 acts on its verdicts. Nothing
> about that changes here.

#### Snapshot, then check — the part that matters

`revise-prose` **may not change what the text claims.** Not as an instruction —
as a checked postcondition:

```bash
python <tools>/learn.py snapshot "<project>" --json      # before the pass
# … the pass rewrites source_text/*.md …
python <tools>/learn.py preserve "<project>" --restore --json
```

| invariant | how it is checked |
|---|---|
| the multiset of numeric tokens is unchanged | `prose.py`'s number extractor, reused |
| the multiset of citekeys is unchanged | the same regex `prose.py citekeys` uses |
| every `**[FLAG: ...]**` block is present **verbatim** | multiset of flag blocks |
| section word count within **±15%** | word count |
| no section rendered empty | trivially |

A failed invariant **rejects the whole pass for that section** and restores the
snapshot. It is a **warning, not a stop**: a section that could not be revised
safely is simply the section the drafter wrote, and the manuscript is no worse
than it was. Rejection is per section — one section that could not be revised
safely does not throw away four that could.

Numeric comparison normalizes presentation, not value: `0.42` and `0.420` are
the same token; `42%` and `0.42` are **not**, because converting units is a
claim about what was measured. The comparator is a multiset, so moving a number
between sentences is permitted and duplicating or dropping one is not.

**This is what buys the wiggle room.** A pass that provably preserves every
number, every citekey and every flag can be allowed to rewrite anything else
however it likes, because nothing it does can change the science. Freedom
inside a checked box, not a rule for every sentence. Also re-run
`prose.py outline` after the pass: every outline line covered before must still
be covered.

#### The style rules are advisory, and that is enforced

**No layer-4 rule may reject the pass.** Only the invariants above can. That is
the operational meaning of "not absolute": the style rules have no teeth over
the text; the science invariants have all of them.

Instruct the pass in those terms, verbatim:

> These are the things a good reader notices, not a specification to satisfy.
> Where a rule would make a sentence worse, leave the sentence and say why.
> A section you improve in three places and leave alone in ten is a success.
> Sentence length is a distribution, not a ceiling — a 35-word sentence after
> three short ones is good writing. Do not flatten the prose into short
> declaratives; that trades one monotony for another.

#### `prose_polish` — how much it may do

```yaml
prose_polish: standard      # off | light | standard | heavy
```

| value | behaviour |
|---|---|
| `off` | the module does not run, and `plan` takes it out of the list |
| `light` | openers and paragraph-lead sentences only — highest value, lowest risk |
| `standard` | full pass, sentence and paragraph level (default) |
| `heavy` | may also split/merge paragraphs and reorder sentences within one |

`heavy` is the only level permitted to change paragraph boundaries; outline
coverage is what keeps that honest.

#### The map it works from

```bash
python <tools>/prose.py voice "<project>" --per-paragraph --json
```

**An instrument, never a gate — it always exits 0.** No threshold, no budget,
no pass/fail. It reports sentence length mean and **standard deviation** (a fix
that lowers the mean while collapsing the sd has made the prose worse), the
five longest sentences, opening-sentence length per paragraph, **lead-sentence
rank**, citekeys per sentence and the count with ≥2, numbers per sentence,
hedge and transition density, nominalizations and passive fraction.

A gate on a prose measurement recreates the original failure one layer up: the
drafter optimizes the metric, and mean sentence length is not the thing anyone
wants.

#### Two reports, and the second lists are the point

`reports/rN/prose_report.md`:

- **Changed** — before, after, and the one-line reason, **in the exact
  shape `agent-brief` prints** (`- pass:` / `- rule:` / `- before:` / `- after:`
  / `- why:`, one block per change). That shape is a contract, not a style:
  `learn.py` parses it next round to find out whether the user reverted
  anything an automatic pass did, and a revert is a **waiver** against the
  rule that drove it — three of them demote the rule. A report written as
  prose reads back as a round in which nothing was changed and nothing
  reverted, which is worse than no report, so neither `before` nor `after`
  may be summarized.
- **Left alone** — sentences a rule fired on that it judged better as written,
  with the reason.
- **Take-home** and **Supported by** — the abstract's one claim and the
  sections behind it, written by module 1b into this same file and read back
  by `prose.py claim` (item 43).

`reports/rN/learned_rules.md`, the layer-3 counterpart:

- **Applied** — rule id, where, and the sentence it shaped.
- **Waived** — rule id, where, and the one-line reason.

**Both lists in both files are required.** An empty "left alone" list on a long
section is suspect and `final-check` says so. A section with six active rules
in scope, all applied and none waived, is the signature of a drafter treating
the registry as a checklist rather than exercising judgment, and `final-check`
says that too.

#### A waiver is negative evidence

```bash
python <tools>/learn.py waive LR-014 --because "the technique IS the \
contribution here" --project "<name>" --round r2 --section title_abstract
```

**Record every waiver.** A learned rule is what your past edits taught, not a
specification — apply it where it makes the sentence better, waive it where it
does not, and give the reason. Waiving a well-evidenced rule needs a better
reason than waiving a thin one, but neither is forbidden, and the brief tells
you which is which: each layer-3 entry carries its observation count, project
count, last-reinforced date and waiver count.

**Three independent waivers demote `active` → `candidate`**, and a candidate is
applied to nothing. Independence is a section in a round, so three waivers in
one section of one round are one — otherwise a single unlucky section demotes a
good rule. A demoted rule keeps all three reasons verbatim in
`demoted_because`, keeps its evidence, and is one `learn.py promote <id>
--because "…"` away from coming back.

The mechanism cannot tell *"this rule makes this sentence worse"* from
*"applying this rule is work"*, and there is no automatic check that could —
judging whether a waiver reason is honest means reading the sentence, which is
the user's job. That is the entire reason `learned_rules.md` exists.

## 1d. comprehension-check — the module that reads the paper

Every other check in this pipeline asks a **conformance** question: is this
sentence well formed, is the density inside its budget, does the paragraph
lead with its main idea, do the citekeys resolve. **Every one of them can be
satisfied by prose no reader can follow.** Conformance is not comprehension,
and no amount of the first produces the second.

It is not `revise-prose`'s job for a second reason: **`revise-prose` is the
party that produced the prose.** Asking it whether its own output is
followable is asking the author.

**This agent's context contains only:** the manuscript in reading order —
`title_abstract.md`, then the body sections in document order, then
`live_captions.md`, because a reader has the figures — and one sentence
naming the reader it is being asked to be, from `writing_config.yml:audience`.
It is **denied** the outline, `plan/README.md`, the literature landscape, the
hypothesis, `data/`, `data/corpus/`, `drafts/rough_draft.md` and every report.

**The denials are the module.** A reader who has read the plan can follow
anything, because they already know what the paper is going to say — and
nothing visibly fails when that isolation breaks. The report still looks
thoughtful; it is simply worthless, and it is trusted because it exists.

It writes `reports/rN/comprehension_report.md` in four parts:

**A. The restatement** — four sentences written *first* and without looking
for faults: the question, what was done, what was found, the one thing to
remember. **You compare them**, never the module: against `prose.py claim`'s
declared take-home and against the outline's claims, because you are the only
party holding both and the reader must never see either. *A divergence there
is the most valuable finding this pipeline can produce: the paper does not say
what it thinks it says.* Surface it in the round summary as one line.

> **If the reader's "one thing to remember" is a list, the conclusion did not
> land one.** Say so in the round summary, in the same line that reports a
> part-A divergence. A conclusion that produces three things to remember has
> the same defect as a paper that does not say what it thinks it says, one
> scale down.

That is a test **you** run, not the module — it has no idea a conclusion was
supposed to leave one thing behind, and telling it so would tell it what the
paper argues. It is judgment rather than a measurement, and
`specs/flow-and-conclusion-2026-09-19.md` §3.3 says so in as many words: no
number supports it, and what would measure it is a round where part A's fourth
sentence is read back against the conclusion it came from.

**B. Where the reader stopped** — each with a location and one reason from a
closed set: `term_undefined` · `referent_unclear` · `order` · `overloaded` ·
`connection` · `jargon_density` · `unexplained_step` · `contradiction` ·
**`no_argument`** — a passage that reports what others found without saying
what follows from it. Closed on purpose: a free-text reason cannot be counted
across rounds, and the point of this list is that it shrinks.

**`no_argument` stopped being review-only on 2026-09-20.** It used to read
*"and on a review also"*, and a research paper's Discussion can become the
same annotated bibliography its own sections wrote. This does not widen the
fence the closed set is — it removes a `paper_kind` condition on one member
that was already in it.

**C. The bridges** — per section boundary and per paragraph transition, did
this follow from the last one.

> **The conclusion is a bridge too, and it is the one that carries the whole
> paper.** Say whether it told you something the sections had not already told
> you separately, or whether it walked you back through them.

That sentence is the whole of what this toolkit does about *"Big issues with
the conclusion — doesn't do much to wrap it up. Don't just recap."* The
obvious fix — a `prose.py` check that measures a conclusion's overlap with
the sections — was prototyped on 2026-09-19 and **fires on neither draft, in
neither direction**; `specs/flow-and-conclusion-2026-09-19.md` §3 carries the
measurement and refuses it. The difference between a recap and a synthesis is
not in the words, so it belongs to the module that reads rather than to the
one that counts.

**D. What it did not need explained** — and this part is not optional. A
reader that reports only difficulties will be read as saying the paper is too
hard, every single time.

#### The dial, and what `standard` means at r1

```yaml
comprehension_check: standard      # off | light | standard
comprehension_fix:   auto          # auto | ask | off
```

`light` is `prose.py readability` only — **no agent call** — and `standard`
resolves to it at r1, because r1's prose is going to change anyway and the
deterministic half costs nothing. From r2 on, and in the `submission` preset,
`standard` is the full blind read. `off` takes the module out of the plan
**and the plan says it was skipped**: a plan that lists a check tells the user
it ran, so one that drops a check has to say so.

There is no `heavy`. There is no heavier version of reading the paper, and
inventing one would produce an adversarial reader — which is
`reviewer-check --heavy`, and already exists.

#### The countable half

```bash
python <tools>/prose.py readability "<project>" --config "<journal>/writing_config.yml" --json
```

Tail rank, the given–new bridge between paragraphs, bare demonstrative
openers, abbreviation load, subject–verb separation, topic return. It
**always exits 0** and every finding is advisory.

**The abbreviation findings are off by default, and that is measured.**
Written the obvious way the check fires 4.7 times per real published abstract
in this field, on 21 of 24 — and the exemption the obvious design reaches for,
elements and SI units, removes *none* of them. What actually fires is `CH3`,
`COOH`, `C6H4OH`, `NH2`: chemical formulae, which the engine now parses rather
than lists. `--abbreviations` turns the findings on once somebody has
calibrated them; `known_abbreviations` in `writing_config.yml` is where the
field's own nouns go.

#### The register is the field's, and the module is told so

Measured on 192 sentences of published prose in the user's own field: **mean
22.7 words, sd 15.7, 40% over 25 words, 29% passive.** A readability standard
of fifteen to twenty words and an active voice would flag two fifths of
peer-reviewed work here and be wrong every time. So the brief carries four
floors as prohibitions: it may not ask for a technical term to be *removed*
(only defined at first use, or used consistently — a synonym reads as a second
referent); it may not ask for a number, a citation, a real hedge or a
qualification to be dropped; it may not ask for shorter sentences *as such*;
and there is no reading level, ever. The yardstick is `audience`, and the
report says which reader it used.

#### The dial decides, and the round does not enter into it

**The dial is the ceiling, and it is the whole rule.** Both rewrite passes —
the confused-reader fix here and the AI-sounding fix at 3.64 — resolve through
`fix_mode()`, which does not read the round number at all:

| dial | What happens |
|---|---|
| `auto` | **runs, every round.** *"I think we make the checkers run on the first draft because the goal is to make it so that it doesn't NEED additional iterations."* |
| `ask` | **always offered**, per pass, in the round block, with what it may overwrite named |
| `off` | never runs, and the plan says the pass is not in this round |

**This page said "the fix runs at r1 and is offered from r2" until
2026-09-20, and the code stopped following that rule on 2026-09-17.**
`fix_mode()`'s own body reads `del revision` — the round is discarded — and
`specs/writing-engine-prose.md` §12.2 is explicit that
`specs/edit-authority.md` §5's r1-runs/r2-offers rule was **superseded**,
because the per-sentence coauthor guard that same spec built protects what
the round rule was protecting, and protects it more precisely. An operator
following the old sentence would either announce an offer that never comes or
read the pass having run as the engine overstepping — and the round summary
would describe a round that did not happen, which is exactly what §12.4 names:
*"a plan that lists a check tells the user it ran."*

The history below is kept because it is what stops this being re-litigated,
and there are now two corrections in it rather than one.

What was here before *that* was exactly backwards, and measured so: `standard`
resolved down to `light` at r1 and the fix was off at `light`, so the
automatic rewrite stayed **out** of the draft that was still the engine's and
ran **on** the draft that had the user's and their coauthors' work in it. The
r1 downgrade was a *cost* decision — do not buy an expensive blind read of
prose that is about to change — and it had quietly become a *policy* about
when the engine may rewrite a person's sentences. Those two are separate now:
`comprehension_check`'s level keeps its cost logic and loses its authority
over the fix.

**So at `light`, write the report anyway.** `prose.py readability` produced
real findings and nothing collected them; write
`reports/rN/comprehension_report.md` from them, saying in its first line that
this is the deterministic half and no blind read ran. Otherwise the plan
offers a pass over a report nobody wrote, which is the failure this whole
toolkit exists to catch.

`off` still means off, and **the plan names a pass that did not run** — "it
was offered and you declined" and "it never ran" must not render the same way.

When there are findings and the round says run, run the bounded second pass
over the sections it named and no others:

```bash
python <tools>/manuscript.py agent-brief "<project>" --module "revise-prose --from-comprehension" \
    --journal "<journal>" --sections discussion --json
```

Same 1c, same preservation invariant, one extra file in its given list.
Then say what it did — the sections rewritten, the finding counts acted on,
and the `Left alone` count:

> comprehension-check stopped four times in the discussion —
> `referent_unclear` ×3, `overloaded` ×1. The second pass rewrote the
> discussion, acted on 3 and left 1 alone with a reason. Before/after per
> sentence is in `prose_report.md`.

**Why a comprehension finding is the easy one to act on.** It is close to
self-validating: a reader reporting *I stopped here* is itself the evidence,
and there is nothing to adjudicate. An AI-voice finding is a pattern match, so
1e adjudicates it before 3.64 sees it. That asymmetry is about **what has to
be judged first**, and it is not what decides whether the pass runs — the
round is.

**The ratchet worry is answered by measurement, not by asking.** The worry
was directional — that fixes only ever make prose simpler. The four register
floors above already forbid the four moves that would do it, and they bind
the automatic pass exactly as they bound the offered one. What is added is
the evidence that they held: `final-check` reports the before/after `voice`
diff for every section this pass touched.

**Every other guard is unchanged**: the numeric and citekey multisets, the
verbatim `**[FLAG]**` blocks, word count within ±15%, the snapshot restore,
**frozen sections skipped entirely**, the mandatory `## Left alone` list
with a reason per declined finding, and the blind 1c's denials.

`ask` is the setting for a user who wants to be asked at r1 as well: offer the
pass in one line and let `no` end it. `off` takes the pass out of the round
**and the plan says it was skipped**, under the same rule the `off` above
follows.

## 1e. quality-check — the audit, and the only thing that may judge a finding

Order **3.62**. The stretch between reading the paper and checking its
attribution now runs: 3.5 read it → 3.6 act on that reading → **3.62 audit
the voice and the ten rules** → 3.64 act on that audit → 3.7 attribution.

```bash
python <tools>/manuscript.py ai-voice-findings "<project>" --journal "<journal>"
python <tools>/manuscript.py agent-brief "<project>" --module quality-check \
    --journal "<journal>" --json
```

The first command runs `prose.py ai_voice` and parks its payload as
`reports/rN/ai_voice.json`. Run it first — 1e's whole first job is
adjudicating what is in that file.

#### It is not blind, and that runs opposite to 1d on purpose

`comprehension-check` is denied the outline because a reader who knows the
argument can follow prose nobody else can. Here the reverse holds: **you
cannot tell significance inflation from a significant finding without knowing
what was found.** A blind agent flags every strong claim in the paper. So 1e
gets `source_text`, the outline, the claims ledger and `analysis` — a *fresh*
context rather than a blind one, because **the agent that wrote the prose is
the worst available judge of whether the prose sounds like an agent wrote
it.**

#### Three jobs, and every finding gets a verdict

**A. The 15 mechanical AI-voice findings**, adjudicated. Each is a pattern
match — *this sentence contains "serves as"* is an observation about
characters, not a defect.

**B. The 6 judgment patterns** no regex reaches: significance inflation,
notability name-dropping, superficial *-ing* analyses, promotional language,
formulaic challenge-and-triumph, and epistemic uniformity. Each finding names
the sentence **and what it would assert if the inflation were removed** — a
finding that cannot name the deflated claim is not a finding. #24 has the
highest ceiling of the 25 here, because the engine **already records each
claim's evidence tier**, so the claim verb is graded against a recorded fact
rather than a prose metric.

**C. The ten rules of layer 2.** Seven are auditable against drafted text;
rules 5, 9 and 10 are not, and saying so is part of the design rather than an
omission — 5 is 1b's under its own blindness, 9 is a fact about how the
drafter spent its attention that no reader of the output can recover, and 10
is the learning loop. Rule 3 is the one worth naming: **C—C—C at both ends**,
and the closing end is the half `prose.py` cannot see, because tail rank
measures aboutness and not whether the last sentence concludes.

**Rule 7 is audited as the point, never the shape.** A results paragraph in
plain declarative prose that opens on its finding is **not** a rule-7 finding.

| Verdict | Means | Goes to |
|---|---|---|
| `apply` | the pattern fired and the fix improves this sentence | 3.64's brief |
| `leave` | the pattern fired and the prose is right anyway | the user's report only |
| `ask` | it turns on a claim's strength or an author's intent | a `**[FLAG: ...]**` |

**A run that verdicts everything `apply` is a defect, not a clean paper.**

#### 3.64 — revise-prose --from-quality, and what it may not see

```bash
python <tools>/manuscript.py agent-brief "<project>" --module "revise-prose --from-quality" \
    --journal "<journal>" --sections discussion --json
```

**It follows the same rule 3.6 does**: `auto` runs every round, `ask` always
offers, `off` never runs, through the one resolver — and it has a dial of its
own, `quality_fix`, which became real on 2026-09-17. This paragraph said
there was no such dial and that the question was "answered once for both
passes"; a declared dial nothing reads is a defect
(`specs/writing-engine-prose.md` §9.1), so it is read on the same line it was
added. What 1e's adjudication buys is
that **no finding reaches this pass unjudged**, which is a separate guarantee
and unchanged.

**The pass is denied `ai_voice.json`.** That file carries the four
per-section **densities** — em dashes, boldface, three-item lists, filler
openers — and a density names no sentence. The only way to act on one is to
hunt the construct and eliminate it, which is metric optimization by
definition, and a pass told to reduce em-dash density produces prose contorted
to avoid em dashes. **A pass that cannot see the number cannot optimize it**,
which is a stronger guarantee than instructing it not to — the same
reasoning that gives `stats-check` an explicit allow-list. It is also denied
the `leave` verdicts, which it may not overturn.

**And denied `quality_densities.md`, which is the same denial by its other
door.** 1e is told to report those four numbers to the user, and for a long
time its only write target was `quality_report.md` — the one file 3.64 IS
given — so the densities arrived in the rewrite pass's reading anyway, every
round, and the structural guarantee above was a request nobody enforced. The
densities have a file of their own now; it is the user's and no pass reads it.
`assemble` greps the produced `quality_report.md` for a density table
afterwards and files item 127 if it finds one, because a denial that rests on
another component remembering it is not a denial.

Everything that makes 1c safe is unchanged: the numeric and citekey multisets,
the verbatim flags, the snapshot restore, the four register floors, and a
mandatory `## Left alone` list — which here means the narrower and more
interesting claim *“1e said apply and I still disagreed”*.

**Both reports reach the user either way**: what 3.64 acted on, what it left
alone and why, and — in `quality_densities.md` — the densities nothing acted
on.

**The count of findings in Part A is the engine's, not the brief's.** 1e's
prompt carries the number `ai_voice.json` actually holds, computed before the
agent is spawned. It used to state a fixed 15, which is the count of mechanical
RULES; a run handed a file holding 7 spent a numbered paragraph of its report
asking the engine which of the two was wrong. **A rule that fired on nothing is
not a missing finding**, and a number about an input belongs to whoever can
count it.

## Hook the reader

The user's standing instruction, and it sits with the number-vomit rule as a
house rule of this toolkit:

> **Write it to be read, not to be filed.** A reader gives the first sentence
> of every section about four seconds. Earn the fifth. Think of the opening of
> a good science news story — it tells you what happened and why anyone should
> care before it tells you how it was measured.

What that means in practice, and none of it is licence to overstate:

- **Open on the stake, not on the furniture.** "Metal-organic frameworks have
  attracted widespread attention due to their tunability" is the furniture —
  it is true of a thousand papers and tells the reader nothing about this one.
  "The framework decides where the metal ends up" is the stake: it is this
  paper's reason to exist, it is one idea, and it can be read aloud once.
  Whatever else the paper is about goes in the *next* sentence, not folded
  into this one.
- **One idea per sentence, and it has to survive being read aloud.** This has
  the same standing as the three rules above it, because the sentence a
  reader gives up on costs the four seconds the whole rule was written to
  win. A second idea gets a second sentence. Do not repeat a noun three times
  to hold a clause together. If you cannot read it aloud in one breath, it is
  two sentences. About twenty words is the ceiling for an opener.

  The stake is what the sentence is *about*, not how much is packed into it.
  Every other bullet here pushes toward loading more in, and this is the
  counterweight: a sentence can be true, concrete, staked and still
  unreadable, and none of the engine's checks can see that. `voice`, the
  density check and the flag conventions all pass a tongue-twister. You are
  the only thing that catches it.

  This bullet exists because the exemplar above used to be a longer sentence
  about frameworks and metal particles, a drafter bolted the paper's subject
  onto the front of it, and the result — 26 words, "metal" three times,
  "framework" twice — was the sentence the user could not read. The rule
  worked exactly as written; the example was the defect. It is not quoted
  here, deliberately: quoting it is how it comes back (system-changes
  item 20).
- **Concrete beats abstract, every time.** A number with a consequence, a
  named material, a thing that visibly happened. "Five attempts to grow a
  replicate failed" is more interesting than "growth was inconsistent", and it
  is also more accurate.
- **Tension is what carries a paragraph.** Something was expected and
  something else happened; two explanations disagree; the thing that works in
  bulk does not work in a film. Say it as a tension rather than smoothing it
  into a list of observations.
- **The last sentence of a section sets up the next one.** A section that ends
  by summarising itself has closed a door the reader was walking through.
- **Short sentences, active voice, first person where the group did the
  thing.** `voice` in `writing_config.yml` is already set to
  `active_first_person` for this reason. Passive, impersonal prose does not
  read as more objective; it reads as harder, and the digest says so.

**Three things this rule never buys**, and a draft that trades any of them for
interest is worse than a dull one:

- **It is not a licence to overstate.** "Dramatic", "unprecedented",
  "remarkable" and "for the first time" are the exact words *Langmuir*'s own
  checklist bans from a title and an editor discounts everywhere else. Interest
  comes from the specific thing that happened, never from an adjective in front
  of it.
- **It does not move a claim ahead of its evidence.** The evidence bracket
  still governs. A hook for a result that does not exist yet is a
  `**[FLAG: data]**`, the same as any other.
- **It does not touch the Experimental Section.** Methods are rendered from
  recorded values and are meant to be dull and complete. Reproducibility is
  the interest there.

Where a journal's own house style pulls against this — and ACS's does, a
little — the journal wins on vocabulary and this rule wins on structure. You
can always choose which sentence goes first.

## The number-vomit rule

The user's stated failure mode from previous attempts: AI-written results read
as a list of numbers with no meaning attached.

> **Every number in the running text is preceded by what it means, in words, and
> followed by why it matters — in the same sentence or the next. A number that
> appears without an interpretation moves to a table or a caption.**

Not this:

> The mean symmetry index was 0.82 ± 0.05 versus 0.71 ± 0.05 (p = 0.003).

This:

> Arrays from the thermophile were markedly more symmetric than those from the
> mesophile — a 15% higher symmetry index (0.82 ± 0.05 vs. 0.71 ± 0.05,
> p = 0.003) — which is the difference between a lattice that resolves at 4 Å
> and one that does not.

`prose.py density` counts two currencies, because not all numbers cost the
reader the same: **inline numbers** in the sentence flow are expensive, and a
**parenthetical cluster** is one object to the eye however many values it holds.
Budgets are per 100 words and scale up with paragraph length. Cross-references,
citations, dates, nomenclature (`16S rRNA`, `Cas9`), instrument models and
catalogue numbers are excluded — without that the check fires on every mention
of 16S rRNA and becomes noise.

**The checker reports; it does not rewrite.** Over budget is a finding with its
counts and a suggested remedy, and the user decides. Record an accepted one in
`writing_config.yml:number_density_waivers` so the same paragraph is not
re-flagged every round.

**The budget is per section, and it is learnable.** One number applied to five
sections that are not alike was the clearest case here of a prohibition being
too restrictive in one place and too loose in another at the same time: a
global `low` put 8 of 11 findings on Methods — a section whose job is to be
dull and complete — while the abstract sat at nearly 4× the same budget in the
same list and nobody noticed. `number_density` is now a mapping, the defaults
were measured (`methods: high`, unenforced; `results: medium`;
`introduction`/`discussion: low`; `title_abstract` a whole-section count of 6,
because a rate over 250 words is noise and the reader meets all of an
abstract's numbers at once), and the scalar spelling still means one level for
every section.

Each section's budget resolves through four steps, and `density --json`
reports which one won as `budget_source`: this project's config, then an
`active` learned budget for `(research_type, section)`, then one for a parent
research type, then the built-in default. The config beats the registry because
a value the user typed into *this* project is a statement about *this* paper.

**Two rules survive any density setting, any dial and any layer**: no paragraph
is more than one-third numeric tokens, and every inline number still needs its
interpretation. Those are layer 0. `learn.py` refuses to record either as a
`kind: numeric` budget, and that refusal is a test rather than a comment — a
fraction cap looks exactly like a budget, and it is the one place the learning
loop would quietly eat an invariant.

**Say what to open a results paragraph with, not only what not to.** Layer 2
rule 7 is the generative form: *a results paragraph opens on what was found and
what it means, then gives the data and the logic that establish it.* That keeps
the older rule's core — "open with the finding, not the measurement" — which
was the half worth keeping, and supplies the half it lacked, which is what to
open with instead of only what not to.

*The question-and-answer form — pose the question, give the data, answer it —
is one shape this can take, offered as a pattern and not required.* An earlier
form of rule 7 mandated it for every results paragraph. That went one step too
far: a section written entirely in that form reads as a catechism, and it is
not what published work in this field does.

## The user's own draft, and the two drafting modes

`drafts/rough_draft.md` is optional and an empty one is a normal project -
never report it as missing and never nag about it. What it is for: the user
writes the paper there, and **anything under a heading becomes the highest
source of truth for that section**.

That turns on one question - *is there prose for this section* - and the answer
is the mode (spec 20.1, 21.3):

| mode | when | what draft-sections does |
|---|---|---|
| `compose` | the section is empty: r1 with no rough-draft block, a wiped section, or `--redraft` | write the paragraph from the outline line, the recorded facts and the captions |
| `edit` | prose exists: a rough-draft block, **or any round after the one that wrote it** | fix mechanics, insert citations, insert `**[FLAG: ...]**`, apply what the reports asked for, and change nothing else |

Derived per section, never configured, and the plan block prints it. It is one
mechanism, not a rough-draft feature: an agent told "draft the results" and
handed the existing results as one more source writes its own results section
that borrows from them, whoever wrote the original.

**A number the user wrote that nothing measured is KEPT AND FLAGGED.** Not
deleted - that throws away the author's knowledge of their own experiment - and
not asserted - that is the failure the whole engine exists to prevent. The flag
names `drafts/rough_draft.md` as where the number came from, and it blocks
submission until somebody resolves it. A rough draft is *supposed* to contain
numbers the statistics have not caught up with.

**Two modules never read it**: `stats-check` and the blind `abstract` agent.
`evidence-check`, `revise-prose`, `reviewer-check` and `learn-from-edits` are
denied it too, and `revise-prose` additionally **skips every edit-mode section
and reports the skip**.

**A rough-draft abstract stands and the blind agent does not run** - not "runs
and is overridden", which is an agent call spent on output nobody asked for.
The plan block says so and the estimate is one lower.

`source_weights: {rough_draft: ...}` takes `primary` (the default) or `ignore`,
and nothing else. `low` and `normal` are refused with the reason: a rough draft
that is *sort of* consulted is the worst of both modes.

## The retention invariant - run it after drafting

"Just makes edits" is a **checked postcondition**, not an instruction in a
prompt (spec 20.3). Run it after `draft-sections` and before `assemble`:

```bash
python <tools>/manuscript.py retention "<project>" --journal X --restore
```

Per edit-mode section, held against its baseline - the rough-draft block at r1,
`obsolete/drafts/rN/` at every later round:

| invariant | on failure |
|---|---|
| every sentence of the baseline is present, or listed under **Removed** with a reason | reject |
| the baseline's numbers are a **subset** of the section's | reject |
| citekeys and `**[FLAG: ...]**` are **additive only** | reject |
| growth past +25% of the baseline | reported, not a gate |

`reports/rN/retention_report.md` carries **Kept** / **Edited** / **Removed**,
so the discretion is auditable and the user can disagree with it.

**A rejection is not a failure of the round.** The fallback is the baseline
written through verbatim with the flags appended, and that is a usable section
- the user's own words, unedited. Say that when you report it, rather than
presenting it as an error.
